"""The reconciliation beat of Instantly (#930): what the webhook missed, read back from the API.

The task runs as ``System(DUE_WORK)`` (app/worker.py) only to pick the due connections; each one is then synced as
``ForOrg`` of its organization. A pass of one connection:

1. takes the connection's lease (``claim_token``), so two beats never read the same cursor;
2. attempts the due Instantly stops (#931, ``stops.drain``);
3. keeps its webhook (``subscription.ensure``);
4. reads the workspace's sent and received emails from each watermark on, a few pages at a time;
5. goes on with the round over the held leads, asked for by email: a lead's status (bounced, unsubscribed) and label;
6. applies the native effects that failed, and the replies no vote will come for (``effects.retry``), even after an
   Instantly error: they need only the database. A retry that fails is logged and the pass goes on;
7. sets when the next pass is due: soon while a cursor is part-way, later when the webhook delivers.

A connection picked for its stops alone skips steps 3 to 5 and 7, so its read schedule stays.

The kill switch stops the whole beat before any call. A disconnected connection is never picked, and its facts stay.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import structlog
from psycopg import sql

from app.admin.settings import is_disabled
from app.core.db import get_conn
from app.core.errors import Result, is_err, ok
from app.core.rate_limit import over_limit
from app.core.tasks import each_org
from app.integrations import instantly_store
from app.integrations.instantly_client import PAGE_SIZE
from app.integrations.instantly_store import HELD
from app.integrations.instantly_sync import client, effects, mapping, stops, store, subscription
from app.integrations.instantly_sync.vocabulary import Fact, Ignored
from app.outreach import stop_outbox

logger = structlog.get_logger()

PICK = 60  # connections a beat starts (about 300 polled ones every 5 minutes); the rest wait for the next beat
LEASE = timedelta(minutes=2)  # outlasts the task's 55 s hard limit
PAGES = 5  # pages of each read in one pass
# Instantly allows 20 calls a minute of GET /api/v2/emails for a workspace; two projects may share one.
EMAILS_PER_MINUTE = 15
OVERLAP = timedelta(minutes=2)  # a pass reads back this far before its watermark: a late index is not missed
INDEX_LAG = timedelta(seconds=30)  # an email stored just before a pass may not be listed yet
CATCHING_UP, POLLED, LIVE = timedelta(minutes=1), timedelta(minutes=5), timedelta(hours=1)
MAX_BACKOFF = timedelta(hours=1)
# A webhook reply is read back each minute this long, until the API lists it with its class.
CLASSIFY_FOR = timedelta(hours=1)
# Reset when the connection names another workspace: those cursors read the old one.
_WORKSPACE_STATE = (
    'webhook_refusal',
    'emails_refused_at',
    'webhook_checked_at',
    'sent_watermark',
    'sent_cursor',
    'sent_pass_started_at',
    'received_watermark',
    'received_cursor',
    'received_pass_started_at',
    'leads_campaign_id',
    'leads_cursor',
    'leads_round_started_at',
    'leads_round_done_at',
)
_HELD_SQL = ', '.join(f"'{outcome}'" for outcome in HELD)
EmailType = Literal['sent', 'received']


def due() -> list[dict]:
    """As System: the connected workspaces that hold leads and are due, or that have a stop to attempt; those with a
    stop first, then the longest waiting."""
    stops_due = stop_outbox.DUE_FOR.format('c.id')
    with get_conn() as conn:
        return conn.execute(
            'SELECT c.id::text AS connection_id, c.organization_id::text, c.project_id::text'
            ' FROM instantly_connections c LEFT JOIN instantly_sync_state s ON s.connection_id = c.id'
            " WHERE c.status = 'connected' AND (s.claim_until IS NULL OR s.claim_until < now())"
            # A connect since the last pass asks the new key at once: the old wait may be the hour of a live webhook.
            ' AND (((s.connection_id IS NULL OR s.next_run_at <= now() OR s.last_attempt_at < c.connected_at)'
            ' AND EXISTS (SELECT 1 FROM instantly_leads l WHERE l.project_id = c.project_id'
            f' AND l.workspace_id = c.workspace_id AND l.outcome IN ({_HELD_SQL}))) OR {stops_due})'
            f' ORDER BY {stops_due} DESC, s.next_run_at NULLS FIRST LIMIT %s',
            (PICK,),
        ).fetchall()


async def run() -> dict:
    if await asyncio.to_thread(is_disabled, 'vendor_disabled:instantly'):
        return {'paused': True}
    until = stops.deadline()
    by_org: dict[str, list[dict]] = defaultdict(list)
    for picked in await asyncio.to_thread(due):
        by_org[picked['organization_id']].append(picked)

    async def unit(org: str) -> list[str]:
        return [await sync_one(picked, until) for picked in by_org[org]]

    done = await each_org(by_org, 'instantly reconcile', unit)
    return {'connections': sum(len(d) for d in done)}


async def sync_one(picked: dict, until: float) -> str:
    """One pass of one connection, as ``ForOrg`` of its organization; its stops start no attempt after ``until``.
    Never raises on an Instantly error."""
    token = str(uuid4())
    state = await asyncio.to_thread(_claim, picked['connection_id'], token)
    if state is None:
        return 'busy'
    connection = await asyncio.to_thread(_connection, picked['project_id'])
    key = instantly_store.api_key(connection)
    if is_err(key) or connection is None or str(connection['id']) != picked['connection_id']:
        await asyncio.to_thread(_finish, picked['connection_id'], token, state, error=None, more=False)
        return 'skipped'
    binding = store.Binding(
        organization_id=picked['organization_id'],
        project_id=picked['project_id'],
        connection_id=picked['connection_id'],
        workspace_id=connection['workspace_id'],
    )
    await stops.drain(binding, key.value, connection['connected_at'], until)
    if not _reads_due(state, connection):
        await _retry(binding)
        await asyncio.to_thread(_release_keeping_schedule, binding.connection_id, token)
        return 'stops'
    result = await _passes(binding, key.value, state, datetime.now(UTC))
    await _retry(binding)
    if is_err(result) and result.error.code == 'INSTANTLY.INVALID_KEY':
        await asyncio.to_thread(stops.refuse_key, binding.connection_id, connection['connected_at'], result.error.code)
    error = result.error.code if is_err(result) else None
    more = bool(not is_err(result) and result.value)
    await asyncio.to_thread(_finish, binding.connection_id, token, state, error=error, more=more)
    if error:
        logger.info('instantly.reconcile.failed', code=error, connection_id=binding.connection_id)
    return error or 'synced'


def _reads_due(state: dict, connection: dict) -> bool:
    if not state['campaigns']:
        return False
    last = state['last_attempt_at']
    return last is None or state['next_run_due'] or last < connection['connected_at']


async def _passes(binding: store.Binding, key: str, state: dict, now: datetime) -> Result[bool]:
    """True when a read stopped part-way and the next pass should come soon. A key that may not read emails (a key
    made for #929 alone) still gets the leads round: bounces, unsubscribes and labels come from it."""
    hooked = await subscription.ensure(binding, key, now=now)
    if is_err(hooked):
        return hooked
    more, refused = False, False
    for email_type in ('sent', 'received'):
        read = await _emails(binding, key, state, email_type, now)
        if is_err(read) and read.error.code == 'INSTANTLY.MISSING_SCOPE':
            refused = True  # a limit of the key, shown on the card, not a failure to back off from
            break
        if is_err(read):
            return read
        more = more or read.value
    await asyncio.to_thread(_save_refusal, binding.connection_id, now if refused else None)
    leads = await _leads(binding, key, state, now)
    return leads if is_err(leads) else ok(more or leads.value)


def classify_soon(conn, connection_id: str) -> None:
    conn.execute(
        "UPDATE instantly_sync_state SET next_run_at = least(next_run_at, now() + interval '1 minute')"
        ' WHERE connection_id = %s AND emails_refused_at IS NULL',
        (connection_id,),
    )


async def _emails(binding: store.Binding, key: str, state: dict, email_type: EmailType, now: datetime) -> Result[bool]:
    cursor: str | None = state[f'{email_type}_cursor']
    started: datetime = state[f'{email_type}_pass_started_at'] or now
    since = (state[f'{email_type}_watermark'] or state['floor'] or now) - OVERLAP
    budget = f'instantly:emails:{binding.workspace_id}'
    for _ in range(PAGES):
        if await asyncio.to_thread(over_limit, budget, 'instantly_emails', EMAILS_PER_MINUTE, 60):
            return ok(True)
        page = await client.list_emails(key, email_type=email_type, since=since, cursor=cursor)
        if is_err(page):
            return page
        await _record(binding, [mapping.from_email(item, now=now) for item in page.value.items])
        cursor = page.value.next_cursor
        if cursor is None:
            # Everything stored before this pass began is now read: the watermark moves to it.
            await _save(binding, f'{email_type}_watermark', started - INDEX_LAG, f'{email_type}_cursor', None)
            await _save(binding, f'{email_type}_pass_started_at', None)
            return ok(False)
        await _save(binding, f'{email_type}_cursor', cursor, f'{email_type}_pass_started_at', started)
    return ok(True)


async def _leads(binding: store.Binding, key: str, state: dict, now: datetime) -> Result[bool]:
    """The round over the held leads, by campaign id and then email, 100 leads a call and a few calls a pass. Only the
    held leads are asked for (``contacts``): the user's own leads in the same campaign are never read."""
    campaigns: list[str] = state['campaigns'] or []
    campaign: str | None = state['leads_campaign_id']
    after: str | None = state['leads_cursor']  # the last email of the campaign already read
    started: datetime = state['leads_round_started_at'] or now
    if campaign not in campaigns:  # a campaign whose leads are no longer held: go on with the next one
        campaign, after = next((c for c in campaigns if campaign is None or c > campaign), None), None
    for _ in range(PAGES):
        if campaign is None:
            break
        emails = await asyncio.to_thread(_held_emails, binding, campaign, after)
        if emails:
            page = await client.list_leads(key, campaign_id=campaign, emails=emails)
            if is_err(page):
                return page
            await _record(binding, [f for item in page.value.items for f in mapping.from_lead(item, campaign, now=now)])
        if len(emails) < PAGE_SIZE:
            campaign, after = next((c for c in campaigns if c > campaign), None), None
        else:
            after = emails[-1]
        await _save(binding, 'leads_campaign_id', campaign, 'leads_cursor', after)
        await _save(binding, 'leads_round_started_at', started)
    if campaign is not None:
        return ok(True)
    # The round reached every campaign, on the last call of this pass too.
    await _save(binding, 'leads_round_done_at', started, 'leads_round_started_at', None)
    await _save(binding, 'leads_campaign_id', None, 'leads_cursor', None)
    return ok(False)


def _held_emails(binding: store.Binding, campaign_id: str, after: str | None) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT DISTINCT email FROM instantly_leads WHERE project_id = %s AND workspace_id = %s'
            f' AND campaign_id = %s AND outcome IN ({_HELD_SQL}) AND email > %s ORDER BY email LIMIT %s',
            (binding.project_id, binding.workspace_id, campaign_id, after or '', PAGE_SIZE),
        ).fetchall()
    return [r['email'] for r in rows]


async def _record(binding: store.Binding, items: list[Fact | Ignored]) -> None:
    if items:
        await _effects(lambda conn: effects.record_and_apply(conn, binding, items))


async def _retry(binding: store.Binding) -> None:
    """A failed retry must not keep the pass from refusing a key and releasing its lease, else the connection waits
    out the lease: the next pass retries."""
    try:
        await _effects(lambda conn: effects.retry(conn, binding))
    except Exception as exc:
        # The type only: a message may carry an address.
        logger.error(
            'instantly.effects.retry_failed', connection_id=binding.connection_id, error_type=type(exc).__name__
        )


async def _effects(write: Callable[[Any], effects.Effects]) -> None:
    def run() -> effects.Effects:
        with get_conn() as conn:
            return write(conn)

    (await asyncio.to_thread(run)).announce()


async def _save(binding: store.Binding, *pairs: object) -> None:
    """``column, value, column, value``: progress kept after each page, so a killed pass resumes there."""
    columns = pairs[0::2]
    if not set(columns) <= set(_WORKSPACE_STATE):  # names from this module, never from a payload
        raise ValueError(f'not a cursor column: {columns}')
    sets = sql.SQL(', ').join(sql.SQL('{} = %s').format(sql.Identifier(str(column))) for column in columns)

    def write() -> None:
        with get_conn() as conn:
            conn.execute(
                sql.SQL('UPDATE instantly_sync_state SET {} WHERE connection_id = %s').format(sets),
                (*pairs[1::2], binding.connection_id),
            )

    await asyncio.to_thread(write)


def _save_refusal(connection_id: str, refused_at: datetime | None) -> None:
    """``emails_refused_at`` decides whether ``outcomes.changes()`` shows a webhook send or reply the API never listed.
    When it turns on, those facts appear, so they take a new ``changed`` in the same transaction: a consumer already
    past their old one would never see them. The lock is the one the writer of ``instantly_events`` takes."""
    with get_conn() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('instantly_events:' || %s, 0))", (connection_id,))
        before = conn.execute(
            'SELECT emails_refused_at FROM instantly_sync_state WHERE connection_id = %s FOR UPDATE', (connection_id,)
        ).fetchone()
        conn.execute(
            'UPDATE instantly_sync_state SET emails_refused_at = %s WHERE connection_id = %s',
            (refused_at, connection_id),
        )
        if refused_at and before and before['emails_refused_at'] is None:
            conn.execute(
                "UPDATE instantly_events SET changed = nextval('instantly_events_changed_seq')"
                " WHERE connection_id = %s AND kind IN ('sent', 'reply') AND provider_ref IS NULL",
                (connection_id,),
            )


def _claim(connection_id: str, token: str) -> dict | None:
    """The state of a due connection, leased to this pass; None while another pass holds it or it is not due."""
    reset = sql.SQL(', ').join(
        sql.SQL('{c} = CASE WHEN s.workspace_id = EXCLUDED.workspace_id THEN s.{c} END').format(c=sql.Identifier(c))
        for c in _WORKSPACE_STATE
    )
    query = sql.SQL(
        'INSERT INTO instantly_sync_state AS s (connection_id, organization_id, project_id, workspace_id, claim_token,'
        ' claim_until) SELECT c.id, c.organization_id, c.project_id, c.workspace_id, %(token)s, now() + %(lease)s'
        " FROM instantly_connections c WHERE c.id = %(id)s AND c.status = 'connected'"
        ' ON CONFLICT (connection_id) DO UPDATE SET claim_token = EXCLUDED.claim_token,'
        ' claim_until = EXCLUDED.claim_until, workspace_id = EXCLUDED.workspace_id, {reset}'
        ' WHERE (s.claim_until IS NULL OR s.claim_until < now()) AND (s.next_run_at <= now()'
        ' OR s.last_attempt_at < (SELECT connected_at FROM instantly_connections WHERE id = s.connection_id)'
        # Due by the clock that wrote next_run_at: a worker clock a few ms behind the database's would skip a pass.
        ' OR {stops_due}) RETURNING s.*, s.next_run_at <= now() AS next_run_due'
    ).format(reset=reset, stops_due=sql.SQL(stop_outbox.DUE_FOR.format('s.connection_id')))
    with get_conn() as conn:
        state = conn.execute(query, {'token': token, 'lease': LEASE, 'id': connection_id}).fetchone()
        if state is None:
            return None
        held = conn.execute(
            'SELECT min(created_at) AS floor, array_agg(DISTINCT campaign_id ORDER BY campaign_id) AS campaigns'
            f' FROM instantly_leads WHERE project_id = %s AND workspace_id = %s AND outcome IN ({_HELD_SQL})',
            (state['project_id'], state['workspace_id']),
        ).fetchone()
        webhook = conn.execute(
            "SELECT status FROM instantly_webhook_subscriptions WHERE connection_id = %s AND status <> 'retired'",
            (connection_id,),
        ).fetchone()
    return {**state, **held, 'webhook': webhook['status'] if webhook else None}


def _connection(project_id: str) -> dict | None:
    with get_conn() as conn:
        return instantly_store.row(conn, project_id)


def _release_keeping_schedule(connection_id: str, token: str) -> None:
    with get_conn() as conn:
        conn.execute(
            'UPDATE instantly_sync_state SET claim_token = NULL, claim_until = NULL'
            ' WHERE connection_id = %s AND claim_token = %s',
            (connection_id, token),
        )


def _finish(connection_id: str, token: str, state: dict, *, error: str | None, more: bool) -> None:
    """Releases the lease and sets the next pass: soon while a read is part-way, then by the webhook's state; after an
    error, a wait that doubles up to an hour."""
    now = datetime.now(UTC)
    if error:
        failing = now - (state['failing_since'] or now)
        wait = min(max(failing * 2, CATCHING_UP), MAX_BACKOFF)
    with get_conn() as conn:
        if not error:
            # A key that may not read emails never classifies a reply: waiting for it would poll for nothing.
            waiting = conn.execute(
                "SELECT 1 FROM instantly_events e WHERE e.connection_id = %s AND e.reply_nature = 'unclassified'"
                ' AND e.received_at > now() - %s AND NOT EXISTS (SELECT 1 FROM instantly_sync_state s'
                ' WHERE s.connection_id = e.connection_id AND s.emails_refused_at IS NOT NULL) LIMIT 1',
                (connection_id, CLASSIFY_FOR),
            ).fetchone()
            wait = CATCHING_UP if more or waiting else LIVE if state['webhook'] == 'live' else POLLED
        conn.execute(
            'UPDATE instantly_sync_state SET claim_token = NULL, claim_until = NULL, last_attempt_at = now(),'
            ' last_success_at = CASE WHEN %(failed)s THEN last_success_at ELSE now() END, last_error_code = %(code)s,'
            ' failing_since = CASE WHEN %(failed)s THEN coalesce(failing_since, now()) END,'
            ' next_run_at = now() + %(wait)s WHERE connection_id = %(id)s AND claim_token = %(token)s',
            {'failed': error is not None, 'code': error, 'wait': wait, 'id': connection_id, 'token': token},
        )
