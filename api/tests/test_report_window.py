from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException

from app.reports.window import parse


def test_each_bound_is_local_midnight_across_a_dst_change():
    w = parse(date(2026, 3, 1), date(2026, 3, 31), 'Europe/Rome')  # summer time starts on 29 March
    assert w.start_at == datetime(2026, 2, 28, 23, tzinfo=UTC)  # CET, +01:00
    assert w.end_at == datetime(2026, 3, 31, 22, tzinfo=UTC)  # 1 April, CEST, +02:00


def test_one_day_is_that_whole_local_day():
    w = parse(date(2026, 10, 25), date(2026, 10, 25), 'Europe/Rome')  # summer time ends: a 25-hour day
    assert (w.end_at - w.start_at).total_seconds() == 25 * 3600


def test_a_leap_year_is_allowed_and_a_day_more_is_not():
    assert parse(date(2028, 1, 1), date(2028, 12, 31), 'UTC').end == date(2028, 12, 31)  # 366 days
    with pytest.raises(HTTPException) as e:
        parse(date(2027, 1, 1), date(2028, 1, 2), 'UTC')  # 367 days
    assert (e.value.status_code, e.value.detail['code']) == (422, 'REQUEST.INVALID_INPUT')


@pytest.mark.parametrize(
    ('start', 'end', 'tz'),
    [
        (date(2026, 5, 2), date(2026, 5, 1), 'UTC'),
        (date(2026, 5, 1), date(2026, 5, 2), 'Mars/Olympus'),
        (date(2026, 5, 1), date(2026, 5, 2), '../etc/passwd'),
        (date(2026, 5, 1), date(2026, 5, 2), ''),
    ],
)
def test_end_before_start_or_an_unknown_zone_is_invalid_input(start, end, tz):
    with pytest.raises(HTTPException) as e:
        parse(start, end, tz)
    assert (e.value.status_code, e.value.detail['code']) == (422, 'REQUEST.INVALID_INPUT')


@pytest.mark.parametrize(
    ('start', 'end', 'tz'),
    [
        (date(9999, 12, 31), date(9999, 12, 31), 'UTC'),  # the midnight after the last day is past year 9999
        (date(1, 1, 1), date(1, 1, 1), 'Asia/Tokyo'),  # local midnight is still year 0 in UTC
    ],
)
def test_a_date_whose_midnight_is_out_of_range_is_invalid_input(start, end, tz):
    with pytest.raises(HTTPException) as e:
        parse(start, end, tz)
    assert (e.value.status_code, e.value.detail['code']) == (422, 'REQUEST.INVALID_INPUT')
