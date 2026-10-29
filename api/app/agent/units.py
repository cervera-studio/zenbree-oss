"""What a tool call can deliver, resolved once before it runs (#418): the credits the preflight holds, the bound the
handler keeps and the units its plan stage counts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.agent import plan as plans
from app.agent.tools import registry
from app.billing.price import Price, Quote
from app.core.db import get_conn
from app.llm.base import ToolCall

FREE = Price(0, (), lambda cells: False)  # a tool with no Price: its quote still counts rows for its plan stage


def _args(call: ToolCall) -> dict:
    """A model can send arguments that parse to no object: they price as none, and the handler's crash fails the
    call alone."""
    return call.input if isinstance(call.input, dict) else {}


def search_behind(call: ToolCall, session_id: str) -> ToolCall:
    """The search that filled the list of a more_rows call, read in that call's own chat only (#259): more_rows
    re-runs it, so it is the plan stage the call pays for and what the call can deliver."""
    try:
        list_id = str(UUID(str(_args(call).get('list_id'))))
    except ValueError:
        return call
    with get_conn() as conn:
        row = conn.execute(
            'SELECT l.last_search FROM lists l'
            ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
            ' WHERE l.id = %s AND s.id = %s',
            (list_id, session_id),
        ).fetchone()
    search = row and row['last_search']
    return ToolCall(call.id, search['tool'], search.get('args') or {}) if search and search.get('tool') else call


@dataclass(frozen=True)
class PricedCall:
    call: ToolCall
    tool: registry.ToolDef
    quote: Quote
    behind: str  # the tool whose plan stage the call pays for: more_rows pays for the search it re-runs

    @classmethod
    def of(cls, call: ToolCall, tool: registry.ToolDef, session_id: str) -> PricedCall:
        behind = search_behind(call, session_id) if call.name == 'more_rows' else call
        args = _args(behind)
        price = tool.price or FREE
        if behind.name == 'search_companies' and args.get('source') == 'aiark' and not args.get('local_query'):
            quote = Quote(price, plans.AIARK_COMPANY_PAGE)  # AI Ark bills every row of its page
        else:
            quote = price.quote(args, tool.input_schema)
        return cls(call, tool, quote, behind.name)
