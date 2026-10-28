"""``account_state`` (#935): what one account may do now, from its row and today's usage."""

from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.sender_state import Hold, Waiting, account_state
from app.outreach import sending

# Wednesday 2026-10-14 10:00 in Rome (08:00 UTC), inside the default window (Monday to Friday, 9 to 18).
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


def row(**over):
    return {
        'id': 'a1',
        'status': 'connected',
        'paused': False,
        'daily_limit': 50,
        'created_at': NOW - timedelta(days=30),
        'last_used_at': None,
        'send_timezone': 'Europe/Rome',
        'send_window_start_hour': 9,
        'send_window_end_hour': 18,
        'send_window_days': [0, 1, 2, 3, 4],
        **over,
    }


@pytest.mark.parametrize(
    ('over', 'used', 'vendor_disabled', 'state', 'reason'),
    [
        ({}, 0, False, 'ready', None),
        ({}, 49, False, 'ready', None),
        ({'status': 'disconnected'}, 0, False, 'held', Hold.DISCONNECTED),
        ({'status': 'error'}, 0, False, 'held', Hold.NEEDS_RECONNECT),
        ({'paused': True}, 0, False, 'held', Hold.PAUSED),
        ({}, 0, True, 'held', Hold.VENDOR_DISABLED),
        ({'last_used_at': NOW + timedelta(hours=1)}, 0, False, 'held', Hold.THROTTLED),
        ({'last_used_at': NOW + timedelta(seconds=2)}, 0, False, 'waiting', Waiting.PACED),
        ({'last_used_at': NOW}, 0, False, 'waiting', Waiting.PACED),
        ({}, 50, False, 'waiting', Waiting.CAPPED),
        ({'created_at': NOW - timedelta(days=3)}, 10, False, 'waiting', Waiting.CAPPED),
        ({'send_window_end_hour': 10}, 0, False, 'waiting', Waiting.OUTSIDE_WINDOW),
        ({'send_window_days': [5, 6]}, 0, False, 'waiting', Waiting.OUTSIDE_WINDOW),
        ({'last_used_at': NOW - timedelta(seconds=1)}, 0, False, 'waiting', Waiting.PACED),
        ({'last_used_at': NOW - timedelta(minutes=15)}, 0, False, 'ready', None),
        # a hold outranks a wait; the order of the holds is the order shown
        ({'status': 'error', 'paused': True}, 50, True, 'held', Hold.NEEDS_RECONNECT),
        ({'paused': True}, 50, True, 'held', Hold.PAUSED),
        ({}, 50, True, 'held', Hold.VENDOR_DISABLED),
        ({'last_used_at': NOW + timedelta(hours=1)}, 50, False, 'held', Hold.THROTTLED),
        # a wait: the cap outranks the window
        ({'send_window_days': [5, 6]}, 50, False, 'waiting', Waiting.CAPPED),
    ],
)
def test_state_and_reason(over, used, vendor_disabled, state, reason):
    result = account_state(row(**over), now=NOW, used_today=used, vendor_disabled=vendor_disabled)
    assert (result.state, result.reason) == (state, reason)


def test_the_email_channel_switch_holds_every_account():
    result = account_state(row(), now=NOW, used_today=0, channel_disabled=True)
    assert (result.state, result.reason, result.resumes_at) == ('held', Hold.CHANNEL_DISABLED, None)
    capped = account_state(row(), now=NOW, used_today=50, channel_disabled=True)
    assert capped.reason is Hold.CHANNEL_DISABLED
    paused = account_state(row(paused=True), now=NOW, used_today=0, channel_disabled=True)
    assert paused.reason is Hold.PAUSED


def test_a_throttled_account_resumes_when_the_dispatcher_sends_again():
    rest = NOW + timedelta(hours=1)
    result = account_state(row(last_used_at=rest), now=NOW, used_today=0)
    assert result.resumes_at == rest + sending.send_gap('a1', rest)


def test_a_paced_account_resumes_at_the_end_of_its_gap():
    last = NOW - timedelta(minutes=1)
    result = account_state(row(last_used_at=last), now=NOW, used_today=0)
    assert result.resumes_at == last + sending.send_gap('a1', last)


def test_a_capped_account_resumes_when_its_window_opens_after_the_midnight_reset():
    result = account_state(row(), now=NOW, used_today=50)
    assert result.resumes_at == datetime(2026, 10, 15, 7, 0, tzinfo=UTC)


def test_a_window_that_is_shut_resumes_when_it_opens():
    evening = datetime(2026, 10, 14, 17, 0, tzinfo=UTC)  # 19:00 in Rome
    result = account_state(row(), now=evening, used_today=0)
    assert result.waiting is Waiting.OUTSIDE_WINDOW
    assert result.resumes_at == datetime(2026, 10, 15, 7, 0, tzinfo=UTC)  # 09:00 on Thursday in Rome


def test_a_held_account_has_no_resume_time_unless_the_clock_ends_the_hold():
    for over in ({'paused': True}, {'status': 'error'}, {'status': 'disconnected'}):
        assert account_state(row(**over), now=NOW, used_today=0).resumes_at is None
    assert account_state(row(), now=NOW, used_today=0).resumes_at is None


FRIDAY_EVENING = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
MONDAY_OPENING = datetime(2026, 10, 19, 7, 0, tzinfo=UTC)


def test_a_capped_account_whose_window_is_shut_at_the_reset_resumes_when_the_window_opens():
    result = account_state(row(), now=FRIDAY_EVENING, used_today=50)
    assert (result.waiting, result.resumes_at) == (Waiting.CAPPED, MONDAY_OPENING)


def test_a_capped_account_resumes_at_midnight_when_the_window_is_open_then():
    always = row(send_window_start_hour=0, send_window_end_hour=24, send_window_days=[0, 1, 2, 3, 4, 5, 6])
    result = account_state(always, now=FRIDAY_EVENING, used_today=50)
    assert result.resumes_at == datetime(2026, 10, 16, 22, 0, tzinfo=UTC)


def test_a_paced_slot_that_ends_after_the_window_closes_is_outside_the_window_until_the_next_opening():
    last = FRIDAY_EVENING + timedelta(minutes=59)
    now = last + timedelta(seconds=30)
    result = account_state(row(last_used_at=last), now=now, used_today=0)
    assert (result.waiting, result.resumes_at) == (Waiting.OUTSIDE_WINDOW, MONDAY_OPENING)


def test_a_throttled_rest_that_ends_after_the_window_closes_resumes_at_the_next_opening():
    result = account_state(row(last_used_at=FRIDAY_EVENING + timedelta(hours=2)), now=FRIDAY_EVENING, used_today=0)
    assert (result.hold, result.resumes_at) == (Hold.THROTTLED, MONDAY_OPENING)
