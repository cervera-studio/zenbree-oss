"""What one sending account may do now (#935): ready, waiting for the clock, or held until something changes.

One pure function reads the account's own ``user_oauth_connections`` row and today's usage. The caps, the ramp, the
window and the pacing gap come from ``app.outreach.sending``, the dispatcher's own rules, so the screen and the
send path cannot drift. Only the account is judged here, not a project link or a campaign's pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from app.admin.settings import is_disabled
from app.core.config import settings
from app.outreach import sending


class Hold(StrEnum):
    """Needs a person or an outside event. Declaration order is the precedence of the reason shown."""

    DISCONNECTED = 'disconnected'
    NEEDS_RECONNECT = 'needs_reconnect'
    PAUSED = 'paused'
    CHANNEL_DISABLED = 'channel_disabled'
    VENDOR_DISABLED = 'vendor_disabled'
    THROTTLED = 'throttled'


class Waiting(StrEnum):
    """Time alone clears it. The account takes no work now and resumes by itself at ``resumes_at``."""

    CAPPED = 'capped'
    OUTSIDE_WINDOW = 'outside_mailbox_window'
    PACED = 'paced'


Reason = Literal[
    'disconnected',
    'needs_reconnect',
    'paused',
    'channel_disabled',
    'vendor_disabled',
    'throttled',
    'capped',
    'outside_mailbox_window',
    'paced',
]


@dataclass(frozen=True)
class AccountState:
    hold: Hold | None
    waiting: Waiting | None
    resumes_at: datetime | None
    sent_today: int
    daily_cap: int

    @property
    def state(self) -> Literal['ready', 'waiting', 'held']:
        if self.hold:
            return 'held'
        return 'waiting' if self.waiting else 'ready'

    @property
    def reason(self) -> Hold | Waiting | None:
        return self.hold or self.waiting


_STATUS_HOLDS = {'disconnected': Hold.DISCONNECTED, 'error': Hold.NEEDS_RECONNECT}
# The dispatcher stamps last_used_at with its own clock, which can run a few seconds ahead of the API's.
_CLOCK_SKEW = timedelta(seconds=5)


def _is_rate_limit_rest(row: dict, now: datetime) -> bool:
    return bool(row['last_used_at']) and row['last_used_at'] > now + _CLOCK_SKEW


def account_hold(
    row: dict, now: datetime, vendor_disabled: bool = False, channel_disabled: bool = False
) -> Hold | None:
    if hold := _STATUS_HOLDS.get(row['status']):
        return hold
    if row['paused']:
        return Hold.PAUSED
    if channel_disabled:
        return Hold.CHANNEL_DISABLED
    if vendor_disabled:
        return Hold.VENDOR_DISABLED
    if _is_rate_limit_rest(row, now):
        return Hold.THROTTLED
    return None


def _opening(row: dict, at: datetime, tz: str) -> datetime:
    opening = sending.next_open(
        at,
        timezone=tz,
        start_hour=row['send_window_start_hour'],
        end_hour=row['send_window_end_hour'],
        days=list(row['send_window_days']),
    )
    return opening or at


def _pacing_end(row: dict) -> datetime | None:
    last = row['last_used_at']
    return last + sending.send_gap(str(row['id']), last) if last else None


def _waiting(row: dict, now: datetime, tz: str, used: int, cap: int) -> tuple[Waiting | None, datetime | None]:
    slot_end = _pacing_end(row)
    paced = slot_end is not None and now < slot_end
    if used >= cap:
        waiting, at = Waiting.CAPPED, (sending.day_start(now, tz) + timedelta(days=1)).astimezone(UTC)
    elif not sending.window_open(row, now, tz):
        waiting, at = Waiting.OUTSIDE_WINDOW, now
    elif paced:
        waiting, at = Waiting.PACED, now
    else:
        return None, None
    if paced and slot_end:
        at = max(at, slot_end)
        if waiting is Waiting.PACED and not sending.window_open(row, at, tz):
            waiting = Waiting.OUTSIDE_WINDOW  # the slot ends after the window closes: the window is the cause
    return waiting, _opening(row, at, tz)


def account_state(
    row: dict, *, now: datetime, used_today: int, vendor_disabled: bool = False, channel_disabled: bool = False
) -> AccountState:
    """A hold wins over a wait. ``resumes_at`` is set only when the time is known, and is when the dispatcher can
    send: a rest ends at ``last_used_at`` plus the pacing gap it still enforces, a cap at the next local midnight,
    a window at its next opening, a pacing slot at its end. A time that falls outside the mailbox's window moves
    to the window's next opening."""
    # ponytail: warm-up counts from the row's creation; reconnecting a different address does not restart it.
    cap = sending.daily_cap(row['daily_limit'], row['created_at'], now)
    hold = account_hold(row, now, vendor_disabled, channel_disabled)
    tz = row['send_timezone'] or settings.outreach_send_window_timezone
    if hold:
        slot_end = _pacing_end(row)
        resumes = _opening(row, slot_end, tz) if hold is Hold.THROTTLED and slot_end else None
        return AccountState(hold, None, resumes, used_today, cap)
    waiting, resumes = _waiting(row, now, tz, used_today, cap)
    return AccountState(None, waiting, resumes, used_today, cap)


def mailbox_kill_switches() -> dict[str, dict[str, bool]]:
    """``kill_switches`` of Google and Microsoft. Read them before taking a connection: a settings cache miss takes
    a second pool connection."""
    return {provider: kill_switches(provider) for provider in ('google', 'microsoft')}


def kill_switches(provider: str) -> dict[str, bool]:
    return {
        'vendor_disabled': is_disabled(f'vendor_disabled:{provider}'),
        'channel_disabled': is_disabled('channel_disabled:email'),
    }
