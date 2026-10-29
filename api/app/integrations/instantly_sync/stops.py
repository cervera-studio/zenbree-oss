"""Carry out the Instantly stops of one connection (#931), first in its reconcile pass (``reconcile.sync_one``).

The pass owns the lease, the organization's scope and the kill switch, so this module has none of its own.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.core.db import get_conn
from app.core.errors import is_err
from app.integrations import instantly_client
from app.integrations.instantly_store import HELD
from app.integrations.instantly_sync import store
from app.outreach import stop_outbox
from app.outreach.stop_outbox import Due, Event, Later, NotEnrolled, Refused, Stop, Stopped

PER_PASS = 50
# The stops of one beat stop starting attempts after this. The connections of an organization run one after another
# in one task (50 s soft limit), so the budget is the beat's, not each connection's: a backlog in one connection
# leaves the others their reads.
DRAIN_SECONDS = 15.0
_clock = time.monotonic
_OUTAGE = frozenset({'INSTANTLY.UNREACHABLE', 'INSTANTLY.UNKNOWN_OUTCOME'})
_KEY_REFUSED = frozenset({'INSTANTLY.INVALID_KEY', 'INSTANTLY.PLAN_REQUIRED'})


def deadline() -> float:
    return _clock() + DRAIN_SECONDS


async def drain(binding: store.Binding, key: str, connected_at: datetime, until: float) -> None:
    """Attempts the due stops of the connection made at ``connected_at``, with its key, and starts none after
    ``until`` (``deadline()`` of the beat). Each answer is settled in its own short transaction, never across a call."""
    work = await asyncio.to_thread(_due, binding.connection_id)
    # The stops that need no call settle first: an outage then backs off only the ones that need Instantly.
    work.sort(key=lambda item: _without_call(binding.workspace_id, item) is None)
    for i, item in enumerate(work):
        if _clock() > until:
            break
        event = await remove(key, binding.workspace_id, item, _unchanged)
        if event is None:
            continue
        if isinstance(event, Refused) and event.code in _KEY_REFUSED:
            await asyncio.to_thread(refuse_key, binding.connection_id, connected_at, event.code)
            break
        outage = isinstance(event, Later) and event.code in _OUTAGE
        await asyncio.to_thread(_settle, work[i:] if outage else [item], event)
        if isinstance(event, Later) and event.code != 'INSTANTLY.LEAD_NOT_SETTLED':
            break


def _without_call(workspace_id: str, item: Due) -> Event | None:
    if item.workspace_id != workspace_id:
        return Refused('INSTANTLY.WORKSPACE_CHANGED')
    if item.outcome not in HELD:
        return NotEnrolled()
    return None


async def remove(key: str, workspace_id: str, item: Due, unchanged: Callable[[Stop], Awaitable[bool]]) -> Event | None:
    """What Instantly says of the stop's lead, after the DELETE when one is due. None: the stop changed while its lead
    was read (``unchanged`` re-reads it), so there is nothing to settle and the next pass reads it again."""
    known = _without_call(workspace_id, item)
    if known is not None:
        return known
    lead_id = item.instantly_lead_id
    if lead_id is None:
        found = await instantly_client.find_leads(key, item.campaign_id, [item.email])
        if is_err(found):
            return _failed(found.error.code)
        lead_id = found.value.get(item.email)
        if lead_id is None:
            return Later('INSTANTLY.LEAD_NOT_SETTLED')
    lead = await instantly_client.get_lead(key, lead_id)
    if is_err(lead):
        return _failed(lead.error.code)
    if lead.value is None or lead.value.stopped:
        return Stopped()
    if item.keep_lead:
        return Refused('INSTANTLY.STOP_IN_INSTANTLY')
    # ponytail: an event of the lead itself (its reply in Instantly) can set keep_lead on the stop at any time. This
    # re-read leaves the time from it to Instantly applying the DELETE, a few milliseconds, in which that lead is still
    # deleted. Closing it needs a lock held across the HTTP call, and a slow Instantly would then stall the writer
    # that files the event.
    if not await unchanged(item.stop):
        return None
    gone = await instantly_client.delete_lead(key, lead_id)
    if is_err(gone):
        return _failed(_after_a_good_read(gone.error.code))
    return Stopped()


def _failed(code: str) -> Event:
    if code in ('INSTANTLY.RATE_LIMITED', *_OUTAGE):
        return Later(code)
    return Refused('INSTANTLY.STOP_SCOPE' if code == 'INSTANTLY.MISSING_SCOPE' else code)


def _after_a_good_read(code: str) -> str:
    return 'INSTANTLY.MISSING_SCOPE' if code == 'INSTANTLY.INVALID_KEY' else code


async def _unchanged(stop: Stop) -> bool:
    def read() -> bool:
        with get_conn() as conn:
            return stop_outbox.unchanged(conn, stop)

    return await asyncio.to_thread(read)


def _due(connection_id: str) -> list[Due]:
    with get_conn() as conn:
        return stop_outbox.due(conn, connection_id, PER_PASS)


def _settle(items: list[Due], event: Event) -> None:
    now = datetime.now(UTC)
    with get_conn() as conn:
        for item in items:
            stop_outbox.settle(conn, item.stop, event, now)


def refuse_key(connection_id: str, connected_at: datetime, code: str) -> None:
    with get_conn() as conn:
        stop_outbox.refuse_key(conn, connection_id, connected_at, code)
