import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.instantly_sync import mapping
from app.integrations.instantly_sync.vocabulary import EventKind, FactRow, Ignored, Label, ReplyNature, fold, mode
from app.outreach.inbound import ReplyClass

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
AUTO, OPT_OUT = ReplyClass.AUTO, ReplyClass.OPT_OUT


def hook(event_type='reply_received', **extra):
    payload = {
        'event_type': event_type,
        'timestamp': (NOW - timedelta(minutes=1)).isoformat(),
        'workspace': 'ws-1',
        'campaign_id': 'c-1',
        'lead_email': 'ana@acme.test',
        'step': 1,
        **extra,
    }
    return mapping.from_webhook(payload, now=NOW)


def received(**extra):
    item = {'id': 'e1', 'ue_type': 2, 'lead': 'ana@acme.test', 'campaign_id': 'c-1', 'timestamp_email': NOW.isoformat()}
    return mapping.from_email(item | extra, now=NOW)


@pytest.mark.parametrize(
    ('event_type', 'subject', 'text', 'nature', 'ours'),
    [
        ('reply_received', 'Re: Ciao', 'Sounds good, call me', ReplyNature.UNCLASSIFIED, None),
        ('reply_received', 'Re: Ciao', 'Please remove me from your list', ReplyNature.UNCLASSIFIED, OPT_OUT),
        ('reply_received', 'Automatic reply: Ciao', 'Back on Monday', ReplyNature.UNCLASSIFIED, AUTO),
        ('reply_received', None, None, ReplyNature.UNCLASSIFIED, None),
        ('auto_reply_received', 'Out of office', 'Back on Monday', ReplyNature.AUTO, AUTO),
        ('auto_reply_received', 'Re: Your proposal', 'I am away until 3 November', ReplyNature.AUTO, None),
        ('auto_reply_received', 'Re: Ciao', 'Please remove me', ReplyNature.AUTO, OPT_OUT),
        ('auto_reply_received', None, None, ReplyNature.AUTO, None),
    ],
)
def test_a_webhook_reply_carries_instantly_vote_and_ours(event_type, subject, text, nature, ours):
    fact = hook(event_type, reply_subject=subject, reply_text=text)
    assert (fact.kind, fact.reply_nature, fact.our_class) == (EventKind.REPLY, nature, ours)


def test_an_api_reply_is_voted_on_its_subject_and_text():
    assert received(is_auto_reply=0, subject='Re: Ciao', body={'text': 'No grazie'}).our_class is OPT_OUT
    assert received(is_auto_reply=1, subject='Fuori ufficio', body={'text': 'Torno lunedì'}).our_class is AUTO
    assert received(is_auto_reply=1).our_class is None
    assert received(is_auto_reply=1, subject='Re: Your proposal', body={'text': 'Away until Monday'}).our_class is None


@pytest.mark.parametrize(
    ('extra', 'reason'),
    [
        ({'event_type': 'email_opened'}, 'event_type'),
        ({'event_type': 'campaign_completed'}, 'event_type'),
        ({'lead_email': None}, 'bad_record'),
        ({'timestamp': (NOW + timedelta(hours=1)).isoformat()}, 'bad_record'),
        ({'timestamp': (NOW - timedelta(days=200)).isoformat()}, 'bad_record'),
    ],
)
def test_what_a_webhook_does_not_tell_is_ignored_with_a_reason(extra, reason):
    assert hook(**extra) == Ignored(reason)


def test_a_label_is_never_a_reply():
    label = hook('lead_closed')
    assert label.kind is EventKind.INTEREST and label.label is Label.WON and label.reply_nature is None


def test_an_api_email_maps_sends_from_campaigns_and_classified_replies_only():
    assert received(ue_type=1).kind is EventKind.SENT
    assert received(is_auto_reply=1).reply_nature is ReplyNature.AUTO
    assert received(ue_type=3) == Ignored('unmapped')  # sent by hand, not a step
    assert received(ue_type=4) == Ignored('unmapped')  # scheduled, not sent


def test_the_mode_says_live_only_after_a_delivery():
    assert mode(connected=True, disabled=False, webhook='registered') == 'polled'
    assert mode(connected=True, disabled=False, webhook='live') == 'live'
    assert mode(connected=True, disabled=True, webhook='live') == 'paused'
    assert mode(connected=False, disabled=False, webhook='live') == 'off'


def test_only_a_lead_status_makes_a_bounce_hard():
    [status] = mapping.from_lead({'email': 'ana@acme.test', 'status': -1}, 'c-1', now=NOW)
    webhook = hook('email_bounced')
    assert status.hard_bounce and not webhook.hard_bounce and status.key == webhook.key
    assert mapping.from_lead({'email': 'ana@acme.test', 'status': 1}, 'c-1', now=NOW) == []


def _row(n, kind, minutes, **extra) -> FactRow:
    return FactRow(id=f'f{n}', kind=kind, occurred_at=NOW + timedelta(minutes=minutes), **extra)


ROWS = [
    _row(1, EventKind.SENT, 0),
    _row(2, EventKind.BOUNCED, 5),
    _row(3, EventKind.REPLY, 10, reply_nature=ReplyNature.HUMAN, lead_id='l1'),
    _row(4, EventKind.REPLY, 20, reply_nature=ReplyNature.UNCLASSIFIED, our_class=OPT_OUT, lead_id='l1'),
    _row(5, EventKind.UNSUBSCRIBED, 30, lead_id='l2'),
    _row(6, EventKind.BOUNCED, 40, hard_bounce=True, lead_id='l2'),
    _row(7, EventKind.REPLY, 50, reply_nature=ReplyNature.HUMAN, our_class=AUTO, lead_id='l1'),
    _row(8, EventKind.REPLY, 60, reply_nature=ReplyNature.HUMAN, lead_id='l2'),
]


def test_the_fold_gives_every_fact_that_stops_its_act_in_every_order():
    answers = {fold(order) for order in itertools.permutations(ROWS)}
    assert len(answers) == 1
    [acts] = answers
    assert [(a.effect, a.fact_id, a.lead_id) for a in acts] == [
        ('human_reply', 'f3', 'l1'),
        ('opt_out_reply', 'f4', 'l1'),
        ('unsubscribed', 'f5', 'l2'),
        ('bounced', 'f6', 'l2'),
        ('human_reply', 'f7', 'l1'),
        ('human_reply', 'f8', 'l2'),
    ]


def test_a_soft_bounce_and_an_autoresponder_by_both_votes_never_stop():
    autoresponder = _row(9, EventKind.REPLY, 5, reply_nature=ReplyNature.AUTO, our_class=AUTO)
    label = _row(10, EventKind.INTEREST, 6)
    assert [a.effect for a in fold([ROWS[1], autoresponder, label])] == ['auto_reply']
