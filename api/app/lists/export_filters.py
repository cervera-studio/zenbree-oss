"""The export's optional row filters, on top of the ``rows`` choice."""

from __future__ import annotations

from app.lists.lookups import verified_email_sql
from app.lists.row_view import VISIBLE_CELLS_SQL

# Visible cells, like the ``with_email`` rule: a pending (hidden) email or status never counts.
_VERIFIED = f' AND {verified_email_sql(VISIBLE_CELLS_SQL)}'
_NOT_OFF_TARGET = " AND r.rating IS DISTINCT FROM 'bad'"


def extra_filters(verified_only: bool, hide_off_target: bool) -> str:
    return (_VERIFIED if verified_only else '') + (_NOT_OFF_TARGET if hide_off_target else '')
