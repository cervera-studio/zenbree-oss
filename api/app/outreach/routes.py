"""Campaign builder (#84): drafts from a list, steps, sender pool, preview, activation, pause/resume and the
approval queue. Scoped by the campaign's ``organization_id`` on every read and write; its
children (targets, sends) by the campaign's ``owner_id``, which they share by foreign key. A campaign belongs to
its list's project and sends from that project's senders (#307), so any member of the organization may run it.

Adapted from the earlier implementation ``outreach/use_cases/approval_queue.py`` and ``preview_campaign_launch.py``. Sending itself is the
beat dispatcher (``app.outreach.dispatcher``); the test email is ``app.outreach.test_email_routes``. Nothing here
calls a provider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.auth.middleware import CurrentUser, current_user
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.linkedin.api import RISK_NOTICE
from app.outreach import activation, approval_queue, holds, rejects, send_states, sending
from app.outreach.approval_queue import QueuedSend, QueuedSends
from app.outreach.campaign_models import Campaign, Campaigns, CampaignStatus, CreateIn, PatchIn
from app.outreach.campaign_sql import campaign_query
from app.outreach.drafts import draft_from_list, drop_blocked_drafts
from app.outreach.fields import CampaignField, FieldsRefused, parse_fields
from app.outreach.freeze import freeze
from app.outreach.preview import LivePreviewIn, Preview, preview_items
from app.outreach.step_copy import StepsRefused, check_subject, checked_steps
from app.outreach.target_rows import Targets, read_targets
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/campaigns')
Auth = Depends(current_user)
Project = Depends(current_project)

MAX_BULK = 1000
TEST_EMAILS_PER_HOUR = 10


class NoQueuedSend(BaseModel):
    """What an edit answers when the person moved on between the update and the read."""

    model_config = ConfigDict(extra='forbid')


class Approved(BaseModel):
    approved: int
    held: int


class Rejected(BaseModel):
    rejected: int


class Restored(BaseModel):
    restored: int


def _own(conn, campaign_id: UUID, org_id: str, *, lock: bool = False) -> dict:
    row = conn.execute(
        'SELECT * FROM outreach_campaigns WHERE id = %s AND organization_id = %s' + (' FOR UPDATE' if lock else ''),
        (campaign_id, org_id),
    ).fetchone()
    if not row:
        raise api_error(404, 'CAMPAIGN.NOT_FOUND', 'campaign not found')
    return row


def _fields(raw: object) -> list[CampaignField]:
    try:
        return parse_fields(raw)
    except FieldsRefused as e:
        raise api_error(422, e.code, str(e)) from None


def _detail(conn, campaign_id: UUID, org_id: str) -> dict:
    row = conn.execute(*campaign_query(org_id, where='AND c.id = %s', params=(campaign_id,), limit=1)).fetchone()
    if not row:
        raise api_error(404, 'CAMPAIGN.NOT_FOUND', 'campaign not found')
    [row] = approval_queue.with_counts([row], approval_queue.counts(conn, row['project_id'], row['id']))
    return {**row, **_detail_stats(conn, row), 'linkedin_risk_notice': RISK_NOTICE}


# C3 (#218). "Invalid email": the address could not be sent to at all (INVALID_EMAIL), or its email bounced hard
# (#747, a DSN read from the sending mailbox).
_DETAIL_STATS_SQL = """
SELECT c.send_window_start_hour, c.send_window_end_hour, c.send_window_days, c.b_percent, c.allocation_paused_at,
       (SELECT count(*) FROM outreach_campaign_sends s WHERE s.campaign_id = c.id
          AND s.channel = 'email' AND s.status IN ('sending', 'sent')
          AND s.attempted_at >= %(day_start)s) AS sent_today,
       (SELECT count(DISTINCT m.target_id) FROM outreach_inbound_messages m
         WHERE m.campaign_id = c.id AND m.owner_id IS NOT DISTINCT FROM c.owner_id AND m.kind = 'reply'
           AND m.label IN ('interested', 'meeting_request')) AS interested,
       (SELECT count(DISTINCT m.target_id) FROM outreach_inbound_messages m
         WHERE m.campaign_id = c.id AND m.owner_id IS NOT DISTINCT FROM c.owner_id AND m.kind = 'auto_reply') AS auto_replies,
       (SELECT count(*) FROM outreach_inbound_messages m
         WHERE m.campaign_id = c.id AND m.owner_id IS NOT DISTINCT FROM c.owner_id AND m.read_at IS NULL) AS unread_replies,
       (SELECT count(DISTINCT s.target_id) FROM outreach_campaign_sends s
         WHERE s.campaign_id = c.id AND (s.bounce_kind = 'hard' OR s.error_code = 'INVALID_EMAIL' AND s.status = 'refused')) AS invalid_email
FROM outreach_campaigns c WHERE c.id = %(id)s"""


def _detail_stats(conn, campaign: dict) -> dict:
    """Send window, today's sends against the pool's daily caps, and the reply and failure breakdown."""
    now = datetime.now(UTC)
    day_start = sending.day_start(now, settings.outreach_send_window_timezone)
    stats = conn.execute(_DETAIL_STATS_SQL, {'id': campaign['id'], 'day_start': day_start}).fetchone()
    # ponytail: a mailbox's cap is shared by all the campaigns of the projects it serves; this is the pool's whole cap.
    mailboxes = conn.execute(
        'SELECT daily_limit, created_at FROM user_oauth_connections'
        " WHERE id IN (SELECT sender_id FROM outreach_campaign_senders WHERE campaign_id = %s) AND status = 'connected'",
        (campaign['id'],),
    ).fetchall()
    return {**stats, 'daily_cap': sum(sending.daily_cap(m['daily_limit'], m['created_at'], now) for m in mailboxes)}


def project_campaigns(
    conn, org_id: str, project_id: str, *, statuses: list[str] | None = None, limit: int = 200
) -> list[dict]:
    params: list = [project_id]
    where = 'AND c.project_id = %s'
    if statuses is not None:
        where, params = where + ' AND c.status = ANY(%s)', [*params, statuses]
    return conn.execute(*campaign_query(org_id, where=where, params=tuple(params), limit=limit)).fetchall()


@router.get('', response_model=Campaigns)
def list_campaigns(status: CampaignStatus | None = None, user: CurrentUser = Auth, project_id: str = Project) -> dict:
    """The active project's campaigns, newest first, of one ``status`` when given."""
    with get_conn() as conn:
        rows = project_campaigns(conn, user.org_id, project_id, statuses=[status] if status else None)
        rows = approval_queue.with_counts(rows, approval_queue.counts(conn, project_id))
    return {'campaigns': [{k: v for k, v in r.items() if k not in ('steps', 'fields')} for r in rows]}


@router.post('', status_code=201, response_model=Campaign, response_model_exclude_unset=True)
def create_campaign(body: CreateIn, user: CurrentUser = Auth) -> dict:
    """A draft enrolling the list's people as draft targets. Nothing is sendable until activation."""
    with get_conn() as conn:
        lst = conn.execute(
            'SELECT entity FROM lists WHERE id = %s AND organization_id = %s AND deleted_at IS NULL',
            (body.list_id, user.org_id),
        ).fetchone()
        if not lst:
            raise api_error(404, 'LIST.NOT_FOUND', 'list not found')
        if lst['entity'] != 'person':
            raise api_error(422, 'CAMPAIGN.LIST_NOT_PEOPLE', 'a campaign needs a list of people')
        draft = draft_from_list(
            conn,
            user.id,
            str(body.list_id),
            body.name,
            reuse=False,
            block_active_duplicates=body.block_active_duplicates,
        )
        return _detail(conn, UUID(draft.id), user.org_id)


@router.get('/{campaign_id}', response_model=Campaign, response_model_exclude_unset=True)
def get_campaign(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        return _detail(conn, campaign_id, user.org_id)


@router.patch('/{campaign_id}', response_model=Campaign, response_model_exclude_unset=True)
def patch_campaign(campaign_id: UUID, body: PatchIn, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id, lock=True)
        if c['status'] in ('stopped', 'completed'):
            raise api_error(409, 'CAMPAIGN.INVALID_TRANSITION', f'a {c["status"]} campaign cannot be edited')
        steps = fields = None
        if body.steps is not None or body.fields is not None:
            # Live targets index into the steps by number: rewriting them under an active
            # sequence would shift people onto other messages.
            if c['status'] != 'draft':
                raise api_error(
                    409, 'CAMPAIGN.NOT_DRAFT', 'steps and fields can only be edited while the campaign is a draft'
                )
            fields = _fields(c['fields'] if body.fields is None else body.fields)
            try:
                steps = checked_steps(c['steps'] if body.steps is None else body.steps, fields=fields)
            except StepsRefused as e:
                raise api_error(422, e.code, e.message[:300], step=e.step, variant=e.variant) from None
        conn.execute(
            'UPDATE outreach_campaigns SET name = coalesce(%s, name), steps = coalesce(%s, steps),'
            ' fields = coalesce(%s, fields), block_active_duplicates = coalesce(%s, block_active_duplicates)'
            ' WHERE id = %s',
            (
                body.name,
                Jsonb(steps) if steps is not None else None,
                Jsonb([f.model_dump() for f in fields]) if fields is not None else None,
                body.block_active_duplicates,
                campaign_id,
            ),
        )
        return _detail(conn, campaign_id, user.org_id)


@router.delete('/{campaign_id}', status_code=204)
def delete_campaign(campaign_id: UUID, user: CurrentUser = Auth) -> None:
    with get_conn() as conn:
        if _own(conn, campaign_id, user.org_id, lock=True)['status'] != 'draft':
            raise api_error(409, 'CAMPAIGN.NOT_DRAFT', 'only a draft can be deleted; pause it instead')
        conn.execute('DELETE FROM outreach_campaigns WHERE id = %s', (campaign_id,))


@router.post('/{campaign_id}/activate', response_model=Campaign, response_model_exclude_unset=True)
def activate(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    """Draft → active, explicitly. Its draft people become active; approval stays as configured (on)."""
    with get_conn() as conn, conn.transaction():  # a refusal below undoes the dropped drafts too
        # Project lock first (same order as enrollment and refill): the recheck below must see every
        # campaign of the project that was activated before this one.
        project_id = _own(conn, campaign_id, user.org_id)['project_id']
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        c = _own(conn, campaign_id, user.org_id, lock=True)
        if c['status'] != 'draft':
            raise api_error(409, 'CAMPAIGN.INVALID_TRANSITION', f'a {c["status"]} campaign cannot be activated')
        if not c['steps']:
            raise api_error(422, 'CAMPAIGN.NO_STEPS', 'write at least one step first')
        activation.check(conn, c)
        if not (frozen := freeze(conn, c, drop_blocked_drafts(conn, c))).active:
            raise api_error(422, 'CAMPAIGN.NO_TARGETS', 'this campaign has nobody to contact', **frozen.why_waiting())
        conn.execute(
            "UPDATE outreach_campaigns SET status = 'active', activated_at = now() WHERE id = %s", (campaign_id,)
        )
        return _detail(conn, campaign_id, user.org_id)


def _transition(campaign_id: UUID, user: CurrentUser, src: str, dst: str) -> dict:
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id, lock=True)
        if c['status'] != src:
            raise api_error(409, 'CAMPAIGN.INVALID_TRANSITION', f'a {c["status"]} campaign cannot become {dst}')
        conn.execute('UPDATE outreach_campaigns SET status = %s WHERE id = %s', (dst, campaign_id))
        return _detail(conn, campaign_id, user.org_id)


@router.post('/{campaign_id}/pause', response_model=Campaign, response_model_exclude_unset=True)
def pause(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    """Stops new claims and deliveries at the next beat; a send already at the provider is not recalled."""
    return _transition(campaign_id, user, 'active', 'paused')


@router.post('/{campaign_id}/resume', response_model=Campaign, response_model_exclude_unset=True)
def resume(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    return _transition(campaign_id, user, 'paused', 'active')


@router.get('/{campaign_id}/preview', response_model=Preview)
def preview(campaign_id: UUID, user: CurrentUser = Auth, limit: int = Query(default=3, ge=1, le=10)) -> dict:
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id)
        return {'items': preview_items(conn, c, c['steps'], parse_fields(c['fields']), limit)}


@router.post('/{campaign_id}/preview', response_model=Preview)
def live_preview(campaign_id: UUID, body: LivePreviewIn, user: CurrentUser = Auth) -> dict:
    """The builder's preview of unsaved steps (#217): the same render as ``GET .../preview``, nothing stored."""
    steps = [{**s.model_dump(), 'step': n} for n, s in enumerate(body.steps, 1)]
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id)
        fields = _fields(c['fields'] if body.fields is None else body.fields)
        return {'items': preview_items(conn, c, steps, fields, body.limit)}


@router.get('/{campaign_id}/targets', response_model=Targets)
def targets(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    """The people of a campaign (#218): step, state and newest activity, first 1000."""
    with get_conn() as conn:
        return {'items': read_targets(conn, _own(conn, campaign_id, user.org_id), MAX_BULK, datetime.now(UTC))}


@router.get('/{campaign_id}/approvals', response_model=QueuedSends)
def approvals(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id)
        return {'items': approval_queue.items(conn, c, datetime.now(UTC), MAX_BULK)}


class SendIdsIn(BaseModel):
    send_ids: list[UUID] = Field(min_length=1, max_length=MAX_BULK)


_AWAITING = send_states.guard('approved', frm=('awaiting_approval',), column='s.status')
_APPROVABLE = (
    f's.id = ANY(%s) AND s.campaign_id = %s AND s.owner_id IS NOT DISTINCT FROM %s AND {_AWAITING}'
    " AND t.id = s.target_id AND t.owner_id IS NOT DISTINCT FROM s.owner_id AND t.status = 'active' AND t.next_step = s.step"
)


@router.post('/{campaign_id}/approvals/approve', response_model=Approved)
def approve(campaign_id: UUID, body: SendIdsIn, user: CurrentUser = Auth) -> dict:
    """Approve exactly the messages named (the ones shown), never "everything waiting now":
    the queue grows while someone reads it. Approving does not send; the next beat does. A message to a person who is
    held (a stop in Instantly, or Instantly sends their email) stays waiting and counts as ``held``."""
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id)
        holds.lock_out_enrollments(conn, [c['project_id']], wait=True)
        rows = conn.execute(
            "UPDATE outreach_campaign_sends s SET status = 'approved', approved_at = now()"
            f' FROM outreach_campaign_targets t WHERE {_APPROVABLE}'
            f' AND {holds.not_held("%s", "s.channel", "t.email", "t.linkedin_url")} RETURNING s.id',
            (body.send_ids, campaign_id, c['owner_id'], c['project_id']),
        ).fetchall()
        held = conn.execute(
            f'SELECT count(*) AS n FROM outreach_campaign_sends s, outreach_campaign_targets t WHERE {_APPROVABLE}',
            (body.send_ids, campaign_id, c['owner_id']),
        ).fetchone()['n']
    return {'approved': len(rows), 'held': held}


@router.post('/{campaign_id}/approvals/reject', response_model=Rejected)
def reject(campaign_id: UUID, body: SendIdsIn, user: CurrentUser = Auth) -> dict:
    """The person skips this step (``skipped``/``REJECTED``) and moves to the next one, like ``NO_EMAIL``."""
    with get_conn() as conn:
        return {'rejected': rejects.reject(conn, _own(conn, campaign_id, user.org_id), body.send_ids)}


@router.post('/{campaign_id}/approvals/undo-reject', response_model=Restored)
def undo_reject(campaign_id: UUID, body: SendIdsIn, user: CurrentUser = Auth) -> dict:
    """«Annulla» after a reject (#230): the send waits again and the person goes back to its step.

    Only while nothing has moved since: the person is still active, one step past the rejected one, and no
    later step has been claimed. The person's row is locked before that check, the lock the dispatcher takes
    before it claims a step (``dispatcher._claim``), so a claim cannot slip in between check and restore.
    The campaign row is locked first, the lock ``dispatcher.complete_finished`` takes before it completes a
    campaign, so a campaign cannot complete while someone is being put back (campaign, then targets: one order).
    NO KEY UPDATE, not UPDATE: a claim holds a person's row and then inserts a send, whose foreign key takes
    KEY SHARE on the campaign; a full UPDATE lock here would wait on that claim while it waits on us.
    """
    restored = 0
    with get_conn() as conn:
        owner_id = _own(conn, campaign_id, user.org_id)['owner_id']
        c = conn.execute(
            'SELECT status FROM outreach_campaigns WHERE id = %s FOR NO KEY UPDATE', (campaign_id,)
        ).fetchone()
        if c['status'] not in ('active', 'paused'):
            return {'restored': 0}
        rows = conn.execute(
            'SELECT id, target_id, step FROM outreach_campaign_sends WHERE id = ANY(%s) AND campaign_id = %s'
            " AND owner_id IS NOT DISTINCT FROM %s AND status = 'skipped' AND error_code = 'REJECTED' AND target_id IS NOT NULL"
            ' ORDER BY target_id, step',  # one lock order, so two undos never deadlock
            (body.send_ids, campaign_id, owner_id),
        ).fetchall()
        for r in rows:
            target = conn.execute(
                'SELECT status, next_step FROM outreach_campaign_targets WHERE id = %s FOR UPDATE', (r['target_id'],)
            ).fetchone()
            if not target or target['status'] != 'active' or target['next_step'] != r['step'] + 1:
                continue
            # A fresh statement after the lock: it sees any claim committed before we got it.
            moved = conn.execute(
                'SELECT 1 FROM outreach_campaign_sends WHERE campaign_id = %s AND target_id = %s AND step > %s LIMIT 1',
                (campaign_id, r['target_id'], r['step']),
            ).fetchone()
            if moved or not send_states.move(
                conn, r['id'], ('skipped',), 'awaiting_approval', set_='error_code = NULL'
            ):
                continue
            conn.execute(
                'UPDATE outreach_campaign_targets SET next_step = %s WHERE id = %s', (r['step'], r['target_id'])
            )
            restored += 1
    return {'restored': restored}


class SendEditIn(BaseModel):
    # Lengths and emptiness are checked in the route (they depend on the channel), so an over-long edit gets
    # CAMPAIGN.COPY_TOO_LONG, not a bare 422. A LinkedIn message has no subject.
    subject: str = Field(default='', max_length=10_000)
    body: str = Field(max_length=50_000)


@router.patch('/{campaign_id}/approvals/{send_id}', response_model=QueuedSend | NoQueuedSend)
def edit_send(campaign_id: UUID, send_id: UUID, body: SendEditIn, user: CurrentUser = Auth) -> dict:
    """Rewrite one held message before approving it. The dispatcher refuses leftover placeholders anyway."""
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id)
        owner_id = c['owner_id']
        held = conn.execute(
            'SELECT channel FROM outreach_campaign_sends WHERE id = %s AND campaign_id = %s'
            " AND owner_id IS NOT DISTINCT FROM %s AND status = 'awaiting_approval'",
            (send_id, campaign_id, owner_id),
        ).fetchone()
        if not held:
            raise api_error(404, 'CAMPAIGN.SEND_NOT_FOUND', 'no such message waiting for approval')
        channel = held['channel']
        subject = body.subject if channel == 'email' else ''
        fields = [('body', body.body, sending.NOTE_MAX_PREMIUM if channel == 'linkedin_connect' else sending.MAX_BODY)]
        if channel == 'email':
            fields.insert(0, ('subject', subject, sending.MAX_SUBJECT))
            if not subject.strip():
                raise api_error(422, 'CAMPAIGN.COPY_EMPTY', 'subject: a subject is required')
        for field, value, limit in fields:
            if len(value) > limit:
                raise api_error(422, 'CAMPAIGN.COPY_TOO_LONG', f'{field}: {len(value)} characters, at most {limit}')
        if channel != 'linkedin_connect' and not body.body.strip():
            raise api_error(422, 'CAMPAIGN.COPY_EMPTY', 'body: the message cannot be empty')
        try:
            check_subject(subject)
        except StepsRefused as e:
            raise api_error(422, e.code, e.message) from None
        if placeholder := sending.find_placeholder(subject, body.body):
            raise api_error(422, 'CAMPAIGN.PLACEHOLDER', f'fill or remove {placeholder}')
        row = conn.execute(
            'UPDATE outreach_campaign_sends SET subject = %s, body = %s WHERE id = %s AND campaign_id = %s'
            " AND owner_id IS NOT DISTINCT FROM %s AND status = 'awaiting_approval' RETURNING id",
            (subject, body.body, send_id, campaign_id, owner_id),
        ).fetchone()
        if not row:
            raise api_error(404, 'CAMPAIGN.SEND_NOT_FOUND', 'no such message waiting for approval')
        queued = approval_queue.items(conn, c, datetime.now(UTC), 1, send_id)
        return queued[0] if queued else {}
