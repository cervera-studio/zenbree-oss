"""Instantly's records in Zenbree's vocabulary (#930): the only module that knows a wire field name. Pure.

Sources, checked against developer.instantly.ai on 2026-10-16 (the reply fields on 2026-10-17):
- a webhook delivery (``event_type``, ``timestamp``, ``workspace``, ``campaign_id``, ``lead_email``, ``step``, and for
  a reply ``reply_subject`` and ``reply_text``): it carries no event id, so its key is a hash of the fields that name
  the event. A retried delivery carries the same body, so the same key.
- an email of ``GET /api/v2/emails``: ``id``, ``message_id``, ``ue_type`` (1 sent from a campaign, 2 received, 3 sent
  by hand, 4 scheduled), ``is_auto_reply``, ``lead``, ``campaign_id``, ``step``, ``timestamp_email``,
  ``timestamp_created``, ``subject`` and ``body.text``.
- a lead of ``POST /api/v2/leads/list``: ``status`` (-1 bounced, -2 unsubscribed) and ``lt_interest_status`` (the
  label table below; a custom label is another number and is left out, never guessed), with
  ``timestamp_last_interest_change``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.integrations.instantly_sync.vocabulary import EventKind, Fact, Ignored, Label, ReplyNature, Source
from app.outreach.inbound import ReplyClass, evidence

_WEBHOOK_KINDS: dict[str, EventKind | Label] = {
    'email_sent': EventKind.SENT,
    'email_bounced': EventKind.BOUNCED,
    'reply_received': EventKind.REPLY,
    'auto_reply_received': EventKind.REPLY,
    'lead_unsubscribed': EventKind.UNSUBSCRIBED,
    'lead_interested': Label.INTERESTED,
    'lead_meeting_booked': Label.MEETING_BOOKED,
    'lead_meeting_completed': Label.MEETING_COMPLETED,
    'lead_closed': Label.WON,
    'lead_out_of_office': Label.OUT_OF_OFFICE,
    'lead_not_interested': Label.NOT_INTERESTED,
    'lead_wrong_person': Label.WRONG_PERSON,
    'lead_no_show': Label.NO_SHOW,
}
_LABELS: dict[int, Label] = {
    1: Label.INTERESTED,
    2: Label.MEETING_BOOKED,
    3: Label.MEETING_COMPLETED,
    4: Label.WON,
    0: Label.OUT_OF_OFFICE,
    -1: Label.NOT_INTERESTED,
    -2: Label.WRONG_PERSON,
    -3: Label.LOST,
    -4: Label.NO_SHOW,
}
_LEAD_STATUS = {-1: EventKind.BOUNCED, -2: EventKind.UNSUBSCRIBED}
SENT_FROM_CAMPAIGN, RECEIVED = 1, 2
# A time outside these bounds is a broken or replayed record, not an event.
_PAST, _FUTURE = timedelta(days=90), timedelta(minutes=5)


def from_webhook(payload: dict, *, now: datetime) -> Fact | Ignored:
    kind = _WEBHOOK_KINDS.get(str(payload.get('event_type') or ''))
    if kind is None:
        return Ignored('event_type')
    campaign, email = _text(payload.get('campaign_id')), _email(payload.get('lead_email'))
    at = _time(payload.get('timestamp'), now)
    if not (campaign and email and at):
        return Ignored('bad_record')
    step, workspace = _int(payload.get('step')), _text(payload.get('workspace'))
    label = kind if isinstance(kind, Label) else None
    event = EventKind.INTEREST if label else EventKind(kind)
    nature = ours = None
    if label is not None:
        key = _label_key(campaign, email, label, at)
    elif event in (EventKind.BOUNCED, EventKind.UNSUBSCRIBED):
        key = once_key(event, campaign, email)
    else:
        named = (str(payload.get('event_type')), workspace, campaign, email, at.isoformat(), str(step))
        key = 'wh:' + hashlib.sha256('\x1f'.join(named).encode()).hexdigest()
    if event is EventKind.REPLY:
        auto = payload.get('event_type') == 'auto_reply_received'
        nature = ReplyNature.AUTO if auto else _nature(payload.get('is_auto_reply')) or ReplyNature.UNCLASSIFIED
        ours = _vote(payload.get('reply_subject'), payload.get('reply_text'))
    return Fact(
        source=Source.WEBHOOK,
        kind=event,
        campaign_id=campaign,
        email=email,
        occurred_at=at,
        time_basis='event',
        key=key,
        reply_nature=nature,
        label=label,
        step=step,
        workspace_id=workspace or None,
        our_class=ours,
    )


def from_email(item: dict, *, now: datetime) -> Fact | Ignored:
    ue_type = item.get('ue_type')
    if ue_type not in (SENT_FROM_CAMPAIGN, RECEIVED):
        return Ignored('unmapped')
    ref, campaign, email = _text(item.get('id')), _text(item.get('campaign_id')), _email(item.get('lead'))
    at = _time(item.get('timestamp_email'), now) or _time(item.get('timestamp_created'), now)
    if not (ref and campaign and email and at):
        return Ignored('bad_record')
    kind = EventKind.SENT if ue_type == SENT_FROM_CAMPAIGN else EventKind.REPLY
    nature = ours = None
    if kind is EventKind.REPLY:
        nature = _nature(item.get('is_auto_reply'))
        if nature is None:
            return Ignored('bad_record')
        body = item.get('body')
        ours = _vote(item.get('subject'), body.get('text') if isinstance(body, dict) else None)
    return Fact(
        source=Source.EMAILS,
        kind=kind,
        campaign_id=campaign,
        email=email,
        occurred_at=at,
        time_basis='record',
        key=f'em:{ref}',
        reply_nature=nature,
        step=_int(item.get('step')),
        provider_ref=ref,
        message_id=_text(item.get('message_id'))[:500] or None,
        our_class=ours,
    )


def from_lead(item: dict, campaign_id: str, *, now: datetime) -> list[Fact | Ignored]:
    email = _email(item.get('email'))
    if not email:
        return [Ignored('bad_record')]
    found: list[Fact | Ignored] = []
    status = item.get('status')
    kind = _LEAD_STATUS.get(status) if isinstance(status, int) else None
    if kind is not None:
        # A status carries no time: when it was seen bounds it, and a webhook's exact time replaces it.
        found.append(
            Fact(
                source=Source.LEADS,
                kind=kind,
                campaign_id=campaign_id,
                email=email,
                occurred_at=now,
                time_basis='observed',
                key=once_key(kind, campaign_id, email),
                hard_bounce=kind is EventKind.BOUNCED,
            )
        )
    interest = item.get('lt_interest_status')
    if isinstance(interest, int) and not isinstance(interest, bool):
        label = _LABELS.get(interest)
        at = _time(item.get('timestamp_last_interest_change'), now)
        if label is None:
            found.append(Ignored('unmapped'))
        elif at is not None:
            found.append(
                Fact(
                    source=Source.LEADS,
                    kind=EventKind.INTEREST,
                    campaign_id=campaign_id,
                    email=email,
                    occurred_at=at,
                    time_basis='event',
                    key=_label_key(campaign_id, email, label, at),
                    label=label,
                )
            )
    return found


def once_key(kind: EventKind, campaign_id: str, email: str) -> str:
    """A bounce and an unsubscribe happen once to a lead of a campaign, whatever source tells it."""
    return f'{kind}:{campaign_id}:{email}'


def _label_key(campaign_id: str, email: str, label: Label, at: datetime) -> str:
    return f'interest:{campaign_id}:{email}:{label}:{at.isoformat()}'


def _text(value: object) -> str:
    return value.strip()[:300] if isinstance(value, str) else ''


def _email(value: object) -> str:
    email = _text(value).lower()
    return email if '@' in email else ''


def _int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.strip().lstrip('-').isdigit() else None


def _vote(subject: object, text: object) -> ReplyClass | None:
    return evidence(subject=subject if isinstance(subject, str) else '', text=text if isinstance(text, str) else '')


def _nature(value: object) -> ReplyNature | None:
    if value in (True, 1):
        return ReplyNature.AUTO
    if value in (False, 0):
        return ReplyNature.HUMAN
    return None


def _time(value: object, now: datetime) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at if now - _PAST <= at <= now + _FUTURE else None
