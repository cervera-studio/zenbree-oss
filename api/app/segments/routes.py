"""Segments (#938): saved rules over the people or accounts of the active project, and their members.

Every route runs in the request's scope, so row-level security holds it to the caller's organization, and reads only
the active project. A rule is checked on every write (``rules.parse``); evaluation runs under the project's lock."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, GetJsonSchemaHandler, StringConstraints
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from app.admin.settings import is_disabled
from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.projects.routes import current_project_or_api_key
from app.segments import drain, evaluate
from app.segments.cost import max_entry_credits
from app.segments.evaluate import JoinKind
from app.segments.models import AudienceSegment, AudienceSegments, Edited, Members, Published, SegmentPreview, Why
from app.segments.rules import Entity, Problem, Rule, RuleError, RuleGroup, parse, problems, problems_of

router = APIRouter(prefix='/v1/segments', tags=['Segments'])
Auth = Depends(current_user_or_api_key)
Project = Depends(current_project_or_api_key)
MAX_PER_PROJECT = 50

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class _RuleGroupSchema:
    """Documents a raw rule as a ``RuleGroup``. ``rules.parse`` checks it, not the request model, so a bad rule is
    422 SEGMENT.INVALID_RULE with its path."""

    @classmethod
    def __get_pydantic_json_schema__(cls, _schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        return handler(RuleGroup.__pydantic_core_schema__)


RawRule = Annotated[dict[str, Any], _RuleGroupSchema]


class PreviewIn(BaseModel):
    entity: Entity
    rule: RawRule
    segment_id: UUID | None = None


class SegmentIn(BaseModel):
    name: Name
    entity: Entity
    rule: RawRule


class SegmentEdit(BaseModel):
    version: int
    name: Name | None = None
    rule: RawRule | None = None
    include_new: bool = False


class PublishIn(BaseModel):
    include_current: bool = False


def _paused() -> HTTPException:
    return api_error(503, 'SEGMENT.EVALUATION_PAUSED', 'segment evaluation is paused: try again later')


def _evaluation_open() -> None:
    """The pause covers every request that writes members or previews a rule. ``why`` still answers: it writes
    nothing. Read it before taking a lock: ``is_disabled`` may need a second connection from the pool."""
    if is_disabled(drain.PAUSED):
        raise _paused()


def _join_kind(include: bool) -> JoinKind:
    return 'enter' if include else 'baseline'


_FIELDS = 'id, organization_id, project_id, created_by, name, entity, rule, version, published_at, created_at'


def _row(conn, segment_id: UUID, project_id: str, lock: bool = False) -> dict:
    row = conn.execute(
        f'SELECT {_FIELDS} FROM segments WHERE id = %s AND project_id = %s AND deleted_at IS NULL'
        + (' FOR NO KEY UPDATE' if lock else ''),
        (segment_id, project_id),
    ).fetchone()
    if not row:
        raise api_error(404, 'SEGMENT.NOT_FOUND', 'segment not found')
    return row


def _counted(conn, row: dict) -> dict:
    """The row with its live member count. An evaluation knows the count, so its routes skip this read."""
    n = conn.execute(
        'SELECT count(*) AS n FROM segment_members WHERE segment_id = %s AND exited_at IS NULL', (row['id'],)
    ).fetchone()['n']
    return {**row, 'members': n}


def _segment(row: dict, found: list[Problem]) -> AudienceSegment:
    return AudienceSegment.model_validate({**row, 'problems': [asdict(p) for p in found]})


def _out(conn, row: dict) -> AudienceSegment:
    return _segment(row, problems(conn, row['project_id'], row['entity'], RuleGroup.model_validate(row['rule'])))


def _parse(conn, project_id: str, entity: Entity, raw: dict) -> RuleGroup:
    try:
        return parse(conn, project_id, entity, raw).root
    except RuleError as e:
        raise api_error(422, 'SEGMENT.INVALID_RULE', str(e), path=e.path, reason=e.reason) from None


def _stored(root: RuleGroup) -> Jsonb:
    return Jsonb(root.model_dump(mode='json'))


@router.get('', response_model=AudienceSegments)
def list_segments(project_id: str = Project) -> AudienceSegments:
    with get_conn() as conn:
        rows = conn.execute(
            f'SELECT {_FIELDS} FROM segments WHERE project_id = %s AND deleted_at IS NULL ORDER BY created_at DESC',
            (project_id,),
        ).fetchall()
        live = conn.execute(
            'SELECT segment_id, count(*) AS n FROM segment_members WHERE segment_id = ANY(%s) AND exited_at IS NULL'
            ' GROUP BY segment_id',
            ([r['id'] for r in rows],),
        ).fetchall()
        members = {r['segment_id']: r['n'] for r in live}
        found = problems_of(conn, project_id, [(r['entity'], RuleGroup.model_validate(r['rule'])) for r in rows])
        return AudienceSegments(
            segments=[_segment({**r, 'members': members.get(r['id'], 0)}, f) for r, f in zip(rows, found, strict=True)]
        )


@router.post('/preview', response_model=SegmentPreview)
def preview_rule(body: PreviewIn, project_id: str = Project) -> SegmentPreview:
    """Who matches the rule now. With ``segment_id``, also what an edit of that segment to this rule would change,
    with no write and no lock."""
    _evaluation_open()
    with get_conn() as conn:
        segment = _row(conn, body.segment_id, project_id) if body.segment_id is not None else None
        if segment is not None and segment['entity'] != body.entity:
            raise api_error(
                422,
                'SEGMENT.INVALID_RULE',
                "the rule is not of the segment's entity",
                path='',
                reason='entity_mismatch',
            )
        rule = Rule(body.entity, _parse(conn, project_id, body.entity, body.rule))
        if segment is None:
            return SegmentPreview.model_validate(evaluate.preview(conn, project_id, rule))
        if segment['published_at'] is None:
            found = {
                **evaluate.preview(conn, project_id, rule),
                'joining': 0,
                'leaving': 0,
            }  # as a draft's edit answers
        else:
            found = evaluate.preview(conn, project_id, rule, was=segment)
        return SegmentPreview.model_validate(
            {**found, 'max_credits': max_entry_credits(conn, segment['id'], found['joining'])}
        )


@router.post('', response_model=AudienceSegment, status_code=201)
def create_segment(body: SegmentIn, user: CurrentUser = Auth, project_id: str = Project) -> AudienceSegment:
    with get_conn() as conn:
        # One project's creates take turns, so the cap below holds under concurrency.
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        have = conn.execute(
            'SELECT count(*) AS n FROM segments WHERE project_id = %s AND deleted_at IS NULL', (project_id,)
        ).fetchone()['n']
        if have >= MAX_PER_PROJECT:
            raise api_error(409, 'SEGMENT.LIMIT_REACHED', f'at most {MAX_PER_PROJECT} segments per project')
        root = _parse(conn, project_id, body.entity, body.rule)
        new = conn.execute(
            'INSERT INTO segments (organization_id, project_id, created_by, name, entity, rule)'
            f' VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_FIELDS}',
            (user.org_id, project_id, user.id, body.name, body.entity, _stored(root)),
        ).fetchone()
        return _out(conn, {**new, 'members': 0})


@router.get('/{segment_id}', response_model=AudienceSegment)
def get_segment(segment_id: UUID, project_id: str = Project) -> AudienceSegment:
    with get_conn() as conn:
        return _out(conn, _counted(conn, _row(conn, segment_id, project_id)))


@router.patch('/{segment_id}', response_model=Edited)
def edit_segment(segment_id: UUID, body: SegmentEdit, project_id: str = Project) -> Edited:
    """Rename or change the rule of the version the caller read. A new rule of a published segment applies at once:
    the people it adds join, as entries only with ``include_new``, and the people it drops exit. The old rule is
    judged in the same statement, so a change the data made since the last evaluation stays the data's: the edit's
    answer decides only the edit's own joins."""
    paused = body.rule is not None and is_disabled(drain.PAUSED)
    with get_conn() as conn:
        evaluate.lock_project(conn, project_id)
        was = _row(conn, segment_id, project_id, lock=True)
        if was['version'] != body.version:
            raise api_error(
                409, 'SEGMENT.STALE_VERSION', 'the segment changed since it was read', version=was['version']
            )
        if paused and was['published_at'] is not None:
            raise _paused()
        root = _parse(conn, project_id, was['entity'], body.rule) if body.rule is not None else None
        row = conn.execute(
            'UPDATE segments SET name = coalesce(%(name)s, name), rule = coalesce(%(rule)s, rule),'
            ' version = version + 1, full_due_at = CASE WHEN %(rule)s::jsonb IS NOT NULL AND published_at IS NOT NULL'
            f' THEN now() + %(catch_up)s ELSE full_due_at END WHERE id = %(id)s RETURNING {_FIELDS}',
            {'name': body.name, 'rule': root and _stored(root), 'catch_up': drain.CATCH_UP, 'id': segment_id},
        ).fetchone()
        if root is None or row['published_at'] is None:
            return Edited(
                segment=_out(conn, _counted(conn, row)),
                joining=0,
                leaving=0,
                max_credits=max_entry_credits(conn, segment_id, 0),
            )
        done = evaluate.apply(conn, row, _join_kind(body.include_new), was=was)
        return Edited(
            segment=_out(conn, {**row, 'members': done.applied.members}),
            joining=done.joining,
            leaving=done.leaving,
            max_credits=max_entry_credits(conn, segment_id, done.joining),
        )


@router.delete('/{segment_id}', status_code=204)
def delete_segment(segment_id: UUID, project_id: str = Project) -> None:
    with get_conn() as conn:
        n = conn.execute(
            'UPDATE segments SET deleted_at = now() WHERE id = %s AND project_id = %s AND deleted_at IS NULL',
            (segment_id, project_id),
        ).rowcount
    if not n:
        raise api_error(404, 'SEGMENT.NOT_FOUND', 'segment not found')


def _evaluated(conn, row: dict, join_kind: JoinKind) -> Published:
    applied = evaluate.apply(conn, row, join_kind).applied
    return Published(
        segment=_out(conn, {**row, 'members': applied.members}),
        applied=applied,
        max_credits=max_entry_credits(conn, row['id'], applied.joined),
    )


@router.post('/{segment_id}/publish', response_model=Published)
def publish_segment(segment_id: UUID, body: PublishIn | None = None, project_id: str = Project) -> Published:
    """Start keeping members. Everyone who matches now joins: as entries with ``include_current``, else silently.
    Publishing again only refreshes."""
    _evaluation_open()
    with get_conn() as conn:
        # The project's lock before the row's, in the order an edit takes them.
        evaluate.lock_project(conn, project_id)
        row = _row(conn, segment_id, project_id, lock=True)
        if row['published_at'] is not None:
            return _evaluated(conn, row, 'enter')
        row = conn.execute(
            f'UPDATE segments SET published_at = now(), full_due_at = now() + %s WHERE id = %s RETURNING {_FIELDS}',
            (drain.CATCH_UP, segment_id),
        )
        return _evaluated(conn, row.fetchone(), _join_kind(body is not None and body.include_current))


def _published(conn, segment_id: UUID, project_id: str) -> dict:
    row = _row(conn, segment_id, project_id)
    if row['published_at'] is None:
        raise api_error(409, 'SEGMENT.NOT_PUBLISHED', 'a draft has no members: publish it first')
    return row


@router.post('/{segment_id}/refresh', response_model=Published)
def refresh_segment(segment_id: UUID, project_id: str = Project) -> Published:
    _evaluation_open()
    with get_conn() as conn:
        evaluate.lock_project(conn, project_id)
        return _evaluated(conn, _published(conn, segment_id, project_id), 'enter')


@router.get('/{segment_id}/members', response_model=Members)
def list_members(
    segment_id: UUID, after: UUID | None = None, limit: int = Query(50, ge=1, le=200), project_id: str = Project
) -> Members:
    with get_conn() as conn:
        _published(conn, segment_id, project_id)
        return Members.model_validate(evaluate.members(conn, segment_id, after, limit))


@router.get('/{segment_id}/members/{identity_id}/why', response_model=Why)
def why_member(segment_id: UUID, identity_id: UUID, project_id: str = Project) -> Why:
    """How the rule judges this person now, condition by condition, member or not."""
    with get_conn() as conn:
        row = _row(conn, segment_id, project_id)
        if not conn.execute(
            'SELECT 1 FROM audience_identities WHERE id = %s AND project_id = %s AND entity = %s',
            (identity_id, project_id, row['entity']),
        ).fetchone():
            raise api_error(404, 'SEGMENT.IDENTITY_NOT_FOUND', 'no such person or account in this project')
        found = problems(conn, project_id, row['entity'], RuleGroup.model_validate(row['rule']))
        return Why.model_validate({**evaluate.why(conn, row, identity_id), 'problems': [asdict(p) for p in found]})
