"""A stored chat message as ``GET /v1/chat/sessions/{id}`` sends it."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from app.agent.tool_calls import ToolCalls


class ChatMessage(BaseModel):
    id: UUID
    session_id: UUID
    role: Literal['user', 'assistant']
    content: str
    tool_calls: ToolCalls
    sources: list[dict[str, Any]] | None
    credits_used: int
    status: Literal['success', 'partial', 'failed'] | None
    terminal_code: str | None
    question: dict[str, Any] | None
    created_at: datetime
