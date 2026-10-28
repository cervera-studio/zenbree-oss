"""AI (research) columns: one prompt per column, answered cell by cell from a
web search, with the source of each cell kept in ``list_rows.cell_meta``.

Slimmed from the earlier implementation ``entity_column_enrichment.py`` (per-cell outcome filled/empty/failed, a rerun retries only missing
or failed cells); one cell's research is app/lists/ai_research.py. Runs in the Celery task ``fill_ai_column``, never
in a request.

Credits: one ``Tab`` per run, its hold sized to the pending cells (capped by the balance). Each *filled* cell writes
its ``row_charges`` record in the transaction that writes it (pay-if-found), and the run's end settles the hold from
those records, as the reaper does for a run that died (#660).

Progress (#252): after each commit the run appends a ``fill`` (status change) or ``row`` (one written
cell) frame to the list's Redis stream ``fill_stream_key(list)``; an open list tails it over SSE.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import structlog
from psycopg.types.json import Jsonb

from app.admin.settings import vendor_disabled_now
from app.agent import events
from app.agent.tools import registry
from app.billing import balance, credits
from app.billing.price import Price
from app.billing.tab import HoldGone, Tab
from app.core.db import get_conn
from app.core.scope import ActorGone, acting_for
from app.lists import fill_limits
from app.lists.ai_research import research_cell
from app.lists.ai_snippets import SnippetFill
from app.lists.row_view import VISIBLE_CELLS_SQL
from app.lists.rows import DELIVERED_SQL, is_internal, public_cells
from app.llm.base import LLMProvider
from app.llm.metering import MeteringContext, set_metering_context

logger = structlog.get_logger()

# Per filled cell; a run buys the cells of its own column (``keys``).
AI_CELL = Price(credits=1, keys=(), found=bool)
# ponytail: one run fills at most this many cells (best-fit first) so it ends well
# inside the task limit and the reservation reaper; "Fill" again does the rest.
MAX_CELLS_PER_RUN = 200
CONCURRENCY = 5
# ponytail: a run stops after this many cells in a row came back empty or failed (a prompt that cannot
# ground would otherwise burn a search and an LLM call per row for nothing); per-prompt tuning if it bites.
EARLY_STOP_AFTER = 10
# A queued/running fill older than this is a dead worker (task hard limit 16 min).
STALE_FILL_MINUTES = 20
# A run writes at most MAX_CELLS_PER_RUN rows plus two fill frames; a reload covers anything older.
FILL_STREAM_MAXLEN = 1000


def column_key(label: str, taken: set[str]) -> str:
    """``ai_<slug>``, unique in the list, never mistaken for an internal ``*_id`` key."""
    slug = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:40] or 'column'
    base = f'ai_{slug}' + ('_' if is_internal(f'ai_{slug}') else '')
    key, n = base, 2
    while key in taken:
        key, n = f'{base}_{n}', n + 1
    return key


# ── run ─────────────────────────────────────────────────────────────────────


# A fill is live while queued (since queued_at) or running (since started_at) for
# less than STALE_FILL_MINUTES; older means the worker died (SIGKILL, OOM).
_FILL_TS = (
    "CASE WHEN config->'fill'->>'status' = 'running' THEN config->'fill'->>'started_at'"
    " ELSE config->'fill'->>'queued_at' END"
)
FILL_LIVE_SQL = (
    "(coalesce(config->'fill'->>'status', '') IN ('queued', 'running')"
    f" AND coalesce(({_FILL_TS})::timestamptz, '-infinity') >= now() - interval '{STALE_FILL_MINUTES} minutes')"
)
# What the API shows: a dead fill reads as failed, so the web stops following it and allows a refill.
PUBLIC_CONFIG_SQL = (
    "CASE WHEN coalesce(config->'fill'->>'status', '') IN ('queued', 'running') AND NOT "
    + FILL_LIVE_SQL
    + ' THEN jsonb_set(config, \'{fill}\', (config->\'fill\') || \'{"status": "failed", "error": "fill_stale"}\')'
    ' ELSE config END'
)


def fill_stream_key(list_id: str) -> str:
    return f'list:{list_id}:fill'


def _publish(list_id: str, event: str, data: dict) -> bool:
    return events.append(
        fill_stream_key(list_id), event, data, maxlen=FILL_STREAM_MAXLEN, ttl=events.STREAM_TTL_SECONDS
    )


def _set_fill(conn, list_id: str, key: str, **fill: object) -> dict | None:
    """The column's merged ``fill``, None when it is gone."""
    row = conn.execute(
        "UPDATE list_columns SET config = jsonb_set(config, '{fill}', coalesce(config->'fill', '{}') || %s)"
        " WHERE list_id = %s AND key = %s RETURNING config->'fill' AS fill",
        (Jsonb(fill), list_id, key),
    ).fetchone()
    return row and row['fill']


def queue_fill(
    conn,
    list_id: str,
    key: str,
    cap: int | None = None,
    by: str | None = None,
    job: str | None = None,
    refill: list[str] | None = None,
) -> bool:
    """Mark the column queued unless a live fill already holds it. The caller enqueues.
    ``cap``: the column may hold at most this many filled or hand-edited cells after the run (a plan's approved rows).
    ``by``: the member who asked, who spends it (default: the list's creator).
    ``job``: the chat turn whose plan asked for it, which its charges name (#408).
    ``refill``: a snippet regenerate's rows (app/lists/ai_snippets.py), refilled even when filled.
    Raises ``FillLimited`` past the organization's live-fill cap or hourly quota (#960, #971)."""
    fill = {
        'status': 'queued',
        'queued_at': datetime.now(UTC).isoformat(),
        **({'cap': cap} if cap is not None else {}),
        **({'by': by} if by else {}),
        **({'job': job} if job else {}),
        **({'refill': refill} if refill else {}),
    }
    claimed = conn.execute(
        "UPDATE list_columns SET config = jsonb_set(config, '{fill}', %s)"
        f" WHERE list_id = %s AND key = %s AND kind = 'ai' AND deleted_at IS NULL AND NOT {FILL_LIVE_SQL}",
        (Jsonb(fill), list_id, key),
    ).rowcount
    # only a claim that will research a cell counts against the organization; the caller's rollback undoes it
    if claimed and (
        refill or conn.execute(f'SELECT 1 FROM list_rows r WHERE {_PENDING_SQL} LIMIT 1', (list_id, key)).fetchone()
    ):
        fill_limits.check(conn, list_id, FILL_LIVE_SQL)
    return bool(claimed)


def _room(list_id: str, key: str, cap: int | None) -> int:
    """How many more cells this run may fill: MAX_CELLS_PER_RUN, or what is left of ``cap``."""
    if cap is None:
        return MAX_CELLS_PER_RUN
    with get_conn() as conn:
        spent = conn.execute(
            # a hand edit (manual) may sit on a cell the plan already paid for, so it never frees room (#1095)
            'SELECT count(*) AS n FROM list_rows WHERE list_id = %s'
            " AND cell_meta->%s->>'status' IN ('filled', 'manual')",
            (list_id, key),
        ).fetchone()['n']
    return max(0, min(MAX_CELLS_PER_RUN, cap - spent))


def _claim(list_id: str, key: str) -> dict | None:
    """The queued fill, now running, and who spends it (``user_id``): the member who asked, or the list's creator.
    The task runs as that member, so one removed (#323) or banned (#389) since gets ``ActorGone`` here."""
    with get_conn() as conn:
        col = conn.execute(
            "UPDATE list_columns c SET config = jsonb_set(c.config, '{fill}', c.config->'fill' || %s)"
            ' FROM lists l WHERE c.list_id = %s AND c.key = %s AND l.id = c.list_id AND l.deleted_at IS NULL'
            " AND c.kind = 'ai' AND c.deleted_at IS NULL AND c.config->'fill'->>'status' = 'queued'"
            # the member it is queued for: a header naming a teammate (a forged message) claims nothing, so it
            # neither runs their fill as the teammate nor fails it when the teammate leaves mid-run
            " AND coalesce((c.config->'fill'->>'by')::uuid, l.user_id) = zb.actor()"
            " RETURNING c.config, coalesce((c.config->'fill'->>'by')::uuid, l.user_id) AS user_id,"
            ' l.organization_id, l.project_id, l.entity',
            (Jsonb({'status': 'running', 'started_at': datetime.now(UTC).isoformat()}), list_id, key),
        ).fetchone()
    return col


def _queued(list_id: str, key: str, user: str) -> bool:
    """Queued for ``user``: a forged header must not fail a teammate's fill."""
    with get_conn() as conn:
        return bool(
            conn.execute(
                'SELECT 1 FROM list_columns c JOIN lists l ON l.id = c.list_id WHERE c.list_id = %s AND c.key = %s'
                " AND c.config->'fill'->>'status' = 'queued'"
                " AND coalesce((c.config->'fill'->>'by')::uuid, l.user_id) = %s",
                (list_id, key, user),
            ).fetchone()
        )


_PENDING_SQL = f"list_id = %s AND {DELIVERED_SQL} AND coalesce(cell_meta->%s->>'status', 'failed') = 'failed'"


def _pending(list_id: str, key: str) -> list[dict]:
    """Delivered rows without a cell or with a failed one, best fit first (#150: never bill an undelivered row)."""
    with get_conn() as conn:
        return conn.execute(
            f'SELECT id, {VISIBLE_CELLS_SQL} AS cells FROM list_rows r WHERE {_PENDING_SQL}'
            ' ORDER BY relevance_score DESC NULLS LAST, seq LIMIT %s',
            (list_id, key, MAX_CELLS_PER_RUN),
        ).fetchall()


def _open(payer: credits.Payer, list_id: str, key: str, cells: int, job: str | None) -> Tab | credits.Refused:
    """A tab for ``cells`` cells, as many as the balance and the hard monthly cap of the list's project (#307)
    allow; refused when not even one fits."""
    with get_conn() as conn:
        tab = Tab.open(
            conn,
            payer,
            cells * AI_CELL.credits,
            need=AI_CELL.credits,
            kind='ai_column',
            reference_id=f'{list_id}:{key}',
            tool_name='ai_column',
            description='AI column fill',
            job_id=job,
        )
    # a hold, or credits short: auto top-up may be due either way
    balance.changed(payer.org_id, refused=isinstance(tab, credits.Refused))
    return tab


def _write_cell(run: _Run, row_id: str, cell: dict) -> bool:
    """False when the row stopped being delivered or the cell was edited by hand while it was researched, or when the
    run already finished: nothing written or billed. A filled cell records its charge in the transaction that writes
    it, so a run killed after the commit has paid for it."""
    # read once, before the connection: a cancel does not stop this thread, and ``finish`` may clear the run's tab
    # while it waits; a tab closed since then raises HoldGone at the lock and the transaction rolls back
    tab = run.tab
    if tab is None:
        return False
    list_id, key = run.list_id, run.key
    meta = {
        'status': cell['status'],
        'source': cell['source'],
        'error': cell['error'],
        'at': datetime.now(UTC).isoformat(),
    }
    cells = {key: cell['value']} if cell['status'] == 'filled' else {}
    with get_conn() as conn:
        if cells:  # the hold before the row, the order every charge locks in, so no two writers deadlock
            tab.lock(conn)
        written = conn.execute(
            'UPDATE list_rows r SET cells = cells || %s, cell_meta = cell_meta || %s WHERE id = %s AND list_id = %s'
            f" AND {DELIVERED_SQL} AND coalesce(r.cell_meta->%s->>'status', '') <> 'manual'",
            (Jsonb(cells), Jsonb({key: meta}), row_id, list_id, key),
        ).rowcount
        if written and cells:  # after the write: the charge records the keys the row now holds
            tab.record(conn, uuid.uuid4().hex, list_id, [row_id], run.price)
    if written:  # the same patch, merged the same way by the web
        run.publish('row', {'row_id': row_id, 'cells': public_cells(cells), 'cell_meta': {key: meta}})
    return bool(written)


class _Run:
    """One claimed fill: its tab and its per-outcome counts."""

    def __init__(self, list_id: str, key: str) -> None:
        self.list_id, self.key = list_id, key
        self.price = replace(AI_CELL, keys=(key,))
        self.tab: Tab | None = None
        self.org_id = ''  # whose wallet the tab is on, set with ``tab``
        self.counts = {'filled': 0, 'empty': 0, 'failed': 0}
        self.rows_streaming = True
        self.misses = 0  # consecutive empty or failed cells, for the early stop

    def publish(self, event: str, data: dict) -> None:
        """After a commit. Once a ``row`` frame fails the run stops sending them (one log per outage, not one
        per cell); a ``fill`` frame is always tried, so a blip mid-run never leaves a view on ``running``."""
        if event != 'row':
            _publish(self.list_id, event, data)
        elif self.rows_streaming:
            self.rows_streaming = _publish(self.list_id, event, data)

    def finish(self, status: str, error: str | None) -> None:
        """Settle the tab from the cells' records, refund the rest, record the outcome."""
        spent = 0
        with get_conn() as conn:
            if self.tab:
                try:
                    spent = self.tab.close(conn)
                except HoldGone:  # the reaper settled it from the same records
                    spent = credits.kept(conn, self.tab.hold.id)
            fill = _set_fill(conn, self.list_id, self.key, status=status, error=error, credits=spent, **self.counts)
        if self.tab:
            balance.changed(self.org_id)
        self.tab = None
        if fill:
            self.publish('fill', {'key': self.key, 'fill': fill})


async def fill_column(list_id: str, key: str, *, provider: LLMProvider | None = None) -> None:
    """Celery entry point. No-op unless the column is queued, so a redelivery never refills.

    Any crash (including the Celery soft time limit or a cancel) settles the hold and marks
    the fill failed before re-raising; only a hard kill leaves it running, and the API then
    reports it stale after STALE_FILL_MINUTES (the reaper settles the hold from the cells' records).

    A member who left or was banned gets ``ActorGone``: the organization then fails the fill ``not_a_member`` and
    settles what it delivered, as a turn's end does, and the task ends."""
    run = _Run(list_id, key)
    try:
        col = _claim(list_id, key)
    except ActorGone as gone:
        with acting_for(gone.scope.org, 'fill of a member who left'):
            if _queued(list_id, key, gone.scope.user):  # queued before they left: it holds nothing yet
                run.finish('failed', 'not_a_member')
        raise
    if not col:
        logger.warning('ai_column.not_queued', list_id=list_id, key=key)
        return
    run.publish('fill', {'key': key, 'fill': col['config']['fill']})
    try:
        await _fill(run, col, provider)
    except ActorGone as gone:
        with acting_for(gone.scope.org, 'fill of a member who left'):
            run.finish('failed', 'not_a_member')
        raise
    except BaseException:
        logger.exception('ai_column.fill_crashed', list_id=list_id, key=key)
        run.finish('failed', 'fill_crashed')
        raise


class _Research:
    endpoint = 'ai_column'
    refill = None

    def __init__(self, list_id: str, key: str, entity: str, config: dict) -> None:
        self.list_id, self.key, self.entity = list_id, key, entity
        self.prompt, self.cap = config.get('prompt') or '', (config.get('fill') or {}).get('cap')

    def refused(self) -> str | None:
        search = registry.get('web_search')
        return (
            'vendor_disabled' if search is None or (search.provider and vendor_disabled_now(search.provider)) else None
        )

    def rows(self, most: int) -> list[dict]:
        # ponytail: cap counts every filled or manual cell of the column, also ones before the plan (conservative)
        return _pending(self.list_id, self.key)[: min(most, _room(self.list_id, self.key, self.cap))]

    async def cell(self, provider: LLMProvider, user_id: str, row: dict) -> dict:
        return await research_cell(provider, user_id, self.entity, row['cells'] or {}, self.prompt)


async def _fill(run: _Run, col: dict, provider: LLMProvider | None) -> None:
    user_id, config = str(col['user_id']), col['config']
    mode = (SnippetFill if config.get('mode') == 'snippet' else _Research)(run.list_id, run.key, col['entity'], config)
    set_metering_context(MeteringContext(user_id=user_id, endpoint=mode.endpoint))
    if refused := mode.refused():
        return run.finish('failed', refused)
    rows = mode.rows(MAX_CELLS_PER_RUN)
    if rows:
        run.org_id = str(col['organization_id'])
        payer = credits.Payer(run.org_id, user_id, str(col['project_id']))
        tab = _open(payer, run.list_id, run.key, len(rows), (config.get('fill') or {}).get('job'))
        if isinstance(tab, credits.Refused):
            return run.finish('failed', 'project_cap_reached' if tab.capped else 'insufficient_credits')
        run.tab = tab
    rows = rows[: run.tab.hold.amount // AI_CELL.credits if run.tab else 0]
    if provider is None:
        from app.llm.router import get_provider

        provider = get_provider()
    gate = asyncio.Semaphore(CONCURRENCY)
    crashed: list[Exception] = []

    async def one(row: dict) -> None:
        try:
            async with gate:
                if run.misses >= EARLY_STOP_AFTER:
                    return
                cell = await mode.cell(provider, user_id, row)
                kept = mode.refill is not None and cell['status'] != 'filled'
                # counted before the gate frees: the next cell checks the early stop against it
                if kept or await asyncio.to_thread(_write_cell, run, str(row['id']), cell):
                    run.counts[cell['status']] += 1
                    run.misses = 0 if cell['status'] == 'filled' else run.misses + 1
        except HoldGone as e:  # no other cell can land on a hold the reaper closed
            crashed.append(e)
            raise
        except Exception as e:  # one cell's crash fails the fill, not the other cells
            crashed.append(e)

    # ActorGone, HoldGone or a cancel stops the other cells before their next vendor call
    try:
        async with asyncio.TaskGroup() as group:
            for row in rows:
                group.create_task(one(row))
    except BaseExceptionGroup as stopped:
        _, other = stopped.split(HoldGone)
        if other:
            raise other.exceptions[0] from None
    if crashed:
        logger.error('ai_column.fill_failed', list_id=run.list_id, key=run.key, error=str(crashed[0])[:200])
    run.finish('failed' if crashed else 'done', 'fill_crashed' if crashed else None)
