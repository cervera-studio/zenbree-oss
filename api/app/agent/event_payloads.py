"""The data of each chat SSE event, one model per event.

``ChatEventPayloads`` has one field per event name and is the one list of names (``CHAT_EVENTS``). It is attached to
``POST /v1/chat`` so /openapi.json, and with it ``web/lib/api/schema.gen.ts``, carries the shapes. The web side
(``web/lib/chat/event-payloads.ts``) is checked against them at compile time. The tests feed every frame the
agent tests produce through these models (``api/tests/conftest.py``).

A field that the loop leaves out of a frame is optional here; ``extra='forbid'`` makes a key the loop adds fail too.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agent.summary import ToolSummary


class _Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SessionPayload(_Payload):
    session_id: str
    job_id: str


class TextPayload(_Payload):
    content: str


class TextDroppedPayload(_Payload):
    pass


class AgentThinkingPayload(_Payload):
    turn_seq: int


class ToolStartPayload(_Payload):
    tool_id: str
    tool_name: str
    tool_input: dict


class ToolResultPayload(_Payload):
    tool_id: str
    tool_name: str
    result_preview: str
    status: Literal['success', 'failed']
    summary: ToolSummary | None = None
    brief: dict | None = None
    memory_saved: str | None = None
    campaign: dict | None = None
    error_code: str | None = None


class CreditsPayload(_Payload):
    """What ``tool_id`` cost. A refund frame, at the end of a turn, gives back what it charged and carries the call's updated summary."""

    tool_id: str
    tool_name: str
    cost: int
    remaining: int
    refund: bool | None = None
    summary: ToolSummary | None = None


class ListUpdatedPayload(_Payload):
    list_id: str
    entity: str | None
    rows_added: int | None = None
    rows_updated: int | None = None
    row_count: int | None = None
    rows_scored: int | None = None


class StagesPayload(_Payload):
    stages: list[dict]
    phase: str | None
    credits_spent: int
    retry_stage: int | None
    list_id: str | None


class ErrorPayload(_Payload):
    message: str
    code: str


class MessageSavedPayload(_Payload):
    message_id: str
    status: Literal['success', 'partial', 'failed']
    question: dict | None = None


class DonePayload(_Payload):
    sources: list[dict]
    list_id: str | None
    job_id: str


class ChatEventPayloads(BaseModel):
    """Never built: its field names are the chat event names and its field types their data."""

    session: SessionPayload
    text: TextPayload
    text_dropped: TextDroppedPayload
    agent_thinking: AgentThinkingPayload
    tool_start: ToolStartPayload
    tool_result: ToolResultPayload
    credits: CreditsPayload
    list_updated: ListUpdatedPayload
    stages: StagesPayload
    error: ErrorPayload
    message_saved: MessageSavedPayload
    done: DonePayload


# The 200 of POST /v1/chat: FastAPI puts the model under `text/event-stream` and into /openapi.json.
CHAT_FRAMES: dict[int | str, dict[str, Any]] = {
    200: {'model': ChatEventPayloads, 'description': 'SSE frames; `ChatEventPayloads` has the data of each event'}
}


def check_payload(event: str, data: dict) -> None:
    """Raise when ``data``, as it goes on the wire, is not the payload of chat event ``event`` (a test helper)."""
    field = ChatEventPayloads.model_fields.get(event)
    if field is None or field.annotation is None:
        raise AssertionError(
            f'{event} is not a chat event: add it to ChatEventPayloads in api/app/agent/event_payloads.py'
        )
    try:
        field.annotation.model_validate_json(json.dumps(data, default=str))
    except ValidationError as exc:
        raise AssertionError(
            f'the {event} frame does not match {field.annotation.__name__}: edit the emit site, or the model in '
            f'api/app/agent/event_payloads.py and web/lib/chat/event-payloads.ts\n{exc}'
        ) from exc
