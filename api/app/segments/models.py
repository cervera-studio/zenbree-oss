from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from app.segments.evaluate import Applied, MemberRow
from app.segments.rules import Condition, Entity, RuleGroup


class RuleProblem(BaseModel):
    """What a saved rule names that is gone: the segment is frozen until an edit fixes it."""

    path: str
    reason: Literal['list_not_found', 'column_not_found']
    list_id: UUID
    key: str | None


class AudienceSegment(BaseModel):
    id: UUID
    name: str
    entity: Entity
    rule: RuleGroup
    version: int
    published_at: datetime | None
    created_at: datetime
    created_by: UUID | None
    members: int
    problems: list[RuleProblem]


class AudienceSegments(BaseModel):
    segments: list[AudienceSegment]


class Published(BaseModel):
    """``max_credits``: the most the automations that read the segment can spend if everyone who joined entered."""

    segment: AudienceSegment
    applied: Applied
    max_credits: int


class Edited(BaseModel):
    """A rule edit of a published segment: ``joining`` people it added and ``leaving`` people it dropped. A draft or
    a rename changes no member. ``max_credits`` as in ``Published``, for the people joining."""

    segment: AudienceSegment
    joining: int
    leaving: int
    max_credits: int


class SampleMember(BaseModel):
    identity_id: UUID
    row: MemberRow | None


class SegmentPreview(BaseModel):
    """With a ``segment_id``, ``joining``, ``leaving`` and ``max_credits`` are what a ``PATCH`` of the segment to this
    rule would answer now, as ``Edited``; without one, they are null."""

    count: int
    pending: int
    unidentified_rows: int
    linking_rows: int
    sample: list[SampleMember]
    joining: int | None = None
    leaving: int | None = None
    max_credits: int | None = None


class Member(BaseModel):
    identity_id: UUID
    episode: int
    entered_at: datetime
    row: MemberRow | None


class Members(BaseModel):
    members: list[Member]
    next_cursor: UUID | None


class CellSource(BaseModel):
    """The web page an AI fill took its value from, as ``cell_meta[key].source`` stores it."""

    url: str
    title: str


class ConditionEvidence(BaseModel):
    """A row of the person that has the condition's field; ``hit`` when it satisfies the condition's positive form.
    A cell carries its fill record (``at``, ``source``, ``status``) so a reader sees how old the value is."""

    list_id: UUID
    list_name: str
    row_id: UUID
    hit: bool
    value: Any = None
    at: str | None = None
    source: CellSource | None = None
    status: str | None = None
    relevance_score: float | None = None


class WhyNode(BaseModel):
    path: str
    type: Literal['group', 'condition']
    value: bool
    op: Literal['and', 'or'] | None = None
    children: list[WhyNode] = []
    condition: Condition | None = None
    rows: list[ConditionEvidence] = []


class Why(BaseModel):
    """``problems`` are the segment's: when there are any, the segment is frozen, so ``member`` can differ from
    ``matches``, which judges a rule that names a deleted list or column."""

    identity_id: UUID
    matches: bool
    pending: bool
    member: bool
    rule: WhyNode
    problems: list[RuleProblem]
