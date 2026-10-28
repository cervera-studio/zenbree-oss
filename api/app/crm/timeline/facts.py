"""What the HubSpot timeline ledger is made of (#932): the facts a source records, the states a row goes through and
the codes that say why it is not logged. Pure: no IO. The lists here are the only ones in Python;
tests/test_crm_timeline_sql.py checks the CHECK constraints of the migration against them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from app.crm.hubspot_activity import headers, milliseconds

_ADDRESS = re.compile(r'^[^\s,;<>@]+@[^\s,;<>@]+\.[^\s,;<>@]+$')


class Source(StrEnum):
    INSTANTLY = 'instantly'  # a later source (#925's inbox) adds its member here and its value to the CHECKs


class Kind(StrEnum):
    OUTGOING_EMAIL = 'outgoing_email'
    INCOMING_EMAIL = 'incoming_email'
    OPT_OUT = 'opt_out'


class State(StrEnum):
    PENDING = 'pending'
    CLAIMED = 'claimed'
    LOGGED = 'logged'
    UNCERTAIN = 'uncertain'
    FAILED = 'failed'
    SKIPPED = 'skipped'


class Code(StrEnum):
    """Why a row is not (yet) logged. Stored for the terminal and the user's states; derived for the waiting ones."""

    # skipped, terminal, stored
    NO_OWNER = 'CRM_TIMELINE.NO_OWNER'  # the enrollment has no creator (a deleted user)
    OUTSIDE_WRITE_BACK = 'CRM_TIMELINE.OUTSIDE_WRITE_BACK'  # before the write-back start, or the switch is off
    DUPLICATE = 'CRM_TIMELINE.DUPLICATE'  # the project already holds this physical event
    NATIVE_DUPLICATE = 'CRM_TIMELINE.NATIVE_DUPLICATE'  # #528 already logged this Message-ID
    # failed and uncertain, stored: the user can act
    REFUSED = 'CRM_TIMELINE.REFUSED'  # HubSpot refused the activity (4xx), MAX_ATTEMPTS times
    CONTACT_REFUSED = 'CRM_TIMELINE.CONTACT_REFUSED'  # HubSpot refused the contact (4xx), MAX_ATTEMPTS times
    ANSWER_LOST = 'CRM_TIMELINE.ANSWER_LOST'  # the note may have landed, and nothing on HubSpot says so
    # waiting, derived, never stored
    OWNER_LEFT = 'CRM_TIMELINE.OWNER_LEFT'  # the owner is not a live member of the organization
    GRANT_REVOKED = 'CRM_TIMELINE.GRANT_REVOKED'  # not connected, or the contacts scope is missing
    HELD = 'CRM_TIMELINE.HELD'  # an active read-gate hold, before the contact exists
    VENDOR_DISABLED = 'CRM_TIMELINE.VENDOR_DISABLED'  # vendor_disabled:hubspot


TERMINAL: Final = frozenset({Code.NO_OWNER, Code.OUTSIDE_WRITE_BACK, Code.DUPLICATE, Code.NATIVE_DUPLICATE})
RETRYABLE: Final = frozenset({Code.REFUSED, Code.CONTACT_REFUSED})
RESOLVABLE: Final = frozenset({Code.ANSWER_LOST})
WAITING: Final = frozenset({Code.OWNER_LEFT, Code.GRANT_REVOKED, Code.HELD, Code.VENDOR_DISABLED})
# Refusals of one row (the contact or the activity) before it is given up, so it cannot hold the queue behind it.
MAX_ATTEMPTS: Final = 5


def normalise_message_id(value: str | None) -> str | None:
    """A Message-ID as both ledgers compare it: no angle brackets, lowercase; empty is none."""
    return (value or '').strip().strip('<>').strip().lower() or None


@dataclass(frozen=True, slots=True)
class Person:
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    company_name: str | None = None


@dataclass(frozen=True, slots=True)
class Context:
    """What the note under the activity says: HubSpot has no field for a campaign or a provider."""

    provider: str
    campaign: str
    project: str
    list: str | None
    step: int | None
    subject: str | None = None  # an external feed has none; #925's inbox reply has one
    body: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineFact:
    source: Source
    source_id: str
    organization_id: str
    project_id: str
    owner_id: str | None
    kind: Kind
    email: str
    occurred_at: datetime
    dedupe_key: str
    message_id: str | None
    person: Person
    context: Context

    def __post_init__(self) -> None:
        email = self.email.strip().lower()
        if not _ADDRESS.match(email):
            raise ValueError('a timeline fact names one email address')
        object.__setattr__(self, 'email', email)
        object.__setattr__(self, 'message_id', normalise_message_id(self.message_id))


@dataclass(frozen=True, slots=True)
class Frozen:
    """What a retry must reuse: the object, the exact ``hs_timestamp`` and the direction of an email, the values the
    lost-answer check compares. Stored in ``request`` at the first claim and never rebuilt."""

    object: Literal['emails', 'notes']
    hs_timestamp: int
    direction: Literal['EMAIL', 'INCOMING_EMAIL'] | None

    def as_json(self) -> str:
        return json.dumps({'object': self.object, 'hs_timestamp': self.hs_timestamp, 'direction': self.direction})

    @classmethod
    def of(cls, request: dict) -> Frozen:
        return cls(request['object'], int(request['hs_timestamp']), request.get('direction'))


def freeze(kind: Kind, occurred_at: datetime) -> Frozen:
    ms = milliseconds(occurred_at)
    if kind is Kind.OPT_OUT:
        return Frozen('notes', ms, None)
    return Frozen('emails', ms, 'INCOMING_EMAIL' if kind is Kind.INCOMING_EMAIL else 'EMAIL')


def _line(context: dict) -> str:
    """The line under the text that says where the activity comes from and which Zenbree work it belongs to."""
    parts = ['Zenbree', context.get('project'), context.get('list')]
    parts.append(f'via {context["provider"]}')
    parts.append(context.get('campaign'))
    if context.get('step'):
        parts.append(f'step {context["step"]}')
    return '— ' + ' · '.join(p for p in parts if p)


def opt_out_text(context: dict, source_id: str) -> str:
    """The note of an opt-out. It ends with a marker, so that a later search of the notes can tell whether it landed."""
    said = (
        f'Opted out of {context["campaign"] or "a campaign"} in {context["provider"]}. Recorded by Zenbree. '
        'HubSpot subscription preferences were not changed.'
    )
    return f'{said}\n\n{_line(context)}\n\nzb:{source_id.replace("-", "")[:12]}'


def activity_properties(row: dict, frozen: Frozen, hubspot_owner_id: str | None) -> dict[str, str]:
    """The properties of one call, rebuilt at every claim around ``frozen``: a retry after a refusal sends the owner
    id and the text that are valid now, and the time that was sent first."""
    props = {'hs_timestamp': str(frozen.hs_timestamp)}
    if hubspot_owner_id:
        props['hubspot_owner_id'] = hubspot_owner_id
    context = row['context']
    if frozen.object == 'notes':
        return {**props, 'hs_note_body': opt_out_text(context, str(row['source_id']))}
    incoming = frozen.direction == 'INCOMING_EMAIL'
    text = '\n\n'.join(p for p in ((context.get('body') or '').strip(), _line(context)) if p)
    email = {
        'hs_email_direction': frozen.direction or 'EMAIL',
        'hs_email_status': 'SENT',
        'hs_email_text': text,
        'hs_email_headers': headers(row['email'], '', incoming),
    }
    if context.get('subject'):
        email['hs_email_subject'] = context['subject']
    return {**props, **email}
