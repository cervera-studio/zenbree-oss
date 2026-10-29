from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, max_length=200)]
Terms = Annotated[list[Text], Field(max_length=20)]


class CompanyArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    locations: Terms = []
    employee_ranges: Terms = []
    industries: Terms = []
    keywords: Terms = []
    page: int = Field(default=1, ge=1, le=100)
    local_query: Text | None = None
    local_location: Text | None = None
    country_code: str | None = Field(default=None, pattern=r'^[a-zA-Z]{2}$')
    limit: int = Field(default=20, ge=1, le=60)


class PeopleArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    titles: Terms = []
    seniorities: Terms = []
    locations: Terms = []
    company_domains: Terms = []
    company_name: Text | None = None
    employee_ranges: Terms = []
    employee_size: (
        Annotated[list[Annotated[int, Field(ge=0, le=10000000)]], Field(min_length=2, max_length=2)] | None
    ) = None
    industries: Terms = []
    keywords: Terms = []
    source: Literal['apollo', 'aiark'] = 'apollo'
    page: int = Field(default=1, ge=1, le=100)

    @model_validator(mode='after')
    def ordered_size(self):
        if self.employee_size and self.employee_size[0] > self.employee_size[1]:
            raise ValueError('employee_size must be ordered')
        return self


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    tool_name: Literal['search_people', 'search_companies']
    tool_input: dict = Field(default_factory=dict)
    list_id: UUID
    campaign_id: UUID | None = None
    cadence: Literal['daily', 'weekly'] = 'weekly'
    next_run_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    enabled: bool = True
    # One invocation currently costs at most ONE Zenbree credit, not vendor USD.
    budget_credits_per_run: Literal[1] = 1

    @field_validator('budget_credits_per_run', mode='before')
    @classmethod
    def one_credit(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('Each saved search has a fixed maximum cost of 1 credit')
        return value

    @field_validator('next_run_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('next_run_at needs a timezone')
        return value.astimezone(UTC)

    @model_validator(mode='after')
    def arguments(self):
        if self.tool_name == 'search_companies' and self.campaign_id:
            raise ValueError('Only person searches can refill campaigns')
        model = PeopleArgs if self.tool_name == 'search_people' else CompanyArgs
        self.tool_input = model.model_validate(self.tool_input).model_dump(exclude_none=True)
        return self


class Toggle(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool


class SavedSearch(BaseModel):
    id: UUID
    organization_id: UUID
    user_id: UUID | None
    name: str
    tool_name: Literal['search_people', 'search_companies']
    tool_input: dict[str, Any]
    list_id: UUID
    campaign_id: UUID | None
    cadence: Literal['daily', 'weekly']
    next_run_at: datetime
    enabled: bool
    budget_credits_per_run: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class SavedSearches(BaseModel):
    saved_searches: list[SavedSearch]


class SavedSearchRun(BaseModel):
    id: UUID
    organization_id: UUID
    saved_search_id: UUID
    user_id: UUID | None
    scheduled_for: datetime
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown']
    config: dict[str, Any]
    reservation_id: UUID | None
    credits_spent: int
    new_row_ids: list[UUID]
    pending_row_ids: list[UUID]
    refill_checked_at: datetime | None
    refill_done: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None


class SavedSearchRuns(BaseModel):
    runs: list[SavedSearchRun]
