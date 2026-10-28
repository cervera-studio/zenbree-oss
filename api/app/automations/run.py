"""What one run of an automation holds while it goes, and the one way a step may write (#939).

A step calls vendors, then commits its outcome in ``guarded``: one transaction that locks the automation and the run,
refuses when the run was cancelled, paused or lost its hold, and locks the credit hold so a charge is paid.
``write_hubspot`` is the one step whose vendor half writes: it writes each person to HubSpot and records them in
``crm_contact_writes`` as it goes, so a stop after that half leaves them written. A step that wrote nothing can be
tried again; one that charged or wrote outside our database cannot.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.automations.models import Definition
from app.billing.tab import HoldGone, Tab
from app.core.db import get_conn
from app.integrations.instantly_client import Campaign


class Stopped(Exception):
    """The run may not write any more: ``code`` is why (``cancelled``, ``automation_paused``, ``reservation_expired``,
    ``run_not_running``). Raised before the step's outcome is stored, so the step charged nothing. Raised by the
    ``guarded`` that stores it, it comes after the vendor half: ``write_hubspot`` has written its people by then
    (``runner.AFTER_STOP``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StepFailed(Exception):
    """The step did not do its work. ``retry``: nothing was charged or written outside our database, so trying
    again is safe."""

    def __init__(self, code: str, *, retry: bool = True, output: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry = retry
        self.output = output or {}  # what the step did before it failed


@dataclass
class Outcome:
    """What a step decided and did, as the run's step row records it: counts and ids, never a person's data."""

    decision: str
    output: dict = field(default_factory=dict)
    credits: int = 0
    keep: list[str] | None = None  # who stays in play for the next step; None: everybody who was
    unreached: list[str] = field(default_factory=list)  # who the step did not reach: out of play, not taken in


@dataclass(frozen=True, slots=True)
class Handoff:
    """Who the Instantly step would send, and the campaign and workspace it read. Held only when the run succeeds."""

    row_ids: list[str]
    workspace_id: str
    campaign: Campaign


@dataclass
class Run:
    id: str
    org_id: str
    user_id: str
    project_id: str
    automation_id: str
    definition: Definition
    version: int
    review: bool
    credit_cap: int
    hold_id: str | None
    budget: int  # what the hold covers: the cap, or less when the wallet is short
    target_row_id: str | None = None  # a webhook run's one person; None: the best of the list
    target_account_id: str | None = None  # a signal run's account: its people only, and only it is searched
    spent: int = 0
    taken: list[str] = field(default_factory=list)  # the people the run took in
    row_ids: list[str] = field(default_factory=list)  # of those, who is still in play after each step
    # People a step did not reach (the cap, a vendor that did not answer): not marked as taken, first in the next run.
    unreached: set[str] = field(default_factory=set)
    summary: dict[str, Any] = field(default_factory=dict)
    handoff: Handoff | None = None

    @property
    def remaining(self) -> int:
        return max(self.budget - self.spent, 0)

    @property
    def list_id(self) -> str:
        return str(self.definition.list_id)

    @property
    def cap_hit(self) -> bool:
        return bool(self.unreached) and self.remaining == 0 and self.definition.spends

    def call_id(self, position: int) -> str:
        return f'{self.id}:{position}'


@contextmanager
def guarded(run: Run) -> Iterator[tuple[Any, Tab | None]]:
    """``(connection, tab)`` for the one transaction that stores a step's outcome. ``Stopped`` before any write."""
    with get_conn() as conn:
        automation = conn.execute(
            'SELECT enabled FROM automations WHERE id = %s FOR UPDATE', (run.automation_id,)
        ).fetchone()
        row = conn.execute(
            'SELECT status, cancel_requested FROM automation_runs WHERE id = %s FOR UPDATE', (run.id,)
        ).fetchone()
        if not row or row['status'] != 'running':
            raise Stopped('run_not_running')
        if row['cancel_requested']:
            raise Stopped('cancelled')
        if not automation or not automation['enabled']:
            raise Stopped('automation_paused')
        tab = None
        if run.hold_id:
            tab = Tab.stored(conn, run.hold_id)
            try:
                tab.lock(conn)  # until this commit: nothing closes the hold under the charges
            except HoldGone:
                raise Stopped('reservation_expired') from None
        yield conn, tab
