"""Small helpers shared by the m1-tools handlers."""

from __future__ import annotations

import uuid

from app.admin.settings import vendor_disabled_now
from app.agent.tools.registry import ToolResult
from app.core.errors import Err
from app.lists.lookup_outcome import Contact, NoRowMatch, NothingToSearch

DISABLED = 'This data source is temporarily disabled.'
NO_ROW_MATCH = (
    'None of the row_ids match a row of the current list, or the rows are excluded, so nobody was searched. '
    'Use the row_id of the List rows, not the list id. Do not retry the ids of excluded rows.'
)
NOTHING_TO_SEARCH = (
    'No row is left to search, so nobody was searched and nothing was charged: the rows asked for already have '
    'what this tool finds, or, with no row_ids, no row of the list is left for the next batch. Do not call this '
    'tool again for them: tell the user that nobody was left to search.'
)
UNDELIVERED = (
    "The result's list.undelivered maps each row this call touched that still misses a required column to those "
    'columns (it is absent when none does). Never tell the user such a row is complete: name what it misses, and '
    'call the tool that finds that column when it has not run yet.'
)


def row_uuid(value: object) -> str | None:
    """The row id in Postgres' own spelling, or None for junk (it would fail the ``::uuid`` cast): Postgres reads a
    braced, upper-case or hyphen-less uuid as the same row, and refuses the ``urn:uuid:`` form Python accepts."""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def no_row_match(contact: Contact) -> ToolResult:
    """Ids that load no row: no search was made, so not ok, nothing billed, and ``skipped`` keeps a plan's step open
    for the corrected call."""
    return ToolResult(
        ok=False,
        error=NO_ROW_MATCH,
        error_code='skipped',
        outcome=NoRowMatch(kind='no_row_match', contact=contact),
    )


def nothing_to_search(contact: Contact, unmatched: list[str]) -> ToolResult:
    """The rows asked for, or the list's next batch, leave nobody to search: the work is done, so ok, nothing billed.
    No ``found``: the chat shows no count, never «0 email». ``searched: 0`` keeps a plan's step where it was."""
    return ToolResult(
        ok=True,
        data={'searched': 0, 'failed': [], 'nothing_left': NOTHING_TO_SEARCH, **unmatched_data(unmatched)},
        outcome=NothingToSearch(kind='nothing_to_search', contact=contact, unmatched=len(unmatched)),
    )


def unmatched_ids(requested: list[str], loaded: list[dict]) -> list[str]:
    """The requested ids that loaded no row (wrong, junk, excluded or from another list), each once, in order."""
    found = {p['row_id'] for p in loaded}
    return list(dict.fromkeys(i for i in requested if (row_uuid(i) or i) not in found))


def unmatched_data(unmatched: list[str]) -> dict:
    """The tool-data key that tells the model which of its ids matched nothing; absent when all matched."""
    return {'unmatched_row_ids': unmatched} if unmatched else {}


def providers_off(providers: tuple[str, ...]) -> frozenset[str]:
    """The ``providers`` whose kill switch is on."""
    return frozenset(p for p in providers if vendor_disabled_now(p))


def city_country(location: str | None) -> tuple[str | None, str | None]:
    """``"Milan, Lombardy, Italy"`` → ``("Milan", "Italy")``; one part → city only."""
    parts = [p.strip() for p in (location or '').split(',') if p.strip()]
    if not parts:
        return None, None
    return parts[0], (parts[-1] if len(parts) > 1 else None)


def err_text(result: Err) -> str:
    """What the model reads back for a vendor failure: the detail, never the raw body."""
    return f'{result.error.code}: {result.error.detail}' if result.error.detail else result.error.code
