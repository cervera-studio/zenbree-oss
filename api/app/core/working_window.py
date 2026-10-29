"""Weekday working hours in a named zone, and the next opening after them.

Two bounded contexts need this and neither owns it, which is the same reason
``app.core.reply_address`` lives here: outreach schedules the next attempt
against the window, and integrations refuses an unattended send outside it.

It arrived with the LinkedIn dispatcher, where it lived in
``app.outreach.domain.linkedin`` while LinkedIn was the only channel that
paced itself. Email needs the identical rule for the identical reason — a
mailbox that sends at 03:00 on a Sunday reads to the receiving side as
exactly what it is — so the functions moved rather than being written twice.
``linkedin`` re-exports them, so every existing caller is unchanged.

Pure: no clock of its own, no persistence. ``now`` is always passed in, and
every function takes its hours as arguments. What the module holds is the
DEFAULTS those callers pass, and those are read from settings at import — see
below for why they are read here and nowhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

#: The zone a window is evaluated in when nobody has said otherwise. Sending
#: is judged in the SENDER's local time — "09:00" means 09:00 where the
#: mailbox lives, not where the beat happens to run.
#:
#: There is still no per-connection zone: this one is applied to every mailbox
#: alike, which is why it is settable at all. A deployment whose users are not
#: in Europe/Rome has no other lever, and pretending the constant was a fact
#: about the world rather than a default was the reason it was never one.
DEFAULT_TIMEZONE = settings.outreach_send_window_timezone

#: The weekday hours an unattended sender works. Here rather than in
#: either context's domain because both read them: integrations refuses a
#: send outside them, outreach schedules the next attempt at their next
#: opening, and a window enforced against one pair of numbers and
#: rescheduled against another is a row that never sends.
#:
#: Resolved from settings ONCE, at this single definition, and every consumer
#: — ``send_policy``, ``send_capacity``, ``send_from_mailbox``,
#: ``list_senders``, ``outreach.domain.lifecycle`` — imports these names from
#: here rather than reading the settings itself. That is what keeps the pair
#: the module docstring warns about from coming apart: a second reader would be
#: a second chance to resolve them differently, and the failure that produces
#: is a row scheduled for an opening the guard does not agree is one.
#:
#: Defaults are 9 and 18, so an environment that sets nothing is unchanged.
#: They are a GUARD and not a preference — see the block above
#: ``outreach_send_window_start_hour`` in ``app/shared/config.py`` for what a
#: deployment is buying when it moves them, and what it is spending.
SEND_WINDOW_START_HOUR = settings.outreach_send_window_start_hour
SEND_WINDOW_END_HOUR = settings.outreach_send_window_end_hour


def timezone_or_default(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def in_working_window(
    now: datetime,
    *,
    timezone: str,
    start_hour: int,
    end_hour: int,
) -> bool:
    local = now.astimezone(timezone_or_default(timezone))
    return local.weekday() < 5 and start_hour <= local.hour < end_hour


def next_working_time(
    now: datetime,
    *,
    timezone: str,
    start_hour: int,
    end_hour: int,
    jitter_minutes: int = 0,
) -> datetime:
    """Return now in-window or the next weekday opening, in UTC."""
    zone = timezone_or_default(timezone)
    local = now.astimezone(zone)
    jitter = timedelta(minutes=max(0, jitter_minutes))
    if local.weekday() < 5 and start_hour <= local.hour < end_hour:
        jittered = local + jitter
        if start_hour <= jittered.hour < end_hour:
            return jittered.astimezone(UTC)
    candidate = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if local >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return (candidate + jitter).astimezone(UTC)
