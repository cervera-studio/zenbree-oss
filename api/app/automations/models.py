"""Request and response shapes of automations (#939), and the definition they store.

A definition is a bounded, ordered list of steps over one person list. The steps run in one fixed order, each at most
once, so the stored shape is data and no graph or script. No step sends a message or activates a campaign.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.automations.triggers import (
    AutomationPreviewSignals,
    AutomationSignal,
    ManualTrigger,
    RunSignal,
    Trigger,
    TriggerKind,
)

StepKind = Literal['find_people', 'qualify', 'find_emails', 'add_to_campaign', 'add_to_instantly', 'write_hubspot']
# The order the steps run in: each one needs what the one before it made.
ORDER: tuple[StepKind, ...] = (
    'find_people',
    'qualify',
    'find_emails',
    'add_to_campaign',
    'add_to_instantly',
    'write_hubspot',
)
# The most credits a run may spend (the watchlist's CREDIT_CAP).
MAX_CREDIT_CAP = 100
Text = Annotated[str, Field(min_length=1, max_length=200)]


def _trimmed(value: str) -> str:
    if not value.strip():
        raise ValueError('name the automation')
    return value.strip()


class _Step(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FindPeople(_Step):
    """The people of the accounts of a company list, as Apollo previews them: free."""

    type: Literal['find_people']
    accounts_list_id: UUID
    titles: Annotated[list[Text], Field(min_length=1, max_length=10)]
    seniorities: Annotated[list[Text], Field(max_length=10)] = []
    per_account: int = Field(default=3, ge=1, le=5)


class Qualify(_Step):
    """Scores each person 0 to 100 against ``brief``: 1 credit a person. Lower than ``min_score`` stops there."""

    type: Literal['qualify']
    brief: str = Field(min_length=1, max_length=1000)
    min_score: int = Field(default=50, ge=0, le=100)


class FindEmails(_Step):
    """A verified work email for a person who has none: 1 credit when one is found."""

    type: Literal['find_emails']


class AddToCampaign(_Step):
    """Adds the people to a campaign that has not ended. Never activates it, never sends."""

    type: Literal['add_to_campaign']
    campaign_id: UUID


class AddToInstantly(_Step):
    """Hands the people to a campaign of the project's Instantly workspace, with the default field mapping. Never
    activates it. The batch is made when the run ends, or at its approval when the run is in review
    (``app.automations.instantly_handoff``)."""

    type: Literal['add_to_instantly']
    campaign_id: UUID


class WriteHubspot(_Step):
    """Writes the contact and its company to the owner's HubSpot, when write-back is on."""

    type: Literal['write_hubspot']


Step = Annotated[
    FindPeople | Qualify | FindEmails | AddToCampaign | AddToInstantly | WriteHubspot, Field(discriminator='type')
]


class Definition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    list_id: UUID  # the person list the automation works on; found people land here
    steps: Annotated[list[Step], Field(min_length=1, max_length=len(ORDER))]

    @model_validator(mode='after')
    def _ordered(self):
        kinds = [s.type for s in self.steps]
        if kinds != sorted(set(kinds), key=ORDER.index):
            raise ValueError(f'steps run in this order, each at most once: {", ".join(ORDER)}')
        # One system owns a person's outreach (#931): a run adds people to Zenbree or to Instantly.
        if {'add_to_campaign', 'add_to_instantly'} <= set(kinds):
            raise ValueError('a run adds people to a Zenbree campaign or to an Instantly campaign, not both')
        return self

    def step(self, kind: StepKind):
        return next((s for s in self.steps if s.type == kind), None)

    @property
    def spends(self) -> bool:
        return any(s.type in ('qualify', 'find_emails') for s in self.steps)


class AutomationIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    trigger: Trigger = ManualTrigger()
    definition: Definition
    credit_cap: int = Field(ge=1, le=MAX_CREDIT_CAP)
    enabled: bool = True

    _name = field_validator('name')(_trimmed)


class AutomationPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=120)
    trigger: Trigger | None = None
    definition: Definition | None = None
    credit_cap: int | None = Field(default=None, ge=1, le=MAX_CREDIT_CAP)
    enabled: bool | None = None


class RunStep(BaseModel):
    position: int
    kind: StepKind
    status: Literal['pending', 'running', 'succeeded', 'skipped', 'failed', 'uncertain']
    decision: str | None
    input: dict
    output: dict
    credits: int
    attempts: int
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None


class AutomationRun(BaseModel):
    id: UUID
    automation_id: UUID
    version: int
    trigger_kind: TriggerKind
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown']
    cancel_requested: bool
    credit_cap: int
    credits_spent: int
    review: bool | None  # its people enter the campaign as draft until the user approves the run
    approved_at: datetime | None
    summary: dict
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDelivery(BaseModel):
    """The signed call that started a webhook run: never the email or the URL it named."""

    id: UUID
    webhook_id: str
    received_at: datetime
    matched_by: Literal['linkedin_url', 'email']
    row_id: UUID | None  # None once the row was deleted


class InstantlyCampaignSnapshot(BaseModel):
    """An Instantly campaign as the step read it. ``may_send``: it runs, so a lead added to it can be sent to."""

    id: UUID
    name: str
    status: int
    may_send: bool


class RunInstantly(BaseModel):
    """The people a succeeded run handed to Instantly, and the batch they went in."""

    people: int
    batch_id: UUID | None  # ``GET /v1/integrations/instantly/enrollments/{batch_id}``; None while they are held
    # Why no batch yet: the run waits for its approval, the automation is paused, its creator is no longer a member,
    # the project's Instantly is not connected or is another workspace, another batch of the project still runs, or
    # nothing (the beat makes it within a minute).
    waiting_for: (
        Literal['approval', 'paused', 'creator_gone', 'connection', 'other_workspace', 'other_batch', 'starting'] | None
    )
    campaign: InstantlyCampaignSnapshot


class AutomationRunDetail(AutomationRun):
    steps: list[RunStep]
    delivery: RunDelivery | None
    instantly: RunInstantly | None  # None: the run held nobody for Instantly
    signal: RunSignal | None  # None: not a signal run


class AutomationWebhook(BaseModel):
    url: str | None  # None while the API has no public base URL
    rotated_at: datetime
    pending: int  # calls accepted and not started yet
    # A run of this version waits for approval: no call starts until the user approves it.
    waiting_for_approval: bool


class WebhookSecret(BaseModel):
    """The only answer that holds the secret: it is never shown again."""

    url: str | None
    secret: str
    rotated_at: datetime


class WebhookAccepted(BaseModel):
    delivery_id: UUID


class InboundPerson(BaseModel):
    """The body of a signed call. Senders add their own fields, so the others are ignored; nothing of it is stored."""

    model_config = ConfigDict(extra='ignore')
    email: str | None = Field(default=None, max_length=320)
    linkedin_url: str | None = Field(default=None, max_length=500)


class Automation(BaseModel):
    id: UUID
    organization_id: UUID
    project_id: UUID
    user_id: UUID | None  # who made it; runs spend as them
    name: str
    trigger_kind: TriggerKind
    cadence: Literal['daily', 'weekly'] | None
    next_run_at: datetime | None
    enabled: bool
    version: int
    approved_version: int | None
    credit_cap: int
    definition: Definition
    created_at: datetime
    updated_at: datetime
    last_run: AutomationRun | None
    webhook: AutomationWebhook | None  # a webhook automation before its first secret has none
    signal: AutomationSignal | None  # None: another trigger


class Automations(BaseModel):
    automations: list[Automation]


class AutomationRuns(BaseModel):
    runs: list[AutomationRun]


class AutomationPreviewIn(BaseModel):
    """A definition to check before it is saved or enabled. ``automation_id``: the saved automation it would replace,
    whose creator the run spends and writes as."""

    model_config = ConfigDict(extra='forbid')
    definition: Definition
    credit_cap: int = Field(ge=1, le=MAX_CREDIT_CAP)
    automation_id: UUID | None = None
    # The trigger the draft would save. None: the saved automation's, else ``manual``.
    trigger: Trigger | None = None
    # Only the kind of the trigger, as before ``trigger``: never ``signal``, which needs its watchlist.
    trigger_kind: Literal['manual', 'schedule', 'webhook'] | None = None

    @model_validator(mode='after')
    def _one_trigger(self):
        if self.trigger is not None and self.trigger_kind is not None:
            raise ValueError('send the trigger, not its kind too')
        return self


class AutomationPreviewProblem(BaseModel):
    code: str
    message: str


class AutomationPreviewStep(BaseModel):
    type: StepKind
    credits_each: int  # per person the step bills; 0: free


class AutomationPreviewCampaign(BaseModel):
    id: UUID
    name: str
    # Only a campaign that has not ended. ``active``: an approved run enrolls people as active, and it sends to them.
    status: Literal['draft', 'active', 'paused']


class AutomationPreviewInstantlyCampaign(InstantlyCampaignSnapshot):
    url: str


class AutomationPreviewInstantly(BaseModel):
    """The campaign of the ``add_to_instantly`` step, read from Instantly once."""

    connected: bool  # the project's Instantly connection is usable
    campaign: AutomationPreviewInstantlyCampaign | None  # None: not connected, not found, or not read
    # The error code of a read that failed. It is never one of ``problems``, since the run reads the campaign again,
    # but a code of ``instantly_step._PERMANENT`` (Instantly switched off, a key it refuses or that cannot be
    # decrypted) fails every run until it is fixed. Any other code (unreachable, rate limited) may pass then.
    unreadable: str | None


class AutomationPreview(BaseModel):
    """What a run would work on today and the most it may spend. Counts are ``None`` when no step uses them, or when
    the resource they count is one of ``problems``."""

    problems: list[AutomationPreviewProblem]
    steps: list[AutomationPreviewStep]
    people: int | None  # rows of the person list this automation has not taken in yet
    # of those, with an address a campaign sends to (only with ``add_to_campaign`` or ``add_to_instantly``)
    with_email: int | None
    with_contact: int | None  # of those, with that address or a LinkedIn URL: what enrollment needs
    accounts: int | None  # rows of the company list (only with ``find_people``)
    campaign: AutomationPreviewCampaign | None
    instantly: AutomationPreviewInstantly | None  # None: no ``add_to_instantly`` step
    signals: AutomationPreviewSignals | None  # None: another trigger, or its watchlist is one of ``problems``
    people_per_run: int
    accounts_per_run: int  # companies of the accounts list one run's find-people step looks at
    max_credits: int
