"""Tool registry: one `register(ToolDef(...))` per tool, next to its handler.

Slimmed from the earlier implementation `API/app/chat/tools/registry.py` (678 LOC): no delivery
kinds, clusters, flags or KG gates. Handler modules register at import time;
`app.agent.tools.handlers` imports them all.

Contract every handler follows (see docs/contracts-m1.md):
- async `handler(args, ctx) -> ToolResult`, never raises for vendor errors
- `rows` get appended to the job's List, `row_updates` patch existing rows
- a paid tool declares a `Price` (`app/billing/price.py`); a tool with none is
  free. The store bills each `row_update` that `Price.found` accepts and that
  lands, at most the rows the call was quoted for (`app/lists/delivery.py`); the
  handler returns no charge
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.agent.hosts import Named
from app.billing.price import Price
from app.lists.lookup_claims import Claimer
from app.lists.lookup_outcome import Contact, ContactOutcome


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    job_id: str | None = None
    list_id: str | None = None
    session_id: str | None = None
    project_id: str | None = None  # the chat's project (#307): its LinkedIn account
    named: Named = field(default_factory=Named)  # #407, #437: the only sites fetch_page may read
    max_units: int | None = None  # #418: the units the credit preflight held; a paid handler does no more
    claims: Claimer | None = None  # the call's lookup claims; None where no job runs (tests)

    async def claim(self, key: Contact, row_ids: Sequence[str]) -> None:
        """A lookup handler names the rows it will search before its first vendor call: readers see them as
        ``searching`` until the loop stores the call's answers (``app.lists.lookup_claims``)."""
        if self.claims and row_ids:
            await asyncio.to_thread(self.claims, key, row_ids)


@dataclass
class ToolResult:
    ok: bool
    data: dict = field(default_factory=dict)  # what the model reads back
    rows: list[dict] = field(default_factory=list)
    entity: Literal['company', 'person'] | None = None
    row_updates: list[dict] = field(default_factory=list)  # {'row_id': str, 'cells': dict, 'if_empty'?: str}
    error: str | None = None
    brief: dict | None = None  # set_brief: what the agent understood, shown beside the chat
    memory_saved: str | None = None  # remember: the entry it wrote, shown as the memory note
    campaign: dict | None = None  # draft_campaign: {id, name, target_count}, shown as the draft card
    # why a failed call failed, in the loop's words (a step's error in the chat); unset = the handler's own failure.
    # 'skipped' = nothing ran (a handler's ids matched no row too): the plan's step stays open, not failed.
    error_code: Literal['disabled', 'no_credits', 'over_budget', 'crashed', 'skipped', 'not_connected'] | None = None
    # enrich_email / enrich_phone: what the call answered, counted from its row_updates (lookup_outcome.py)
    outcome: ContactOutcome | None = None

    def __post_init__(self) -> None:
        """A result that writes a contact lookup says what it answered: a handler cannot return a contact result
        that the chat reads as «0 email» (#530)."""
        writes = any(k in ('email_status', 'phone_status') for u in self.row_updates for k in u.get('cells') or {})
        if self.outcome is None and writes:
            raise ValueError('a result that writes email_status or phone_status needs an outcome')

    @classmethod
    def crashed(cls) -> ToolResult:
        return cls(ok=False, error='The tool failed unexpectedly.', error_code='crashed')


FoundNoun = Literal['company', 'person', 'email', 'phone', 'entity']

ToolHandler = Callable[[dict, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str  # what the model sees: when to use, when not, example
    input_schema: dict  # JSON Schema
    handler: ToolHandler
    provider: str | None = None  # vendor name, for the admin kill switch
    # what the tool finds, for the summary card; 'entity' = the entity of the rows it returns; None = nothing countable
    found_noun: FoundNoun | None = None
    price: Price | None = None  # None: a free tool


_REGISTRY: dict[str, ToolDef] = {}


def register(tool: ToolDef) -> ToolDef:
    if tool.name in _REGISTRY:
        raise ValueError(f'tool {tool.name!r} registered twice')
    if tool.price and tool.price.credits <= 0:
        # the loop reads a Price as paid: a 0 would hold nothing yet skip the free-rows check, and divide the low-credit
        # email by zero
        raise ValueError(
            f'tool {tool.name!r} has a Price of {tool.price.credits} credits: a free tool declares no Price'
        )
    _REGISTRY[tool.name] = tool
    return tool


def get(name: str) -> ToolDef | None:
    return _REGISTRY.get(name)


def all_tools() -> list[ToolDef]:
    return list(_REGISTRY.values())


def billed_keys() -> frozenset[str]:
    """Every cell a charge can buy: a turn may still refund it."""
    return frozenset(key for tool in _REGISTRY.values() if tool.price for key in tool.price.keys)


def spec_list() -> list[dict]:
    """Tool specs in the neutral {name, description, input_schema} shape `app.llm` takes."""
    return [{'name': t.name, 'description': t.description, 'input_schema': t.input_schema} for t in _REGISTRY.values()]
