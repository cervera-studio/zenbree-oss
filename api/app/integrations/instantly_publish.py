"""Publish one confirmed Instantly batch (#929): chunks of at most 100 people, each judged again just before it goes.

Three rules keep it safe to run twice and safe to stop:

- Nothing is held across the HTTP call. A chunk is judged and marked ``publishing`` in one short transaction, sent, and
  settled in another. A worker killed in between leaves ``publishing`` rows, which the next start reads as ``uncertain``.
- A request that went out and got no answer (a timeout, a 5xx) makes its chunk ``uncertain``, and the run stops. It is
  never retried blindly: :func:`reconcile` first asks Instantly which of those emails the campaign holds, and only
  those it does not hold go again. Instantly also skips a lead already in the same campaign.
- A request refused before it did anything (429, no connection) goes back to ``pending`` and is safe to repeat.

Instantly's answer to ``leads/add`` counts the leads it left out (blocklist, duplicate, invalid) but names none:
a lead that comes back without an id is ``skipped`` with the reason ``provider_skipped``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from psycopg.types.json import Jsonb

from app.admin.settings import vendor_disabled_now
from app.billing.paid_call import current_user_id
from app.core.db import get_conn
from app.core.errors import Err, Result, err, is_err, ok
from app.crm import gate
from app.integrations import instantly_client, instantly_enroll, instantly_store, instantly_verdicts
from app.integrations.instantly_fields import Mapping, lead
from app.lists.row_view import VISIBLE_CELLS_SQL
from app.outreach import stop_outbox

logger = structlog.get_logger()

CHUNK = 100  # the pace the API reference advises: batches of 100, a wait between them
PAUSE_SECONDS = 2.0
MAX_RATE_WAITS = 3
MAX_RATE_WAIT_SECONDS = 60.0
# Instantly says a new lead can take a few seconds to be searchable: a "not found" is trusted only after this long.
MIN_RECONCILE_AGE_SECONDS = 60
# Refused before anything happened, or fixable by the user: the chunk goes back to pending.
_RETRY_LATER = frozenset(
    {
        'INSTANTLY.UNREACHABLE',
        'INSTANTLY.RATE_LIMITED',
        'INSTANTLY.INVALID_KEY',
        'INSTANTLY.PLAN_REQUIRED',
        'INSTANTLY.MISSING_SCOPE',
    }
)


def claim(enrollment_id: str) -> dict | None:
    """The queued batch, now running. ``publishing`` rows of a worker that died become ``uncertain``."""
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE instantly_enrollments SET status = 'running', started_at = coalesce(started_at, now()),"
            ' touched_at = now(), error_code = NULL, run_token = gen_random_uuid()'
            " WHERE id = %s AND status = 'queued' RETURNING *",
            (enrollment_id,),
        ).fetchone()
        if row:
            conn.execute(
                # updated_at stays: the age of the doubt is the age of the request.
                "UPDATE instantly_leads SET outcome = 'uncertain' WHERE enrollment_id = %s AND outcome = 'publishing'",
                (enrollment_id,),
            )
    return row


def _finish(enrollment_id: str, token, status: str, code: str | None = None) -> None:
    """Ends the run that holds ``token``: a batch a user cancelled, or a retry took into a new run, is not ours to end.
    A run that ends ``completed`` while a person a user cancelled is still marked (``cancelled`` or
    ``cancelled_in_flight``) ends ``cancelled``; one whose cancelled people were all found in Instantly has no mark left."""
    with get_conn() as conn:
        ended = conn.execute(
            "UPDATE instantly_enrollments SET status = CASE WHEN %s = 'completed' AND EXISTS (SELECT 1 FROM"
            " instantly_leads l WHERE l.enrollment_id = %s AND l.reason IN ('cancelled', 'cancelled_in_flight'))"
            " THEN 'cancelled' ELSE %s END, error_code = %s, finished_at = now(), touched_at = now()"
            " WHERE id = %s AND status = 'running' AND run_token = %s RETURNING id",
            (status, enrollment_id, status, code, enrollment_id, token),
        ).fetchone()
        if ended and status == 'cancelled':
            conn.execute(
                "UPDATE instantly_leads SET outcome = 'skipped', reason = 'cancelled', updated_at = now()"
                " WHERE enrollment_id = %s AND outcome = 'pending'",
                (enrollment_id,),
            )
            # Whether they went out stays unknown until a reconcile looks; the reason tells it to release them.
            conn.execute(
                "UPDATE instantly_leads SET reason = 'cancelled_in_flight' WHERE enrollment_id = %s AND outcome = 'uncertain'",
                (enrollment_id,),
            )
        if not ended:
            return
    logger.info('instantly.enrollment_finished', enrollment_id=enrollment_id, status=status, error_code=code)


def _set(lead_ids: list, outcome: str, reason: str | None = None, *, was: str, attempt: bool = False) -> list:
    with get_conn() as conn:
        return _move(conn, lead_ids, outcome, reason, was=was, attempt=attempt)


def _move(conn, lead_ids: list, outcome: str, reason: str | None = None, *, was: str, attempt: bool = False) -> list:
    """Moves people out of ``was`` only, and returns the ones that moved: a worker that lost its place to a cancel or
    a second run finds them elsewhere and changes nothing."""
    moved = conn.execute(
        'UPDATE instantly_leads SET outcome = %s, reason = %s, attempts = attempts + %s, updated_at = now()'
        ' WHERE id = ANY(%s) AND outcome = %s RETURNING id',
        (outcome, reason, 1 if attempt else 0, lead_ids, was),
    ).fetchall()
    return [r['id'] for r in moved]


def _must_stop(enrollment_id: str, token) -> bool:
    """Beats the heart of this run and tells whether to stop: a cancel was asked, or a retry gave the batch to a newer
    run (this one is stale and must change nothing)."""
    with get_conn() as conn:
        row = conn.execute(
            'UPDATE instantly_enrollments SET touched_at = now() WHERE id = %s AND run_token = %s'
            ' RETURNING cancel_requested',
            (enrollment_id, token),
        ).fetchone()
    return not row or row['cancel_requested']


def too_fresh_to_reconcile(conn, enrollment_id) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM instantly_leads WHERE enrollment_id = %s AND outcome = 'uncertain'"
            ' AND updated_at > now() - make_interval(secs => %s) LIMIT 1',
            (enrollment_id, MIN_RECONCILE_AGE_SECONDS),
        ).fetchone()
    )


async def reconcile(key: str, enrollment: dict) -> Result[None]:
    """Ask Instantly which of the uncertain emails the campaign holds: those are ``accepted`` (with their lead id),
    the others go back to ``pending``. An answer that does not come leaves them uncertain."""
    with get_conn() as conn:
        uncertain = conn.execute(
            "SELECT id, email, reason FROM instantly_leads WHERE enrollment_id = %s AND outcome = 'uncertain'"
            ' ORDER BY seq',
            (enrollment['id'],),
        ).fetchall()
    for start in range(0, len(uncertain), instantly_client.PAGE_SIZE):
        chunk = uncertain[start : start + instantly_client.PAGE_SIZE]
        found = await instantly_client.find_leads(key, enrollment['campaign_id'], [r['email'] for r in chunk])
        if is_err(found):
            return found
        with get_conn() as conn:
            for r in chunk:
                if lead_id := found.value.get(r['email']):
                    conn.execute(
                        "UPDATE instantly_leads SET outcome = 'accepted', reason = NULL, instantly_lead_id = %s,"
                        " updated_at = now() WHERE id = %s AND outcome = 'uncertain'",
                        (lead_id, r['id']),
                    )
                elif r['reason'] == 'cancelled_in_flight':
                    # Not in the campaign and nobody wants them sent now: this releases the hold.
                    conn.execute(
                        "UPDATE instantly_leads SET outcome = 'skipped', updated_at = now()"
                        " WHERE id = %s AND outcome = 'uncertain'",
                        (r['id'],),
                    )
                else:
                    conn.execute(
                        "UPDATE instantly_leads SET outcome = 'pending', reason = NULL, updated_at = now()"
                        " WHERE id = %s AND outcome = 'uncertain'",
                        (r['id'],),
                    )
    return ok(None)


def _next_chunk(enrollment_id) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            f'SELECT l.id, l.row_id, l.email, l.payload, {VISIBLE_CELLS_SQL} AS cells, r.excluded FROM instantly_leads l'
            ' LEFT JOIN list_rows r ON r.id = l.row_id AND r.organization_id = l.organization_id'
            " WHERE l.enrollment_id = %s AND l.outcome = 'pending' ORDER BY l.seq LIMIT %s",
            (enrollment_id, CHUNK),
        ).fetchall()


def _schedule_gate_retry(org_id: str):
    def schedule(owner_id: str, delay: int) -> None:
        from app.worker import retry_hubspot_read

        retry_hubspot_read.apply_async((owner_id, org_id), countdown=delay)

    return schedule


async def _crm_read(enrollment: dict, emails: list[str]) -> datetime | None:
    """Read HubSpot for these people now, when their owner has it: the time the verdicts are fresh since."""
    owner = str(enrollment['created_by'])
    with get_conn() as conn:
        if not instantly_verdicts.hubspot_owner(conn, owner):
            return None
    from app.outreach.dispatcher import _access_token

    now = datetime.now(UTC)
    await gate.check({owner: emails}, now, _access_token, _schedule_gate_retry(str(enrollment['organization_id'])))
    return now


async def _judge_chunk(enrollment: dict, chunk: list[dict]) -> list[dict]:
    """The people of ``chunk`` that may still go. The others are ``skipped`` with their reason (``row_removed`` when
    their list row is gone); the ones that go are marked ``publishing``."""
    fresh_since = await _crm_read(enrollment, [r['email'] for r in chunk])
    present = [r for r in chunk if r['cells'] is not None]
    with get_conn() as conn:
        instantly_enroll.lock_project(conn, str(enrollment['project_id']))
        verdicts = instantly_verdicts.judge(
            conn,
            project_id=str(enrollment['project_id']),
            owner_id=str(enrollment['created_by']),
            rows=[{'id': r['id'], 'cells': r['cells'], 'excluded': r['excluded']} for r in present],
            enrollment_id=str(enrollment['id']),
            crm_fresh_since=fresh_since,
        )
        # The address sent is the one confirmed: when the cell changed since, the recheck judged another person.
        reasons = {
            v.row_id: v.reason or ('email_changed' if v.email != r['email'] else None)
            for v, r in zip(verdicts, present, strict=True)
        }
        for r in chunk:
            if r['cells'] is None:
                _move(conn, [r['id']], 'skipped', 'row_removed', was='pending')
            elif reason := reasons[str(r['id'])]:
                _move(conn, [r['id']], 'skipped', reason, was='pending')
        going = [r for r in present if not reasons[str(r['id'])]]
        claimed = set(_move(conn, [r['id'] for r in going], 'publishing', was='pending', attempt=True))
    return [r for r in going if r['id'] in claimed]


def _backfill_payloads(mapping: Mapping, going: list[dict]) -> list[dict]:
    with get_conn() as conn:
        for r in going:
            if r['payload'] is None:
                r['payload'] = conn.execute(
                    'UPDATE instantly_leads SET payload = coalesce(payload, %s) WHERE id = %s RETURNING payload',
                    (Jsonb(lead(r['cells'], r['email'], mapping)), r['id']),
                ).fetchone()['payload']
    return going


async def _send_chunk(key: str, enrollment: dict, going: list[dict]) -> Result[None]:
    payload = [r['payload'] for r in going]
    ids = [r['id'] for r in going]
    added = await instantly_client.add_leads(key, enrollment['campaign_id'], payload)
    if isinstance(added, Err):
        code = added.error.code
        if code == 'INSTANTLY.UNKNOWN_OUTCOME':
            _set(ids, 'uncertain', code, was='publishing')  # it went out: whether it took effect is not known
        elif code in _RETRY_LATER:
            _set(ids, 'pending', was='publishing')
        else:
            _set(ids, 'failed', code, was='publishing')
        return added
    with get_conn() as conn:
        for i, r in enumerate(going):
            if lead_id := added.value.created.get(i):
                conn.execute(
                    "UPDATE instantly_leads SET outcome = 'accepted', reason = NULL, instantly_lead_id = %s,"
                    " updated_at = now() WHERE id = %s AND outcome = 'publishing'",
                    (lead_id, r['id']),
                )
            else:
                conn.execute(
                    "UPDATE instantly_leads SET outcome = 'skipped', reason = 'provider_skipped', updated_at = now()"
                    " WHERE id = %s AND outcome = 'publishing'",
                    (r['id'],),
                )
    logger.info('instantly.chunk_sent', enrollment_id=str(enrollment['id']), sent=len(going), **added.value.counts)
    return ok(None)


async def _publish_loop(key: str, enrollment: dict) -> tuple[str, str | None]:
    mapping = Mapping.model_validate(enrollment['mapping'])
    eid = enrollment['id']
    rate_waits = 0
    while chunk := _next_chunk(eid):
        if _must_stop(eid, enrollment['run_token']):
            return 'cancelled', None
        if vendor_disabled_now('instantly'):
            return 'failed', 'INSTANTLY.DISABLED'
        going = await _judge_chunk(enrollment, chunk)
        if not going:
            continue
        sent = await _send_chunk(key, enrollment, _backfill_payloads(mapping, going))
        if isinstance(sent, Err):
            code = sent.error.code
            if code == 'INSTANTLY.RATE_LIMITED' and rate_waits < MAX_RATE_WAITS:
                rate_waits += 1
                await asyncio.sleep(min(sent.error.context.get('retry_after') or 5, MAX_RATE_WAIT_SECONDS))
                continue
            return 'failed', code
        rate_waits = 0
        await asyncio.sleep(PAUSE_SECONDS)
    return 'completed', None


async def _prepare(enrollment: dict, connection: dict | None) -> Result[str]:
    """The key, after the checks that need no chunk: a connected workspace that is still the batch's own, and a
    campaign that still exists. The campaign is never activated here."""
    if not connection or connection['workspace_id'] != enrollment['workspace_id']:
        return err('INSTANTLY.NOT_CONNECTED', detail='connect the Instantly workspace of this batch')
    key = instantly_store.api_key(connection)
    if is_err(key):
        return key
    found = await instantly_client.get_campaign(key.value, enrollment['campaign_id'])
    return found if is_err(found) else key


def _release_cancelled_in_doubt(enrollment_id) -> None:
    """The campaign is gone, so it holds no one: the people a user cancelled while in flight are released instead of
    waiting for a lookup that can never answer. ponytail: assumes a deleted campaign deletes its leads."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE instantly_leads SET outcome = 'skipped' WHERE enrollment_id = %s AND outcome = 'uncertain'"
            " AND reason = 'cancelled_in_flight'",
            (enrollment_id,),
        )


async def _run(enrollment: dict, connection: dict | None) -> tuple[str, str | None]:
    if vendor_disabled_now('instantly'):
        return 'failed', 'INSTANTLY.DISABLED'  # before any call to Instantly, the reconcile and the campaign read too
    key = await _prepare(enrollment, connection)
    if is_err(key):
        if key.error.code == 'INSTANTLY.NOT_FOUND':
            _release_cancelled_in_doubt(enrollment['id'])
        return 'failed', key.error.code
    with get_conn() as conn:
        if too_fresh_to_reconcile(conn, enrollment['id']):
            return 'failed', 'INSTANTLY.RECONCILE_TOO_SOON'
    reconciled = await reconcile(key.value, enrollment)
    if is_err(reconciled):
        return 'failed', reconciled.error.code
    return await _publish_loop(key.value, enrollment)


async def run(enrollment_id: str) -> None:
    enrollment = claim(enrollment_id)
    if not enrollment:
        return
    if not enrollment['created_by']:
        # The member who confirmed it is gone and nobody took it over (retry does): the recheck has no owner to run as.
        _finish(enrollment_id, enrollment['run_token'], 'cancelled')
        return
    current_user_id.set(str(enrollment['created_by']))
    # Read once: the run calls with this row's key, so a refusal is written against this row's connected_at alone.
    with get_conn() as conn:
        connection = instantly_store.row(conn, str(enrollment['project_id']))
    try:
        status, code = await _run(enrollment, connection)
    except Exception as exc:
        logger.error('instantly.run_failed', enrollment_id=enrollment_id, error_type=type(exc).__name__)
        status, code = 'failed', 'INSTANTLY.INTERNAL'
    if code == 'INSTANTLY.INVALID_KEY' and connection:
        with get_conn() as conn:
            stop_outbox.refuse_key(conn, str(connection['id']), connection['connected_at'], 'INSTANTLY.INVALID_KEY')
    _finish(enrollment_id, enrollment['run_token'], status, code)
