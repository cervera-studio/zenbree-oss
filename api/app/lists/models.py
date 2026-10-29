"""The row and column shapes the lists API sends."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from app.lists.lookups import Lookups


class ScoreReason(BaseModel):
    match: str
    category: Literal['firmographics', 'website', 'hiring', 'news', 'technology', 'contact', 'other']
    freshness: str | None
    key: str | None


class RowPerson(BaseModel):
    row_id: UUID
    list_id: UUID
    full_name: str | None
    title: str | None


class CrmHold(BaseModel):
    reason: str
    until: str | None  # ISO timestamp as Postgres writes it in the JSON


class ListRow(BaseModel):
    id: UUID
    seq: int
    cells: dict[str, Any]
    cell_meta: dict[str, Any]
    relevance_score: Decimal | None
    score_reasons: list[ScoreReason] | None
    excluded: bool
    rating: Literal['good', 'bad'] | None
    rating_note: str | None
    created_at: datetime
    delivered: bool
    crm_hold: CrmHold | None = None  # #120: why the HubSpot read gate holds this person now
    people_count: int | None = None
    people: list[RowPerson] | None = None
    lookups: Lookups  # app.lists.row_view.with_lookups


class ListColumn(BaseModel):
    key: str
    label: str
    kind: Literal['ai', 'code', 'score', 'static']
    position: int
    config: dict[str, Any]
