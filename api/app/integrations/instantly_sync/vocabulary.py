"""What Instantly tells Zenbree about a lead (#930), in Zenbree's words, and what a lead's facts mean. Pure: no IO.

There is no ``delivered`` and no ``enrolled`` kind. A sent event proves a send, not an inbox, and an enrollment is
#929's ``instantly_leads.outcome``: neither can be recorded or read as the other.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from app.outreach.inbound import ReplyClass, safer


class EventKind(StrEnum):
    SENT = 'sent'
    BOUNCED = 'bounced'
    REPLY = 'reply'
    UNSUBSCRIBED = 'unsubscribed'
    INTEREST = 'interest'  # a label on the lead: never a reply on its own


class ReplyNature(StrEnum):
    HUMAN = 'human'
    AUTO = 'auto'
    UNCLASSIFIED = 'unclassified'


class Label(StrEnum):
    """Instantly's own lead labels (``lt_interest_status``). Which ones are positive is the reader's policy (#942)."""

    INTERESTED = 'interested'
    MEETING_BOOKED = 'meeting_booked'
    MEETING_COMPLETED = 'meeting_completed'
    WON = 'won'
    OUT_OF_OFFICE = 'out_of_office'
    NOT_INTERESTED = 'not_interested'
    WRONG_PERSON = 'wrong_person'
    LOST = 'lost'
    NO_SHOW = 'no_show'


class Source(StrEnum):
    WEBHOOK = 'webhook'  # a delivery: no event id, the key is derived from its fields
    EMAILS = 'emails'  # GET /api/v2/emails: Instantly's email id
    LEADS = 'leads'  # a lead's status and label: no event time for a status


type TimeBasis = Literal['event', 'record', 'observed']

#: A webhook fact and an API record of one lead and kind this close in time are one fact. Two replies of one lead
#: inside it, seen by one source each, become one: a duplicate is possible across the window, a lost fact is not.
MATCH_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing a source says happened to one lead of one campaign. Built only by ``mapping``."""

    source: Source
    kind: EventKind
    campaign_id: str
    email: str  # lower(btrim()), as instantly_leads.email
    occurred_at: datetime
    time_basis: TimeBasis
    key: str
    reply_nature: ReplyNature | None = None
    label: Label | None = None
    step: int | None = None
    provider_ref: str | None = None
    message_id: str | None = None
    workspace_id: str | None = None  # what a webhook payload names; the API answers for the key's own workspace
    our_class: ReplyClass | None = None
    hard_bounce: bool = False

    def __post_init__(self) -> None:
        if (self.kind is EventKind.REPLY) != (self.reply_nature is not None):
            raise ValueError('a reply, and only a reply, has a nature')
        if self.our_class is not None and self.kind is not EventKind.REPLY:
            raise ValueError('only a reply has our vote')
        if self.our_class is ReplyClass.HUMAN:
            raise ValueError('our vote is a marker (an autoresponder or an opt-out), never "nothing matched"')
        if self.hard_bounce and self.kind is not EventKind.BOUNCED:
            raise ValueError('only a bounce is hard')
        if (self.kind is EventKind.INTEREST) != (self.label is not None):
            raise ValueError('an interest, and only an interest, has a label')
        if self.occurred_at.tzinfo is None:
            raise ValueError('occurred_at must be aware')


type IgnoredReason = Literal['event_type', 'unmapped', 'bad_record', 'workspace', 'unknown_lead']


@dataclass(frozen=True, slots=True)
class Ignored:
    """A record that is valid but says nothing Zenbree keeps, counted by reason on the connection's sync state."""

    reason: IgnoredReason


@dataclass(frozen=True, slots=True)
class FactRow:
    """A stored fact as the fold reads it."""

    id: str
    kind: EventKind
    occurred_at: datetime
    reply_nature: ReplyNature | None = None
    our_class: ReplyClass | None = None
    hard_bounce: bool = False
    lead_id: str | None = None


_THEIRS = {ReplyNature.HUMAN: ReplyClass.HUMAN, ReplyNature.AUTO: ReplyClass.AUTO}


def reply_class(nature: ReplyNature | None, ours: ReplyClass | None) -> ReplyClass | None:
    """The safer of the votes cast. None when neither Instantly nor Zenbree voted."""
    return safer(_THEIRS.get(nature) if nature else None, ours)


type StopReason = Literal['unsubscribed', 'bounced', 'opt_out_reply', 'human_reply']
#: What a fact does natively: a stop, or an autoresponder's pause.
type Effect = StopReason | Literal['auto_reply']


@dataclass(frozen=True, slots=True)
class Act:
    effect: Effect
    at: datetime
    fact_id: str
    lead_id: str | None


def fold(rows: Iterable[FactRow], *, emails_refused: bool = False) -> tuple[Act, ...]:
    """Every fact of one person that stops or pauses, in time order. Derived on every read and never stored, and the
    same answer for the same facts in any order.

    A reply that neither side voted on waits for Instantly's vote, which the API record brings. With
    ``emails_refused`` no API record will come, and it counts as human: the owner's rule puts human before
    autoresponder. The default is derived here on each read and never stored, so a later vote still decides."""
    rows = sorted(rows, key=lambda r: (r.occurred_at, r.id))
    classes = {r.id: c for r in rows if (c := reply_class(r.reply_nature, r.our_class))}
    if emails_refused:
        classes |= {r.id: ReplyClass.HUMAN for r in rows if r.kind is EventKind.REPLY and r.id not in classes}
    acts: list[Act] = []
    for row in rows:
        reply = classes.get(row.id)
        effect: Effect | None = _stop_reason(row, reply) or ('auto_reply' if reply is ReplyClass.AUTO else None)
        if effect:
            acts.append(Act(effect, row.occurred_at, row.id, row.lead_id))
    return tuple(acts)


def _stop_reason(row: FactRow, reply: ReplyClass | None) -> StopReason | None:
    if row.kind is EventKind.UNSUBSCRIBED:
        return 'unsubscribed'
    if row.kind is EventKind.BOUNCED:
        return 'bounced' if row.hard_bounce else None
    return _REPLY_STOP.get(reply)


_REPLY_STOP: dict[ReplyClass | None, StopReason] = {
    ReplyClass.OPT_OUT: 'opt_out_reply',
    ReplyClass.HUMAN: 'human_reply',
}


type Mode = Literal['live', 'polled', 'paused', 'off']


def mode(*, connected: bool, disabled: bool, webhook: str | None) -> Mode:
    """How facts reach Zenbree now. ``live`` only once an authenticated delivery came: a webhook Instantly accepted
    and never used proves nothing. ``paused``: the kill switch stops every call to Instantly (callbacks are still
    recorded). ``off``: no key to call with."""
    if not connected:
        return 'off'
    if disabled:
        return 'paused'
    return 'live' if webhook == 'live' else 'polled'
