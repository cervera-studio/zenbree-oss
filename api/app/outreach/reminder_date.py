"""The day a «Non ora» reply asks to be reminded, and the day an out-of-office returns: pure date reading."""

from __future__ import annotations

from datetime import date, timedelta

DEFAULT_REMIND_DAYS = 90
MAX_REMIND_DAYS = 365


def day(raw: object) -> date | None:
    try:
        return date.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None


def remind_on(asked: object, today: date) -> date:
    """The day asked for (a bare month: its 1st, the next one to come), else ``DEFAULT_REMIND_DAYS`` from today;
    never past ``MAX_REMIND_DAYS``."""
    chosen = day(asked)
    month = day(f'{asked}-01') if chosen is None and isinstance(asked, str) else None
    if month:
        chosen = month
        if chosen <= today:  # a month gone by, or this one: the next one to come
            chosen = month.replace(year=today.year)
            if chosen <= today:
                chosen = chosen.replace(year=today.year + 1)
    if chosen is None or chosen <= today:
        chosen = today + timedelta(days=DEFAULT_REMIND_DAYS)
    return min(chosen, today + timedelta(days=MAX_REMIND_DAYS))
