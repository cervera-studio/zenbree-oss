"""What a finished tool call found, as one union by ``kind``: the SSE frames, the stored message and both message
routes carry it (``tool_calls.py`` reads the older stored shapes)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from app.agent.tools.registry import ToolDef, ToolResult
from app.lists.lookup_outcome import Legacy, NoRowMatch, NothingToSearch, Searched, refunded


class EntityCount(BaseModel):
    kind: Literal['count']
    noun: Literal['company', 'person']
    count: int


Summary = EntityCount | Searched | NothingToSearch | NoRowMatch | Legacy
ToolSummary = Annotated[Summary, Field(discriminator='kind')]
SUMMARY: TypeAdapter[Summary] = TypeAdapter(ToolSummary)


def summary_of(tool: ToolDef | None, result: ToolResult, list_info: dict) -> Summary | None:
    """``result.outcome`` when set (an ``ok=False`` no_row_match too), else the entity count of a ``found_noun`` tool.
    Never ``result.data``."""
    if result.outcome:
        return result.outcome
    if not result.ok or not tool:
        return None
    noun = (result.entity or 'company') if tool.found_noun == 'entity' else tool.found_noun
    if noun not in ('company', 'person'):  # no found_noun, or a contact tool that returned no outcome
        return None
    return EntityCount(kind='count', noun=noun, count=list_info.get('rows_added', 0) + list_info.get('rows_updated', 0))


def searched_of(result: ToolResult) -> int | None:
    """People a contact call searched: the units a stage counts as done. Nobody left to search is 0, as ``data.searched``
    was."""
    match result.outcome:
        case Searched() as outcome:
            return outcome.searched
        case NothingToSearch():
            return 0
        case _:
            return None


def with_refund(summary: dict, rows: int) -> dict:
    """The stored ``summary`` after the turn took ``rows`` of its found contacts back as undelivered (#659)."""
    parsed = SUMMARY.validate_python(summary)
    return summary if isinstance(parsed, EntityCount) else SUMMARY.dump_python(refunded(parsed, rows), mode='json')
