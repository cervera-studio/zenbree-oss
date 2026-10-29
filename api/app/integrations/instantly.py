"""Instantly (#929): connect a project's workspace, preview and confirm sending qualified people to a campaign.

One workspace for each project, one API key (encrypted, never returned). Enrolling is never silent: the user previews
the rows, confirms, and when the campaign is running in Instantly (it may start sending to the new people under its
own settings) acknowledges that too. Zenbree never activates, pauses or edits a campaign, and an enrollment is not an
email sent: it writes no ``outreach_campaign_sends`` row, no credit and no CRM activity.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Response

from app.admin.settings import is_disabled
from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors import err, is_err
from app.core.errors_http import api_error, unwrap_or_raise
from app.integrations import (
    instantly_client,
    instantly_enroll,
    instantly_publish,
    instantly_store,
    instantly_task,
    token_crypto,
)
from app.integrations.instantly_fields import check
from app.integrations.instantly_models import (
    CampaignsOut,
    ConfirmIn,
    EnrollIn,
    EnrollmentOut,
    InstantlyConnectIn,
    InstantlyStatus,
    PreviewOut,
)
from app.integrations.instantly_sync import outcomes, subscription
from app.outreach import stop_outbox
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/integrations/instantly', tags=['Instantly'])
logger = structlog.get_logger()
Project = Depends(current_project)


def _enabled() -> None:
    if is_disabled('vendor_disabled:instantly'):
        unwrap_or_raise(err('INSTANTLY.DISABLED', detail='Instantly is switched off for now'))


def _status(project_id: str, user: CurrentUser) -> dict:
    with get_conn() as conn:
        connection = instantly_store.row(conn, project_id)
        sync = outcomes.coverage(conn, project_id)
    return {
        **instantly_store.visible(connection),
        'configured': token_crypto.is_configured(),
        'can_manage': instantly_store.may_manage(connection, user),
        'sync': asdict(sync) if sync else None,
    }


@router.get('', response_model=InstantlyStatus)
async def status(user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    return await asyncio.to_thread(_status, project_id, user)


@router.post('/connect', response_model=InstantlyStatus)
async def connect(
    body: InstantlyConnectIn, user: CurrentUser = Depends(current_user), project_id: str = Project
) -> dict:
    """Checks the key against Instantly (workspace and campaigns readable), then stores it encrypted. The failed stops
    of the workspace are attempted again with the new key."""
    _enabled()
    if not token_crypto.is_configured():
        unwrap_or_raise(err('INSTANTLY.NOT_CONFIGURED', detail='key encryption is not configured'))

    def existing() -> dict | None:
        with get_conn() as conn:
            return instantly_store.row(conn, project_id)

    if not instantly_store.may_manage(await asyncio.to_thread(existing), user):
        raise api_error(403, 'INSTANTLY.NOT_ALLOWED', 'only who connected Instantly, or an admin, can replace it')
    key = body.api_key.get_secret_value()
    workspace = unwrap_or_raise(await instantly_store.validated(key))

    def save() -> None:
        with get_conn() as conn:
            stored = instantly_store.store(
                conn,
                user=user,
                project_id=project_id,
                name=body.name,
                key=key,
                workspace=workspace,
            )
            if not is_err(stored) and (connection := instantly_store.row(conn, project_id)):
                stop_outbox.rearm_connection(conn, str(connection['id']), workspace.id)
        unwrap_or_raise(stored)

    await asyncio.to_thread(save)
    logger.info('instantly.connected', project_id=project_id, user_id=user.id)
    return await asyncio.to_thread(_status, project_id, user)


@router.delete('', response_model=InstantlyStatus)
async def disconnect(user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    """Drops the key and keeps the record of the leads. A batch that is still running stops before its next chunk, and
    a pending stop fails: nothing can carry it out until a reconnect."""

    def drop() -> subscription.Leftover | None:
        with get_conn() as conn:
            connection = instantly_store.row(conn, project_id)
            if not instantly_store.may_manage(connection, user):
                raise api_error(
                    403, 'INSTANTLY.NOT_ALLOWED', 'only who connected Instantly, or an admin, can disconnect it'
                )
            key = instantly_store.api_key(connection)
            leftover = subscription.retire_all(conn, project_id, None if is_err(key) else key.value)
            instantly_store.disconnect(conn, project_id)
            if connection:
                stop_outbox.fail_connection(conn, str(connection['id']), 'INSTANTLY.DISCONNECTED')
            live = conn.execute(
                "SELECT id FROM instantly_enrollments WHERE project_id = %s AND status IN ('queued', 'running')",
                (project_id,),
            ).fetchall()
            for batch in live:
                instantly_enroll.request_cancel(conn, project_id, str(batch['id']))
        return leftover

    await subscription.delete_leftover(await asyncio.to_thread(drop))
    return await asyncio.to_thread(_status, project_id, user)


async def _key(project_id: str) -> tuple[dict | None, str]:
    """The connection and its key: a refusal of that key is written against that connection alone."""

    def read() -> dict | None:
        with get_conn() as conn:
            return instantly_store.row(conn, project_id)

    connection = await asyncio.to_thread(read)
    return connection, unwrap_or_raise(instantly_store.api_key(connection))


async def _refusal(connection: dict | None, result):
    """Passes an Ok through; an Err that says Instantly refused the connection's key marks it, then raises."""
    if connection and is_err(result) and result.error.code == 'INSTANTLY.INVALID_KEY':

        def mark() -> None:
            with get_conn() as conn:
                stop_outbox.refuse_key(conn, str(connection['id']), connection['connected_at'], 'INSTANTLY.INVALID_KEY')

        await asyncio.to_thread(mark)
    return unwrap_or_raise(result)


def _campaign_out(c: instantly_client.Campaign) -> dict:
    return {
        'id': c.id,
        'name': c.name,
        'status': c.status,
        'status_label': instantly_client.CAMPAIGN_STATUS.get(c.status, 'unknown'),
        'may_send': c.may_send,
    }


@router.get('/campaigns', response_model=CampaignsOut)
async def campaigns(user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    _enabled()
    connection, key = await _key(project_id)
    found = await _refusal(connection, await instantly_client.list_campaigns(key))
    return {'campaigns': [_campaign_out(c) for c in found]}


async def _prepared(body: EnrollIn, user: CurrentUser, project_id: str):
    """The key, the campaign as it is now and the selected rows; raises when the list, the mapping or the campaign
    is not usable."""
    _enabled()
    connection, key = await _key(project_id)

    def load() -> tuple[set[str] | None, list[dict]]:
        with get_conn() as conn:
            columns = instantly_enroll.list_columns(conn, project_id, str(body.list_id))
            rows = (
                instantly_enroll.load_rows(conn, str(body.list_id), [str(i) for i in body.row_ids])
                if columns is not None
                else []
            )
        return columns, rows

    columns, rows = await asyncio.to_thread(load)
    if columns is None:
        raise api_error(404, 'LIST.NOT_FOUND', 'a list of people of this project was not found')
    if problem := check(body.mapping, columns):
        raise api_error(422, 'INSTANTLY.INVALID_MAPPING', problem)
    if not rows:
        unwrap_or_raise(err('INSTANTLY.NO_VALID_LEADS', detail='none of the rows is in this list'))
    campaign = await _refusal(connection, await instantly_client.get_campaign(key, str(body.campaign_id)))
    return campaign, rows


@router.post('/preview', response_model=PreviewOut)
async def preview(body: EnrollIn, user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    """What confirming now would do. No side effect: nothing is stored and Instantly is only read."""
    campaign, rows = await _prepared(body, user, project_id)

    def judge() -> dict:
        with get_conn() as conn:
            return instantly_enroll.preview(
                conn, owner_id=user.id, project_id=project_id, rows=rows, mapping=body.mapping, campaign=campaign
            )

    return await asyncio.to_thread(judge)


def _enrollment(project_id: str, enrollment_id) -> dict:
    with get_conn() as conn:
        found = instantly_enroll.view(conn, project_id, str(enrollment_id))
    if not found:
        raise api_error(404, 'INSTANTLY.ENROLLMENT_NOT_FOUND', 'enrollment not found')
    return found


def _enqueue(project_id: str, enrollment_id: str) -> None:
    """Queues the run. A broker that is down leaves the batch `failed` with its people `pending`: Retry resumes it."""
    try:
        instantly_task.enqueue(enrollment_id)
    except Exception as exc:
        logger.error('instantly.enqueue_failed', enrollment_id=enrollment_id, error_type=type(exc).__name__)
        with get_conn() as conn:
            conn.execute(
                "UPDATE instantly_enrollments SET status = 'failed', error_code = 'INSTANTLY.QUEUE_UNAVAILABLE',"
                " finished_at = now() WHERE id = %s AND status = 'queued'",
                (enrollment_id,),
            )
        raise api_error(503, 'INSTANTLY.QUEUE_UNAVAILABLE', 'the batch could not be queued: retry it') from exc


@router.post('/enrollments', status_code=202, response_model=EnrollmentOut)
async def enroll(body: ConfirmIn, user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    """Records the selected people (``pending`` or ``skipped`` with a reason) and queues the batch. Every person is
    judged again before their chunk goes."""
    if not body.confirm:
        raise api_error(409, 'INSTANTLY.CONFIRM_REQUIRED', 'confirm the enrollment')
    campaign, rows = await _prepared(body, user, project_id)
    if campaign.may_send and not body.acknowledge_sending:
        raise api_error(409, 'INSTANTLY.SENDING_CONFIRM_REQUIRED', 'this campaign is running and may start sending')

    def create() -> str:
        with get_conn() as conn:
            connection = instantly_store.row(conn, project_id)
            if connection is None:
                raise api_error(409, 'INSTANTLY.NOT_CONNECTED', 'connect Instantly first')
            made = instantly_enroll.create(
                conn,
                owner_id=user.id,
                org_id=user.org_id,
                project_id=project_id,
                connection=connection,
                campaign=campaign,
                list_id=str(body.list_id),
                rows=rows,
                mapping=body.mapping,
            )
        return unwrap_or_raise(made)

    enrollment_id = await asyncio.to_thread(create)
    await asyncio.to_thread(_enqueue, project_id, enrollment_id)
    return await asyncio.to_thread(_enrollment, project_id, enrollment_id)


@router.get('/enrollments/latest', response_model=EnrollmentOut | None)
async def latest_enrollment(user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict | None:
    """The newest batch of the project that still needs a decision (live, failed with people left, or in doubt), or
    null. It is how a user reaches Cancel and Retry once the dialog that made the batch is closed."""

    def read() -> dict | None:
        with get_conn() as conn:
            return instantly_enroll.latest(conn, project_id)

    return await asyncio.to_thread(read)


@router.get('/enrollments/{enrollment_id}', response_model=EnrollmentOut)
async def get_enrollment(
    enrollment_id: UUID, user: CurrentUser = Depends(current_user), project_id: str = Project
) -> dict:
    return await asyncio.to_thread(_enrollment, project_id, enrollment_id)


@router.post('/enrollments/{enrollment_id}/cancel', response_model=EnrollmentOut)
async def cancel_enrollment(
    enrollment_id: UUID, user: CurrentUser = Depends(current_user), project_id: str = Project
) -> dict:
    """A queued or failed batch ends now; a running one stops before its next chunk. What already went stays sent."""

    def cancel() -> None:
        with get_conn() as conn:
            if instantly_enroll.request_cancel(conn, project_id, str(enrollment_id)) is None:
                raise api_error(404, 'INSTANTLY.ENROLLMENT_NOT_FOUND', 'enrollment not found')

    await asyncio.to_thread(cancel)
    return await asyncio.to_thread(_enrollment, project_id, enrollment_id)


@router.post('/enrollments/{enrollment_id}/retry', response_model=EnrollmentOut)
async def retry_enrollment(
    enrollment_id: UUID, response: Response, user: CurrentUser = Depends(current_user), project_id: str = Project
) -> dict:
    """Resumes what is left of a failed or stalled batch. People that may have gone out (`uncertain`) are first looked
    up in the campaign and sent again only when it does not hold them."""
    _enabled()

    def requeue() -> None:
        with get_conn() as conn:
            instantly_enroll.lock_project(conn, project_id)
            current = instantly_enroll.view(conn, project_id, str(enrollment_id))
            if not current:
                raise api_error(404, 'INSTANTLY.ENROLLMENT_NOT_FOUND', 'enrollment not found')
            if not current['can_retry']:
                raise api_error(409, 'INSTANTLY.NOTHING_TO_RETRY', 'this batch has nothing to resume')
            if instantly_publish.too_fresh_to_reconcile(conn, str(enrollment_id)):
                raise api_error(409, 'INSTANTLY.RECONCILE_TOO_SOON', 'wait a minute: Instantly may still be indexing')
            if conn.execute(
                "SELECT 1 FROM instantly_enrollments WHERE project_id = %s AND status IN ('queued', 'running') AND id <> %s",
                (project_id, str(enrollment_id)),
            ).fetchone():
                raise api_error(409, 'INSTANTLY.BUSY', 'another Instantly batch of this project is still running')
            # A later batch may hold these people in the campaign now: they stay out, as the unique index demands.
            conn.execute(
                "UPDATE instantly_leads f SET outcome = 'skipped', reason = 'already_in_instantly', updated_at = now()"
                " WHERE f.enrollment_id = %s AND f.outcome = 'failed' AND EXISTS (SELECT 1 FROM instantly_leads o"
                ' WHERE o.workspace_id = f.workspace_id AND o.campaign_id = f.campaign_id AND o.email = f.email'
                ' AND o.id <> f.id AND o.outcome = ANY(%s))',
                (str(enrollment_id), ['pending', *instantly_store.HELD]),
            )
            conn.execute(
                "UPDATE instantly_leads SET outcome = 'pending', reason = NULL, updated_at = now()"
                " WHERE enrollment_id = %s AND outcome = 'failed'",
                (str(enrollment_id),),
            )
            # The recheck runs as the member who resumes it: the one who confirmed may be gone.
            # What a user cancelled is on the people (`cancelled_in_flight`), so this run reconciles them and never sends.
            conn.execute(
                "UPDATE instantly_enrollments SET status = 'queued', error_code = NULL, cancel_requested = false,"
                ' finished_at = NULL, touched_at = now(), created_by = %s WHERE id = %s',
                (user.id, str(enrollment_id)),
            )

    await asyncio.to_thread(requeue)
    await asyncio.to_thread(_enqueue, project_id, str(enrollment_id))
    response.status_code = 202
    return await asyncio.to_thread(_enrollment, project_id, enrollment_id)
