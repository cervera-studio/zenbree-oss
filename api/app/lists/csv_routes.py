"""CSV import (#344): preview, upload (queues the worker job), and the import's state, stream and discarded rows.
Owner-scoped. Included before ``routes.router`` so ``/imports/...`` never reads as a list id."""

from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.agent import events
from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.event_stream import EventStream
from app.lists.csv_import import CsvError, headers, read_csv
from app.lists.csv_import import column_key as header_key
from app.lists.csv_plan import make_plan
from app.lists.csv_run import TERMINAL, stream_key
from app.lists.prices import CreditPrices, credit_prices
from app.lists.routes import Entity
from app.projects.routes import current_project_or_api_key, default_project, own_project

router = APIRouter(prefix='/v1/lists')
Auth = Depends(current_user_or_api_key)
logger = structlog.get_logger()
Mapping = Annotated[str | None, Query()]


class CsvPreview(BaseModel):
    headers: list[str]
    samples: list[str]
    rows: int
    mapping: list[str]
    to_import: int
    duplicates: int
    in_lists: int
    excluded: int
    incomplete: int
    skipped: int
    prices: CreditPrices


class CsvImportState(BaseModel):
    id: UUID
    status: Literal['queued', 'running', 'done', 'failed']
    name: str
    total: int
    processed: int
    rows_added: int
    duplicates: int
    in_lists: int
    excluded: int
    skipped: int
    discarded: int
    list_id: UUID | None
    chat_session_id: UUID | None
    error: str | None
    cursor: str | None


_STATE_SQL = (
    'SELECT id, status, name, total, processed, rows_added, duplicates, in_lists, excluded, skipped,'
    ' duplicates + in_lists + excluded + skipped AS discarded, list_id, chat_session_id, error FROM csv_imports'
    ' WHERE organization_id = %s AND user_id = %s'
)


def _not_found() -> HTTPException:
    return api_error(404, 'LIST.IMPORT_NOT_FOUND', 'import not found')


def _mapping(raw: str | None) -> list[str] | None:
    """``mapping`` query param: a JSON array with the chosen key per column (see ``parse_csv``)."""
    if raw is None:
        return None
    if len(raw) > 8000:
        raise api_error(422, 'LIST.CSV_BAD_MAPPING', 'mapping is too long')
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise api_error(422, 'LIST.CSV_BAD_MAPPING', 'mapping must be a JSON array of strings')
    return value


def _preview_counts(project_id: str, entity: Entity, rows: list[tuple[int, dict]], skipped: list[int]):
    """What the import would do with ``rows``, without writing. ``incomplete``: rows to import with no
    email (person) or no domain/website (company)."""
    with get_conn() as conn:
        plan = make_plan(conn, project_id, entity, rows, skipped)
    contact = ('email',) if entity == 'person' else ('domain', 'website')
    return {
        'to_import': len(plan.keep),
        'duplicates': plan.count('in_file'),
        'in_lists': plan.count('in_lists'),
        'excluded': plan.count('excluded'),
        'incomplete': sum(not any(m.get(k) for k in contact) for m in plan.keep),
        'skipped': plan.count('no_identity'),
    }


@router.post('/upload/preview', response_model=CsvPreview)
async def preview_csv(
    request: Request,
    entity: Entity,
    mapping: Mapping = None,
    user: CurrentUser = Auth,
    project_id: str = Depends(current_project_or_api_key),
) -> dict:
    """The import dialog's mapping and review steps: nothing is written, nothing is charged."""
    content, chosen = await request.body(), _mapping(mapping)
    try:
        heads, samples, total = await asyncio.to_thread(headers, content)
        _, rows, skipped = await asyncio.to_thread(read_csv, content, entity, chosen)
    except CsvError as e:
        raise api_error(422, e.code, str(e)) from None
    counts = await asyncio.to_thread(_preview_counts, project_id, entity, rows, skipped)
    return {
        'headers': heads,
        'samples': samples,
        'rows': total,
        'mapping': [header_key(entity, h) for h in heads],
        **counts,
        'prices': credit_prices(),
    }


def _queue(user: CurrentUser, entity: Entity, name: str, project_id: UUID | None, mapping, content: bytes) -> str:
    from app.worker import run_csv_import

    with get_conn() as conn:
        pid = own_project(conn, project_id, user.org_id) if project_id else default_project(conn, user)
        import_id = str(
            conn.execute(
                'INSERT INTO csv_imports (organization_id, user_id, project_id, entity, name, mapping, content)'
                ' VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
                (
                    user.org_id,
                    user.id,
                    pid,
                    entity,
                    name,
                    json.dumps(mapping) if mapping is not None else None,
                    content,
                ),
            ).fetchone()['id']
        )
    try:
        run_csv_import.delay(import_id)
    except Exception as exc:
        logger.error('csv_import.enqueue_failed', error_type=type(exc).__name__)
        with get_conn() as conn:
            conn.execute("UPDATE csv_imports SET status = 'failed', content = NULL WHERE id = %s", (import_id,))
        raise api_error(503, 'LIST.IMPORT_UNAVAILABLE', 'import unavailable') from None
    return import_id


def _state(user: CurrentUser, import_id: UUID | str) -> dict:
    """The import and a stream cursor read before it, so a frame is either in the state or after the cursor."""
    cursor = events.last_id(stream_key(str(import_id)))
    with get_conn() as conn:
        row = conn.execute(f'{_STATE_SQL} AND id = %s', (user.org_id, user.id, import_id)).fetchone()
    if not row:
        raise _not_found()
    return {**row, 'cursor': cursor}


@router.post('/upload', status_code=202, response_model=CsvImportState)
async def upload_csv(
    request: Request,
    entity: Entity,
    name: str = Query(default='', max_length=120),
    project_id: UUID | None = None,
    mapping: Mapping = None,
    user: CurrentUser = Auth,
) -> dict:
    """Raw CSV body (not multipart). The file is checked here, then imported by the worker: follow it with
    ``GET /imports/{id}/stream``. Rows map headers to canonical keys (or ``mapping``), merge on dedupe_key,
    and skip the project's exclusions and what the organization's lists already hold."""
    content, chosen = await request.body(), _mapping(mapping)
    try:
        await asyncio.to_thread(read_csv, content, entity, chosen)
    except CsvError as e:
        raise api_error(422, e.code, str(e)) from None
    import_id = await asyncio.to_thread(_queue, user, entity, name.strip() or 'CSV', project_id, chosen, content)
    return await asyncio.to_thread(_state, user, import_id)


@router.get('/imports/active', response_model=CsvImportState | None)
def active_import(user: CurrentUser = Auth, project_id: str = Depends(current_project_or_api_key)) -> dict | None:
    """The newest import of the active project still running, or finished and not yet marked seen: what the
    dialog reopens on."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM csv_imports WHERE organization_id = %s AND user_id = %s AND project_id = %s AND created_at > now() - interval '1 day'"
            " AND (status IN ('queued', 'running') OR seen_at IS NULL) ORDER BY created_at DESC LIMIT 1",
            (user.org_id, user.id, project_id),
        ).fetchone()
    return _state(user, row['id']) if row else None


@router.get('/imports/{import_id}', response_model=CsvImportState)
def get_import(import_id: UUID, user: CurrentUser = Auth) -> dict:
    return _state(user, import_id)


@router.post('/imports/{import_id}/seen', status_code=204)
def mark_seen(import_id: UUID, user: CurrentUser = Auth) -> Response:
    with get_conn() as conn:
        conn.execute(
            'UPDATE csv_imports SET seen_at = now() WHERE id = %s AND organization_id = %s AND user_id = %s',
            (import_id, user.org_id, user.id),
        )
    return Response(status_code=204)


@router.get(
    '/imports/{import_id}/discarded.csv', response_class=Response, responses={200: {'content': {'text/csv': {}}}}
)
def discarded_rows(import_id: UUID, user: CurrentUser = Auth) -> Response:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT name, discarded FROM csv_imports WHERE id = %s AND organization_id = %s AND user_id = %s',
            (import_id, user.org_id, user.id),
        ).fetchone()
    if not row:
        raise _not_found()
    name = f'{row["name"]} - scartate.csv'
    fallback = name.encode('ascii', 'replace').decode()
    return Response(
        row['discarded'] or '',
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name)}'},
    )


async def _frames(import_id: UUID, after: str):
    async with aclosing(events.read_stream(stream_key(str(import_id)), after)) as entries:
        async for item in entries:
            if item is None:
                yield ': ping\n\n'
                continue
            entry_id, fields = item
            yield f'event: {fields["event"]}\nid: {entry_id}\ndata: {fields["data"]}\n\n'
            if fields['event'] in TERMINAL:
                return


@router.get('/imports/{import_id}/stream', response_class=EventStream)
async def import_stream(
    import_id: UUID, user: CurrentUser = Auth, last_event_id: str | None = Header(default=None)
) -> EventStream:
    """SSE: ``progress``, then ``done`` or ``failed`` (which ends the stream) after ``Last-Event-ID``
    (else from the start), ``: ping`` every 15 s."""
    await asyncio.to_thread(_state, user, import_id)  # 404 unless the caller's own
    return EventStream(_frames(import_id, events.entry_id(last_event_id) or '0-0'))
