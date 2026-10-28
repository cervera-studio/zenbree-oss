from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.core.config import settings
from app.core.working_window import SEND_WINDOW_START_HOUR, timezone_or_default

UNKNOWN_RETURN_PAUSE_DAYS = 7


def _zone():
    return timezone_or_default(settings.outreach_send_window_timezone)


def local_today(now: datetime) -> date:
    return now.astimezone(_zone()).date()


def restart_at(day: date) -> datetime:
    return datetime.combine(day, time(SEND_WINDOW_START_HOUR), tzinfo=_zone())


def unknown_return_pause_end(at: datetime) -> datetime:
    return restart_at(local_today(at) + timedelta(days=UNKNOWN_RETURN_PAUSE_DAYS))
