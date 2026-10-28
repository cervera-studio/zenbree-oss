"""The body of ``POST /v1/chat`` and ``POST /v1/runs`` (docs/contracts-m1.md, Chat and runs)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.agent import plan as plans
from app.core.locale import Locale
from app.core.segment_key import SEGMENT_KEY


class PlanApproval(BaseModel):
    """The user approves the latest plan of a list for ``rows`` rows (#44 #47)."""

    list_id: UUID
    version: int
    rows: int = Field(ge=1, le=plans.MAX_PLAN_ROWS)
    dropped_stages: list[int] = Field(default_factory=list, max_length=plans.MAX_STAGES)


class StageRetry(BaseModel):
    job_id: UUID
    stage: int = Field(ge=0)


class ChatRequest(BaseModel):
    message: str = Field(default='', max_length=20_000)  # required unless `segment` writes it
    session_id: UUID | None = None
    project_id: UUID | None = None  # new sessions only; default project when absent
    goal: Literal['campaign'] | None = None  # new sessions only; a later turn never changes it
    plan_approval: PlanApproval | None = None  # this turn runs that plan
    pick: int | None = Field(default=None, ge=0)  # index of the funnel alternative this message picks
    retry_stage: StageRetry | None = None  # this turn retries a failed stage of the last turn's plan
    # the language the user sees: this turn writes in it, never saved on the user (#795); None = the saved one
    locale: Locale | None = None
    # new sessions only: open the chat with the project's stored onboarding segment of this key (a Home welcome
    # card), as onboarding's first list; the server writes the message, the title and the brief the agent reads
    segment: str | None = Field(default=None, pattern=SEGMENT_KEY)

    @model_validator(mode='after')
    def _one_plan_action(self) -> ChatRequest:
        if self.retry_stage and (self.plan_approval or not self.session_id):
            raise ValueError('retry_stage needs a session and cannot come with plan_approval')
        if bool(self.message) == (self.segment is not None):
            raise ValueError('send either a message or a segment')
        # a segment sent on a later turn would be dropped with its filters, so it fails instead
        if self.session_id and self.segment is not None:
            raise ValueError('segment opens a new session and cannot come with session_id')
        return self
