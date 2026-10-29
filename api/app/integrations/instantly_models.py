"""Request and response models of the Instantly routes (#929); the routes are in :mod:`app.integrations.instantly`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.integrations import instantly_enroll
from app.integrations.instantly_fields import Mapping


class InstantlySync(BaseModel):
    """How the campaign events of the workspace reach Zenbree (#930)."""

    # live: the webhook delivers and a slow pass checks it. polled: no webhook delivers, so events arrive within
    # minutes. paused: the kill switch is on. off: not connected.
    mode: Literal['live', 'polled', 'paused', 'off']
    webhook: Literal['creating', 'registered', 'live', 'degraded'] | None  # None: Zenbree has no webhook there
    webhook_refusal: Literal['plan', 'scope', 'refused', 'no_public_url'] | None
    emails_refused: bool  # the key may not read emails: sends and replies come from the webhook alone, unclassified
    complete_until: datetime | None  # every event up to here is recorded; None before a first full pass
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error_code: str | None
    failing_since: datetime | None


class InstantlyStatus(BaseModel):
    connected: bool
    status: Literal['connected', 'error', 'disconnected']
    name: str | None
    workspace_name: str | None
    status_detail: str | None
    configured: bool  # the API can encrypt a key
    can_manage: bool  # this member may replace or disconnect (who connected it, or an admin)
    sync: InstantlySync | None  # None when the project never connected Instantly


class InstantlyConnectIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    # SecretStr keeps the key out of repr, validation errors and logs.
    api_key: SecretStr = Field(min_length=1, max_length=500)


class CampaignOut(BaseModel):
    id: str
    name: str
    status: int  # Instantly's number: 0 draft, 1 active, 2 paused, 3 completed
    status_label: str
    may_send: bool  # running: adding people may start emails under the campaign's own settings


class CampaignsOut(BaseModel):
    campaigns: list[CampaignOut]


AccountsState = Literal['ok', 'not_connected', 'no_permission', 'unavailable']


class InstantlyAccountOut(BaseModel):
    email: str
    status: Literal[
        'active', 'paused', 'maintenance', 'connection_error', 'soft_bounce_error', 'sending_error', 'unknown'
    ]
    warmup: Literal['active', 'paused', 'banned', 'spam_folder_unknown', 'permanent_suspension', 'unknown']
    daily_limit: int | None  # Instantly's own limit; None when the account has none set


class InstantlyAccountsOut(BaseModel):
    # not_connected: no connected key. no_permission: Instantly refused the key (no accounts:read).
    # unavailable: the kill switch is on, or Instantly did not answer now.
    state: AccountsState
    accounts: list[InstantlyAccountOut]  # empty unless state is ok


class EnrollIn(BaseModel):
    list_id: UUID
    row_ids: list[UUID] = Field(min_length=1, max_length=instantly_enroll.MAX_ROWS)
    campaign_id: UUID
    mapping: Mapping = Field(default_factory=Mapping)


class PreviewRow(BaseModel):
    row_id: UUID
    email: str | None
    reason: str | None  # None: it would go; else why not (see instantly_verdicts)


class PreviewLead(BaseModel):
    row_id: UUID
    lead: dict[str, object]  # exactly what would be sent for the row


class PreviewCampaign(BaseModel):
    id: str
    name: str
    status: int
    may_send: bool
    url: str


class PreviewOut(BaseModel):
    campaign: PreviewCampaign
    selected: int
    eligible: int
    skipped: dict[str, int]
    rows: list[PreviewRow]
    sample: list[PreviewLead]
    custom_variables: list[str]  # also added to the campaign's variables in Instantly


class ConfirmIn(EnrollIn):
    confirm: bool = False
    # The campaign is running in Instantly: adding people may start sending. Asked again here, from the campaign as it
    # is now, because it can have been started since the preview.
    acknowledge_sending: bool = False


class EnrollmentCounts(BaseModel):
    pending: int
    publishing: int
    accepted: int
    skipped: int
    failed: int
    uncertain: int


class EnrollmentRow(BaseModel):
    row_id: UUID | None
    email: str | None
    outcome: Literal['pending', 'publishing', 'accepted', 'skipped', 'failed', 'uncertain']
    reason: str | None


class EnrollmentCampaign(BaseModel):
    id: str
    name: str
    url: str


class EnrollmentOut(BaseModel):
    id: UUID
    status: Literal['queued', 'running', 'completed', 'cancelled', 'failed']
    error_code: str | None
    campaign: EnrollmentCampaign
    created_at: datetime
    finished_at: datetime | None
    counts: EnrollmentCounts
    cancel_requested: bool
    can_cancel: bool
    can_retry: bool
    rows: list[EnrollmentRow]
