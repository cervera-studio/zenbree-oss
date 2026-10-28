"""The list export: CSV and XLSX of the delivered rows, with the optional row filters."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.auth.middleware import CurrentUser
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.exclusions.rules import is_excluded, list_rules
from app.lists.export_filters import extra_filters
from app.lists.rate_limit import limit_list_reads
from app.lists.routes import Auth, _columns, _own_list
from app.lists.row_view import VISIBLE_CELLS_SQL
from app.lists.rows import DELIVERED_SQL, to_csv, to_xlsx

router = APIRouter(prefix='/v1/lists')

_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
# Which rows an export holds, on top of "delivered and not matched by the user's exclusions".
ExportRows = Literal['all', 'high_fit', 'with_email']
_EXPORT_FILTERS = {
    'all': '',
    'high_fit': ' AND r.relevance_score >= 70',  # the web's "high" fit tone
    'with_email': f" AND coalesce({VISIBLE_CELLS_SQL}->>'email', '') <> ''",
}
Columns = Annotated[list[str] | None, Query(max_length=200)]


def _export(
    list_id: UUID,
    user: CurrentUser,
    rows: ExportRows,
    columns: list[str] | None,
    verified_only: bool,
    hide_off_target: bool,
) -> tuple[str, list, list]:
    """(file name, columns, rows); ``columns`` keeps only those keys, in list order."""
    with get_conn() as conn:
        lst = _own_list(conn, list_id, user.org_id)
        if (rows == 'with_email' or verified_only) and lst['entity'] != 'person':
            raise api_error(422, 'LIST.EXPORT_INVALID', 'email filters apply to person lists only')
        cols = _columns(conn, list_id)
        rules = list_rules(conn, list_id)
        data = conn.execute(
            f'SELECT r.cells AS raw, {VISIBLE_CELLS_SQL} AS cells, relevance_score FROM list_rows r'
            f' WHERE list_id = %s AND {DELIVERED_SQL}{_EXPORT_FILTERS[rows]}{extra_filters(verified_only, hide_off_target)} ORDER BY relevance_score DESC NULLS LAST, seq',
            (list_id,),
        ).fetchall()
    if columns:
        cols = [c for c in cols if c['key'] in columns]
    # A rule added after the rows landed still keeps them out of the file, matched on every cell, a pending one too (#416).
    data = [r for r in data if not is_excluded(rules, r['raw'])]
    return ''.join(c if c.isalnum() else '-' for c in lst['name'])[:60] or 'list', cols, data


def _attachment(name: str) -> dict:
    fallback = name.encode('ascii', 'replace').decode()  # headers are Latin-1; filename* carries the real name (#428)
    return {'Content-Disposition': f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name)}'}


@router.get(
    '/{list_id}/export.csv',
    dependencies=[Depends(limit_list_reads)],
    response_class=Response,
    responses={200: {'content': {'text/csv': {}}}},
)
def export_csv(
    list_id: UUID,
    user: CurrentUser = Auth,
    rows: ExportRows = 'all',
    columns: Columns = None,
    verified_only: bool = False,
    hide_off_target: bool = False,
) -> Response:
    name, cols, data = _export(list_id, user, rows, columns, verified_only, hide_off_target)
    return Response(to_csv(cols, data), media_type='text/csv; charset=utf-8', headers=_attachment(f'{name}.csv'))


@router.get(
    '/{list_id}/export.xlsx',
    dependencies=[Depends(limit_list_reads)],
    response_class=Response,
    responses={200: {'content': {_XLSX: {}}}},
)
def export_xlsx(
    list_id: UUID,
    user: CurrentUser = Auth,
    rows: ExportRows = 'all',
    columns: Columns = None,
    verified_only: bool = False,
    hide_off_target: bool = False,
) -> Response:
    name, cols, data = _export(list_id, user, rows, columns, verified_only, hide_off_target)
    return Response(to_xlsx(cols, data), media_type=_XLSX, headers=_attachment(f'{name}.xlsx'))
