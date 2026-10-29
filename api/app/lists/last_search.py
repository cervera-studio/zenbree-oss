"""The last paged search that filled a list, so more_rows can fetch the page after it."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from app.core.db import get_conn

PAGED = ('search_companies', 'search_people')


# Apollo documents at most 500 pages per search (app/people/apollo_models.py), whatever the page size.
# ponytail: AI Ark's page ceiling is not documented here, so its total gets the same 500 pages.
MAX_PAGES = 500


def remember_search(list_id: str, tool: str, args: dict, total: int | None = None, fetched: int = 0) -> None:
    """Keep the furthest page of the list's latest search; a new one also clears ``exhausted``.
    ``total`` is the vendor's count for these filters and ``fetched`` the rows its pages returned so far
    (this call's ``fetched`` added to the earlier pages of the same filters), kept to estimate what is left.
    Name lookups and Google Maps return one fixed batch: there is no next page to remember."""
    if tool not in PAGED or args.get('name') or args.get('local_query'):
        return
    page = int(args.get('page') or 1)
    filters = {k: v for k, v in args.items() if k != 'page'}
    keep: dict = {'tool': tool, 'args': {**filters, 'page': page}}
    if total is not None:
        keep['total'] = min(total, MAX_PAGES * fetched) if fetched else total  # a page holds ``fetched`` rows
    with get_conn() as conn:
        conn.execute(
            'UPDATE lists SET last_search = %s || jsonb_build_object('
            "'fetched', %s::int + coalesce(CASE WHEN (last_search->'args') - 'page' = %s"
            " THEN (last_search->>'fetched')::int END, 0)) WHERE id = %s"
            " AND (last_search IS NULL OR NOT (coalesce(last_search->'args', '{}') - 'page' = %s"
            " AND coalesce((last_search->'args'->>'page')::int, 1) >= %s))",
            (Jsonb(keep), fetched, Jsonb(filters), list_id, Jsonb(filters), page),
        )


def remember_result(list_id: str, tool: str, args: dict, result) -> None:
    remember_search(list_id, tool, args, result.data.get('total'), result.data.get('count', len(result.rows)))


# The estimate behind «~N persone ancora trovabili»; null when unknown, exhausted, or nothing is left to fetch.
FINDABLE_SQL = (
    " CASE WHEN coalesce(l.last_search ? 'exhausted', false) OR NOT coalesce(l.last_search ? 'total', false)"
    "  THEN NULL ELSE nullif(greatest((l.last_search->>'total')::int"
    " - coalesce((l.last_search->>'fetched')::int, 0), 0), 0) END AS findable,"
)
