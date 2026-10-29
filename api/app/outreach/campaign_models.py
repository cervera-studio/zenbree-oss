from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.outreach.domain.campaign import Channel, StepCopy
from app.outreach.fields import CampaignField, UncheckedFields

CampaignStatus = Literal['draft', 'active', 'paused', 'stopped', 'completed']


class SequenceStep(BaseModel):
    step: int
    channel: Channel
    subject: str = ''
    body: str
    send_after_days: int
    personalised: str = ''
    threaded: bool = False
    # Variant B's copy of this step (#934); absent: B people get A's copy.
    b: StepCopy | None = None


class CampaignSummary(BaseModel):
    id: UUID
    project_id: UUID
    owner_id: UUID | None
    name: str
    status: CampaignStatus
    approve_before_sending: bool
    block_active_duplicates: bool
    created_at: datetime
    activated_at: datetime | None
    target_count: int
    replied: int
    replied_contacted: int
    sent: int
    failed: int
    contacted: int
    awaiting_approval: int
    awaiting_approval_held: int
    list_name: str | None
    step_channels: list[Channel]
    planned_sends: int
    steps_done: int
    list_id: UUID | None
    chat_session_id: UUID | None


class Campaign(CampaignSummary):
    steps: list[SequenceStep]
    fields: list[CampaignField]
    send_window_start_hour: int
    send_window_end_hour: int
    send_window_days: list[int]
    # B's share of the people who join a campaign whose steps carry a B copy; set in the draft.
    b_percent: int
    # Set: people who join from then on get copy A and stay outside the test.
    allocation_paused_at: datetime | None
    sent_today: int
    interested: int
    auto_replies: int
    invalid_email: int
    unread_replies: int
    daily_cap: int
    linkedin_risk_notice: str


class Campaigns(BaseModel):
    campaigns: list[CampaignSummary]


class CreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    list_id: UUID
    block_active_duplicates: bool = True


class PatchIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    steps: list[dict] | None = None
    fields: UncheckedFields | None = None
    block_active_duplicates: bool | None = None
