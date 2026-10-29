"""Free rows (2026-10-07): searches cost 0 credits but still call a vendor, so a free search needs a positive
balance and an organization gets at most ``PER_DAY`` free rows a day, counted in Redis per UTC day.

The check holds a page of rows atomically (INCRBY) before the search runs and ``settle`` corrects it to the
rows returned, so parallel searches of one organization cannot all pass a check that counts after them."""

from datetime import UTC, datetime

import structlog

from app.agent import events
from app.agent.tools.registry import ToolResult
from app.core.db import get_conn

logger = structlog.get_logger()

SEARCHES = frozenset({'search_companies', 'search_people', 'linkedin_search', 'search_jobs_signals', 'more_rows'})
GATED = SEARCHES | {'web_search'}  # #964: web_search returns no rows but calls a paid vendor
PER_DAY = 1000
WEB_SEARCHES_PER_DAY = 300
PAGE = 100  # the most rows one search returns: what a check holds until its search settles


def check(session_id: str, unspent_hold: int, tool: str = '') -> tuple[str, ToolResult | None]:
    """``(hold, refusal)``: the hold to ``settle`` once the search ran ('' when none), or why it may not run."""
    with get_conn() as conn:
        row = conn.execute(
            'SELECT s.organization_id, c.credits_remaining FROM chat_sessions s'
            ' LEFT JOIN user_credits c ON c.organization_id = s.organization_id WHERE s.id = %s',
            (session_id,),
        ).fetchone()
    if row is None or (row['credits_remaining'] or 0) + unspent_hold <= 0:
        return '', ToolResult(
            ok=False,
            error='Searches are free but need a positive credit balance. Top up credits to continue.',
            error_code='no_credits',
        )
    if tool == 'web_search':
        return '', _web_search_refusal(str(row['organization_id']))
    return hold_page(str(row['organization_id']))


def hold_page(org: str) -> tuple[str, ToolResult | None]:
    """The daily allowance alone, for a caller with no balance rule (a saved-search run may have an empty wallet)."""
    key = _key(org)
    if not _hold(key):
        return '', ToolResult(
            ok=False,
            error=f'This organization reached its {PER_DAY} free rows for today. Tell the user to continue '
            'tomorrow; enriching the rows already found still works.',
            error_code='over_budget',  # a daily allowance, like the run's budget
        )
    return key, None


def _web_search_refusal(org: str) -> ToolResult | None:
    """One call is one unit and nothing settles: the count is the calls attempted today, per organization."""
    key = f'web_searches:{org}:{datetime.now(UTC).date().isoformat()}'
    try:
        r = events.redis_sync()
        taken = r.incr(key)
        r.expire(key, 2 * 86400)
        if not isinstance(taken, int) or taken <= WEB_SEARCHES_PER_DAY:
            return None
    except Exception as exc:  # ponytail: fail-open, as ``today``; the balance check still holds
        logger.warning('agent.web_searches_unheld', error=type(exc).__name__)
        return None
    return ToolResult(
        ok=False,
        error=f'This organization reached its {WEB_SEARCHES_PER_DAY} web searches for today. Tell the user to '
        'continue tomorrow; fetching pages and the rows already found still work.',
        error_code='over_budget',
    )


def _key(org: str) -> str:
    return f'free_rows:{org}:{datetime.now(UTC).date().isoformat()}'


def today(org: str) -> int:
    # ponytail: fail-open on a Redis outage, like the kill switches; chat's balance check still holds; a saved-search run has only this allowance
    try:
        value = events.redis_sync().get(_key(org))
        return int(value) if isinstance(value, bytes | str | int) else 0
    except Exception as exc:
        logger.warning('agent.free_rows_unread', error=type(exc).__name__)
        return 0


def _hold(key: str) -> bool:
    try:
        r = events.redis_sync()
        taken = r.incrby(key, PAGE)
        r.expire(key, 2 * 86400)
        if not isinstance(taken, int) or taken - PAGE < PER_DAY:
            return True
        r.decrby(key, PAGE)
        return False
    except Exception as exc:  # ponytail: fail-open, as ``today``
        logger.warning('agent.free_rows_unheld', error=type(exc).__name__)
        return True


def settle(key: str, rows: int) -> None:
    """The held page becomes the rows the search returned (0 when it failed)."""
    try:
        events.redis_sync().incrby(key, rows - PAGE)
    except Exception as exc:
        logger.warning('agent.free_rows_unsettled', error=type(exc).__name__)
