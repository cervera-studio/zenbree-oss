"""Pure send rules (#80) and the MIME/provider boundary of the email step (#81)."""

import base64
import email
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.mail_threading import ThreadRef
from app.integrations import mailbox_sender
from app.integrations.providers import GOOGLE, MICROSOFT
from app.outreach import sending

# Monday 2026-10-05 10:00 in Rome.
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)


def test_daily_cap_warms_up_and_respects_the_mailbox_limit():
    assert sending.daily_cap(None, NOW - timedelta(days=1), NOW) == 10
    assert sending.daily_cap(None, NOW - timedelta(days=8), NOW) == 25
    assert sending.daily_cap(None, NOW - timedelta(days=30), NOW) == 50
    assert sending.daily_cap(20, NOW - timedelta(days=30), NOW) == 20
    assert sending.daily_cap(5, NOW - timedelta(days=1), NOW) == 5
    assert sending.daily_cap(None, None, NOW) == 10  # unknown age is brand new


def test_send_gap_is_spread_and_deterministic():
    gaps = {sending.send_gap('mb-1', NOW + timedelta(minutes=m)) for m in range(50)}
    assert all(timedelta(minutes=9) <= g <= timedelta(minutes=14) for g in gaps)
    assert len(gaps) > 10
    assert sending.send_gap('mb-1', NOW) == sending.send_gap('mb-1', NOW)


def test_window_is_judged_in_the_mailbox_zone():
    weekdays = [0, 1, 2, 3, 4]
    assert sending.in_window(NOW, timezone='Europe/Rome', start_hour=9, end_hour=18, days=weekdays)
    # 08:00 UTC is 04:00 in New York.
    assert not sending.in_window(NOW, timezone='America/New_York', start_hour=9, end_hour=18, days=weekdays)
    assert not sending.in_window(
        NOW + timedelta(days=5), timezone='Europe/Rome', start_hour=9, end_hour=18, days=weekdays
    )
    assert sending.day_start(NOW, 'Europe/Rome') == datetime(2026, 10, 4, 22, 0, tzinfo=UTC)


def test_steps_are_spaced_from_the_previous_delivery():
    steps = [
        {'channel': 'email', 'send_after_days': 0},
        {'channel': 'email', 'send_after_days': 3, 'threaded': True},
        {'channel': 'email', 'send_after_days': 7, 'threaded': True},
    ]
    assert sending.step_due_at(steps, 1, NOW) == NOW
    assert sending.step_due_at(steps, 3, NOW) == NOW + timedelta(days=4)
    assert sending.thread_root(steps, 3) == 1


def test_render_refuses_what_it_cannot_fill():
    values = {'firstName': 'Anna', 'company': 'Acme', 'lastName': ' '}
    assert sending.render_values('Ciao {{firstName}} di {{company}}', values).value == 'Ciao Anna di Acme'
    assert sending.render_values('Ciao {{nome}}', values).error.code == 'OUTREACH.COPY_UNRENDERED'
    assert sending.render_values('Gentile {{lastName}}', values).error.code == 'OUTREACH.COPY_MISSING_FIELD'


def test_compose_appends_sign_off_and_one_footer():
    body = sending.compose_body('Ciao Anna', 'Marco\nAcme')
    assert body.startswith('Ciao Anna\n\nMarco\nAcme') and body.count('no grazie') == 1
    assert sending.compose_body(body, '') == body  # idempotent: approved copy stays byte for byte
    linked = sending.compose_body('Ciao Anna', '', 'https://u.zenbree.test/x/tok')
    assert linked.endswith(  # the handoff's Unsub-Footer (#220)
        '\n\n---\nPer non ricevere altri messaggi, è sufficiente rispondere "no grazie" e non verranno inviate altre email.'
        ' Oppure: https://u.zenbree.test/x/tok\nTrattamento dati ai sensi del GDPR Art. 13 e D.Lgs. 196/2003.'
    )
    assert sending.find_placeholder('Subject', 'Ciao [Nome],') == '[Nome]'
    assert sending.find_placeholder('Ciao Anna', 'vedi [1]') is None


def test_message_carries_threading_reply_to_and_list_unsubscribe():
    raw = mailbox_sender.build_message(
        sender='me@acme.test',
        sender_name='Marco',
        to='anna@x.test',
        subject='Re: Ciao',
        text='Follow up',
        message_id='<m2@acme.test>',
        reply_to='reply+37c5f49b24d647ca8403a6cfd5cea98f.7a8d38547b16@in.zenbree.test',
        thread=ThreadRef(message_id='<m1@acme.test>', references=('<m1@acme.test>',)),
    )
    msg = email.message_from_bytes(raw)
    assert msg['Message-ID'] == '<m2@acme.test>' and msg['In-Reply-To'] == '<m1@acme.test>'
    assert msg['Reply-To'] == 'reply+37c5f49b24d647ca8403a6cfd5cea98f.7a8d38547b16@in.zenbree.test'
    assert (
        msg['List-Unsubscribe']
        == '<mailto:reply+37c5f49b24d647ca8403a6cfd5cea98f.7a8d38547b16@in.zenbree.test?subject=unsubscribe>'
    )
    assert 'List-Unsubscribe-Post' not in msg  # no HTTPS link, no one-click claim
    plain = mailbox_sender.build_message(
        sender='', sender_name='', to='a@x.test', subject='s', text='t', message_id='<m@x>', reply_to=None, thread=None
    )
    assert 'List-Unsubscribe' not in email.message_from_bytes(plain)


def test_one_click_list_unsubscribe_rfc_8058():
    url = 'https://api.zenbree.test/v1/unsubscribe/' + 'A' * 300  # long tokens must not be folded into =?utf-8?
    common = {'sender': '', 'sender_name': '', 'to': 'a@x.test', 'subject': 's', 'text': 't', 'thread': None}
    both = email.message_from_bytes(
        mailbox_sender.build_message(**common, message_id='<m@x>', reply_to='reply+a.b@in.test', unsubscribe_url=url)
    )
    assert both['List-Unsubscribe'] == f'<{url}>, <mailto:reply+a.b@in.test?subject=unsubscribe>'
    assert both['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
    https_only = email.message_from_bytes(
        mailbox_sender.build_message(**common, message_id='<m@x>', reply_to=None, unsubscribe_url=url)
    )
    assert https_only['List-Unsubscribe'] == f'<{url}>' and 'Reply-To' not in https_only


async def test_gmail_and_graph_requests(http):
    http.handler = lambda r: httpx.Response(200, json={'id': 'g1', 'threadId': 't1'})
    sent = await mailbox_sender.send(GOOGLE, access_token='at', raw=b'raw', thread_id='t0')
    assert sent.value.message_id == 'g1' and sent.value.thread_id == 't1'
    gmail = http.calls[-1]
    assert gmail.headers['authorization'] == 'Bearer at'
    assert gmail.read() == b'{"raw":"cmF3","threadId":"t0"}'

    http.handler = lambda r: httpx.Response(202)
    sent = await mailbox_sender.send(MICROSOFT, access_token='at', raw=b'raw')
    graph = http.calls[-1]
    assert sent.value.thread_id == '' and graph.headers['content-type'] == 'text/plain'
    assert base64.b64decode(graph.read()) == b'raw'


@pytest.mark.parametrize(
    ('response', 'code', 'ambiguous'),
    [
        (httpx.Response(401), 'INTEGRATIONS.AUTH', None),
        (httpx.Response(403, text='{"reason": "userRateLimitExceeded"}'), 'INTEGRATIONS.RATE_LIMITED', None),
        (httpx.Response(400), 'INTEGRATIONS.HTTP_ERROR', False),
        (httpx.Response(503), 'INTEGRATIONS.HTTP_ERROR', True),
    ],
)
async def test_send_errors_say_whether_delivery_is_unknown(http, response, code, ambiguous):
    http.handler = lambda r: response
    sent = await mailbox_sender.send(GOOGLE, access_token='at', raw=b'raw')
    assert sent.error.code == code and sent.error.context.get('delivery_ambiguous') == ambiguous


async def test_timeout_after_the_request_left_is_ambiguous(http):
    def boom(request):
        raise httpx.ReadTimeout('slow', request=request)

    http.handler = boom
    assert (await mailbox_sender.send(GOOGLE, access_token='at', raw=b'x')).error.context['delivery_ambiguous']

    def refused(request):
        raise httpx.ConnectError('down', request=request)

    http.handler = refused
    assert not (await mailbox_sender.send(GOOGLE, access_token='at', raw=b'x')).error.context['delivery_ambiguous']


def test_linkedin_profile_note_and_degree():
    assert sending.profile_slug('https://www.linkedin.com/in/andr%C3%A9s-lopez/?trk=x') == 'andr%C3%A9s-lopez'
    assert sending.profile_slug('linkedin.com/in/anna') == 'anna'
    assert sending.profile_slug('https://www.linkedin.com/company/acme') == ''
    assert sending.first_degree({'network_distance': 'DISTANCE_1'}) and sending.first_degree({'is_relationship': True})
    assert not sending.first_degree({'network_distance': 'SECOND_DEGREE'})
    assert sending.invitation_note('x' * 250, True) == 'x' * 250
    assert sending.invitation_note('x' * 250, None) == ''  # unknown plan gets the free limit


def test_linkedin_window_is_weekday_office_hours_in_rome():
    assert sending.linkedin_window_open(NOW)
    assert not sending.linkedin_window_open(NOW + timedelta(hours=9))  # 19:00
    assert not sending.linkedin_window_open(NOW + timedelta(days=5))  # Saturday


WEEKDAYS = [0, 1, 2, 3, 4]
ROME = {'timezone': 'Europe/Rome', 'start_hour': 9, 'end_hour': 18, 'days': WEEKDAYS}


def test_next_open_inside_after_hours_and_weekend():
    assert sending.next_open(NOW, **ROME) == NOW  # Monday 10:00 Rome
    evening = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)  # Monday 19:00 Rome
    assert sending.next_open(evening, **ROME) == datetime(2026, 10, 6, 7, 0, tzinfo=UTC)  # Tuesday 09:00 Rome
    early = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # Monday 06:00 Rome
    assert sending.next_open(early, **ROME) == datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
    friday_night = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)
    assert sending.next_open(friday_night, **ROME) == datetime(2026, 10, 12, 7, 0, tzinfo=UTC)  # Monday
    assert sending.next_open(NOW, **{**ROME, 'days': []}) is None


def test_next_open_keeps_the_local_hour_across_the_dst_change():
    saturday = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)  # Rome leaves summer time on Oct 25
    assert sending.next_open(saturday, **ROME) == datetime(2026, 10, 26, 8, 0, tzinfo=UTC)  # 09:00 CET


def test_next_send_time_waits_for_every_window():
    campaign = {'send_window_start_hour': 6, 'send_window_end_hour': 20, 'send_window_days': [0, 1, 2, 3, 4, 5, 6]}
    saturday = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
    assert sending.next_send_time(saturday, sending.email_windows(campaign, 'Europe/Rome')) == saturday
    assert sending.next_send_time(saturday, sending.linkedin_windows(campaign, 'Europe/Rome')) == datetime(
        2026, 10, 12, 7, 0, tzinfo=UTC
    )
