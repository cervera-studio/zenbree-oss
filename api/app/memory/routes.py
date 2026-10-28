"""The user's memory document, read and edited on the web Memory page."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, current_user
from app.memory import store
from app.memory.document import DOC_MAX_CHARS, PROMPT_MAX_CHARS, prompt_head

router = APIRouter(prefix='/v1/me/memory', tags=['Memory'])


class MemoryIn(BaseModel):
    body: str = Field(max_length=DOC_MAX_CHARS)
    locked: bool


class Memory(BaseModel):
    body: str
    locked: bool
    updated_at: datetime | None
    max_chars: int
    prompt_max_chars: int
    truncated: bool


def _out(doc: store.MemoryDocument) -> dict:
    return {
        'body': doc.body,
        'locked': doc.locked,
        'updated_at': doc.updated_at,
        'max_chars': DOC_MAX_CHARS,
        'prompt_max_chars': PROMPT_MAX_CHARS,
        'truncated': prompt_head(doc.body)[1],
    }


@router.get('', response_model=Memory)
def get_memory(user: CurrentUser = Depends(current_user)) -> dict:
    return _out(store.read(UUID(user.id)))


@router.put('', response_model=Memory)
def put_memory(body: MemoryIn, user: CurrentUser = Depends(current_user)) -> dict:
    """The user's own edit: the lock only stops the agent, never the user."""
    return _out(store.save_with_lock(UUID(user.id), body.body, body.locked))
