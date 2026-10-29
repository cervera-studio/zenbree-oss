"""A report's date range (#942): local dates in the user's zone, inclusive, as a half-open UTC range."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors_http import api_error

MAX_DAYS = 366


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date
    time_zone: str
    start_at: datetime
    end_at: datetime  # the midnight after end, excluded


def parse(start: date, end: date, tz: str) -> Window:
    """Each bound is converted on its own, so a window across a DST change keeps its local midnights."""
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError, ValueError:  # ValueError: a key such as '' or '../x'
        raise api_error(422, 'REQUEST.INVALID_INPUT', f'unknown time zone {tz!r}') from None
    if end < start:
        raise api_error(422, 'REQUEST.INVALID_INPUT', '`to` is before `from`')
    if (end - start).days + 1 > MAX_DAYS:
        raise api_error(422, 'REQUEST.INVALID_INPUT', f'the range is longer than {MAX_DAYS} days')
    try:
        start_at, end_at = _midnight(start, zone), _midnight(end + timedelta(days=1), zone)
    except OverflowError:  # a bound whose midnight in UTC falls outside years 1 to 9999
        raise api_error(422, 'REQUEST.INVALID_INPUT', 'the range is outside the dates we can read') from None
    return Window(start=start, end=end, time_zone=tz, start_at=start_at, end_at=end_at)


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)
