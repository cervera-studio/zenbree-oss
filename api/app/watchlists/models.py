"""Request and response shapes of watchlists and signals (E13.3, #647)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JobTitle = Annotated[str, Field(min_length=1, max_length=200)]
Cadence = Literal['daily', 'weekly']


def _titles(value: list[str]) -> list[str]:
    """Trimmed, without blanks or repeats; a watchlist that watches nothing is refused."""
    seen = list(dict.fromkeys(t.strip() for t in value if t.strip()))
    if not seen:
        raise ValueError('name at least one function to watch')
    return seen


def _campaign_pair(model):
    if (model.campaign_id is None) != (model.campaign_threshold is None):
        raise ValueError('name a campaign and a score threshold together')
    return model


class WatchlistIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    list_id: UUID
    job_titles: Annotated[list[JobTitle], Field(min_length=1, max_length=20)]
    cadence: Cadence = 'weekly'
    enabled: bool = True
    campaign_id: UUID | None = None
    campaign_threshold: float | None = Field(default=None, gt=0, le=10000)

    _clean = field_validator('job_titles')(_titles)
    _pair = model_validator(mode='after')(_campaign_pair)


class WatchlistPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=120)
    job_titles: Annotated[list[JobTitle], Field(min_length=1, max_length=20)] | None = None
    cadence: Cadence | None = None
    enabled: bool | None = None
    # Sent as null: stop adding people (both go together).
    campaign_id: UUID | None = None
    campaign_threshold: float | None = Field(default=None, gt=0, le=10000)

    _clean = field_validator('job_titles')(lambda v: v if v is None else _titles(v))


class WatchlistRun(BaseModel):
    id: UUID
    watchlist_id: UUID
    scheduled_for: datetime
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown']
    credits_spent: int
    accounts_checked: int
    accounts_skipped: int
    accounts_left: int
    signals_new: int
    people_enrolled: int  # added to the watchlist's campaign by this run
    accounts_without_people: int  # reached the threshold, no known person in a person list: they add nobody
    people_left_out: int  # of those accounts' people, kept out by a rule, the limit or another campaign
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class Watchlist(BaseModel):
    id: UUID
    organization_id: UUID
    user_id: UUID | None  # who made it; runs spend as them
    name: str
    list_id: UUID
    list_name: str
    job_titles: list[str]
    cadence: Cadence
    next_run_at: datetime
    enabled: bool
    campaign_id: UUID | None  # signals add the account's people to it
    campaign_threshold: float | None
    created_at: datetime
    updated_at: datetime
    accounts: int
    signals: int
    last_run: WatchlistRun | None


class Watchlists(BaseModel):
    watchlists: list[Watchlist]


class WatchlistRuns(BaseModel):
    runs: list[WatchlistRun]


class Signal(BaseModel):
    id: UUID
    type: Literal['job_posting']
    row_id: UUID
    account_name: str
    account_domain: str
    title: str
    city: str | None
    country: str | None
    url: str | None
    occurred_at: datetime
    first_seen_at: datetime
    score: float


class Signals(BaseModel):
    signals: list[Signal]
