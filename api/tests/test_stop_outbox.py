from datetime import UTC, datetime, timedelta

import pytest

from app.outreach.stop_outbox import (
    MAX_ATTEMPTS,
    Change,
    ConfirmedByHand,
    Later,
    NotEnrolled,
    Refused,
    Retry,
    Stop,
    Stopped,
    StopStatus,
    transition,
)

NOW = datetime(2026, 10, 17, 9, tzinfo=UTC)
PENDING, FAILED, CONFIRMED = StopStatus.PENDING, StopStatus.FAILED, StopStatus.CONFIRMED
EVENTS = {
    'stopped': Stopped(),
    'not_enrolled': NotEnrolled(),
    'later': Later('INSTANTLY.UNREACHABLE'),
    'refused': Refused('INSTANTLY.STOP_SCOPE'),
    'retry': Retry(),
    'by_hand': ConfirmedByHand('u1'),
}
TABLE = {
    (PENDING, 'stopped'): Change(CONFIRMED, 3, None, None, 'provider'),
    (PENDING, 'not_enrolled'): Change(CONFIRMED, 3, None, None, 'not_enrolled'),
    (PENDING, 'later'): Change(PENDING, 3, NOW + timedelta(minutes=4), 'INSTANTLY.UNREACHABLE'),
    (PENDING, 'refused'): Change(FAILED, 3, None, 'INSTANTLY.STOP_SCOPE'),
    (PENDING, 'retry'): None,
    (PENDING, 'by_hand'): None,
    (FAILED, 'stopped'): None,
    (FAILED, 'not_enrolled'): None,
    (FAILED, 'later'): None,
    (FAILED, 'refused'): None,
    (FAILED, 'retry'): Change(PENDING, 0, NOW, None),
    (FAILED, 'by_hand'): Change(CONFIRMED, 2, None, None, 'user', 'u1'),
    **{(CONFIRMED, name): None for name in EVENTS},
}


@pytest.mark.parametrize(('status', 'event'), [(s, e) for s in StopStatus for e in EVENTS])
def test_every_state_against_every_event(status, event):
    assert transition(Stop('s1', status, 2), EVENTS[event], NOW) == TABLE[(status, event)]


@pytest.mark.parametrize(
    ('attempts', 'wait'),
    [(0, timedelta(minutes=1)), (7, timedelta(minutes=128)), (8, timedelta(hours=4))],
)
def test_the_backoff_doubles_from_one_minute_up_to_four_hours(attempts, wait):
    change = transition(Stop('s1', PENDING, attempts), Later('INSTANTLY.UNREACHABLE'), NOW)
    assert change == Change(PENDING, attempts + 1, NOW + wait, 'INSTANTLY.UNREACHABLE')


def test_the_last_attempt_fails_the_stop():
    change = transition(Stop('s1', PENDING, MAX_ATTEMPTS - 1), Later('INSTANTLY.UNREACHABLE'), NOW)
    assert change == Change(FAILED, MAX_ATTEMPTS, None, 'INSTANTLY.UNREACHABLE')
