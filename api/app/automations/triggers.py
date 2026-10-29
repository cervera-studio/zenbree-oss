"""What starts a run of an automation (#939, #1031, #1030): the trigger a request names, and how a row stores it.

One shape for each kind, so no field has a meaning for one kind only. The JSON of the older kinds stays
``{kind, cadence}``: manual and webhook accept ``cadence: null``, as the single model before them did, and a body
with no ``kind`` is manual.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

# What starts a run. The SQL CHECKs of both tables and the web (through schema.gen.ts) follow this one list.
TriggerKind = Literal['manual', 'schedule', 'webhook', 'signal']


class _Trigger(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ManualTrigger(_Trigger):
    """Runs on ``POST /run`` only."""

    kind: Literal['manual'] = 'manual'
    cadence: None = None


class ScheduleTrigger(_Trigger):
    """Also runs on its cadence."""

    kind: Literal['schedule']
    cadence: Literal['daily', 'weekly']


class WebhookTrigger(_Trigger):
    """Runs once for each signed call that names one person of the list (``app.automations.inbound``)."""

    kind: Literal['webhook']
    cadence: None = None


class SignalTrigger(_Trigger):
    """Runs once for each account of the watchlist whose unused signals reach ``min_score`` together
    (``app.automations.signal_trigger``)."""

    kind: Literal['signal']
    # None once the watchlist was deleted: a problem (AUTOMATION.WATCHLIST_NOT_FOUND), so no save names none.
    watchlist_id: UUID | None
    min_score: float = Field(gt=0, le=10000)


def _kind(value: Any) -> str:
    return value.get('kind', 'manual') if isinstance(value, dict) else getattr(value, 'kind', 'manual')


Trigger = Annotated[
    Annotated[ManualTrigger, Tag('manual')]
    | Annotated[ScheduleTrigger, Tag('schedule')]
    | Annotated[WebhookTrigger, Tag('webhook')]
    | Annotated[SignalTrigger, Tag('signal')],
    Discriminator(_kind),
]


def of_row(automation: dict) -> Trigger:
    match automation['trigger_kind']:
        case 'schedule':
            return ScheduleTrigger(kind='schedule', cadence=automation['cadence'])
        case 'webhook':
            return WebhookTrigger(kind='webhook')
        case 'signal':
            return SignalTrigger(
                kind='signal', watchlist_id=automation['watchlist_id'], min_score=automation['signal_min_score']
            )
    return ManualTrigger()


def columns(trigger: Trigger) -> dict:
    signal = trigger if isinstance(trigger, SignalTrigger) else None
    return {
        'trigger_kind': trigger.kind,
        'cadence': trigger.cadence if isinstance(trigger, ScheduleTrigger) else None,
        'watchlist_id': signal.watchlist_id if signal else None,
        'signal_min_score': signal.min_score if signal else None,
    }


class AutomationSignal(BaseModel):
    """The signal trigger of a saved automation."""

    watchlist_id: UUID | None  # None once the watchlist was deleted
    watchlist_name: str | None
    min_score: float
    since: datetime  # only signals first seen from here start runs


class UsedSignal(BaseModel):
    type: str
    title: str | None
    score: float  # its live score when the run started
    occurred_at: datetime


class RunSignal(BaseModel):
    """The account a signal run worked on, and the signals that started it."""

    account_id: UUID
    account_name: str | None  # None once the account row was deleted
    signals: list[UsedSignal]


class PreviewSignalAccount(BaseModel):
    account_id: UUID
    name: str | None
    title: str | None  # of its strongest signal
    score: float  # the live scores of its signals, added
    seen_at: datetime  # when its newest signal was first seen


class AutomationPreviewSignals(BaseModel):
    """The accounts whose signals of the last 30 days would have started a run (``signal_trigger.preview``)."""

    accounts: int
    recent: list[PreviewSignalAccount]  # the 5 most recent
