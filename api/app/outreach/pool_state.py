"""The client's mailbox accounts as one campaign sees them (#935): picked into its pool or not, each with its state and
what blocks it. Activation, the pool route, the preview and the approval queue read this one view."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast, get_args

from app.core.config import settings
from app.integrations.sender_state import AccountState, Hold, account_state
from app.outreach import allocation, sending

# Only a person clears these. A kill switch or a rate-limit rest ends by itself, so a pool left with only those
# still activates, and its people wait as they do behind a full cap.
Blocker = Literal[Hold.DISCONNECTED, Hold.NEEDS_RECONNECT, Hold.PAUSED]
_BLOCKERS: frozenset[Hold] = frozenset(get_args(Blocker))

# A disconnected account the campaign has not picked is gone from the picker. A picked one stays, blocked, so a
# person can untick it.
_ACCOUNTS = """
SELECT u.*, p.sender_id IS NOT NULL AS picked
FROM project_senders ps
JOIN user_oauth_connections u ON u.id = ps.sender_id
LEFT JOIN outreach_campaign_senders p ON p.campaign_id = %s AND p.sender_id = u.id
WHERE ps.project_id = %s AND ps.provider IN ('google', 'microsoft')
  AND (u.status <> 'disconnected' OR p.sender_id IS NOT NULL)
ORDER BY u.provider, u.account_label, u.created_at"""


@dataclass(frozen=True)
class PoolAccount:
    row: dict
    state: AccountState
    picked: bool

    @property
    def blocker(self) -> Blocker | None:
        hold = self.state.hold
        return cast(Blocker, hold) if hold in _BLOCKERS else None


def accounts(
    conn, campaign: Mapping, now: datetime, switches: Mapping[str, Mapping[str, bool]] | None = None
) -> list[PoolAccount]:
    """``switches``: ``sender_state.mailbox_kill_switches()``. They change ``state``, never a blocker."""
    found = []
    for row in conn.execute(_ACCOUNTS, (campaign['id'], campaign['project_id'])).fetchall():
        used = sending.sent_today(conn, row['id'], now, row['send_timezone'] or settings.outreach_send_window_timezone)
        state = account_state(row, now=now, used_today=used, **(switches or {}).get(row['provider'], {}))
        found.append(PoolAccount(row, state, row.pop('picked')))
    return found


def ready(pool: Sequence[PoolAccount]) -> list:
    return [a.row['id'] for a in pool if a.picked and a.blocker is None]


def can_send(pool: Sequence[PoolAccount]) -> bool:
    return bool(ready(pool))


def sender_of(pool: Sequence[PoolAccount], target: Mapping) -> dict | None:
    """The account row ``allocation.predict`` names for ``target`` (``id``, ``sender_id``, ``sender_pinned_at``)."""
    picked = allocation.predict(target, ready(pool))
    return next((a.row for a in pool if a.row['id'] == picked), None)
