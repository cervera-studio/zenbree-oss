"""The plan stage a run reports (`GET /v1/jobs/{id}` and the `stages` event)."""

from typing import Literal

from pydantic import BaseModel


class StageError(BaseModel):
    code: Literal['provider', 'failed', 'disabled', 'no_credits', 'over_budget', 'crashed', 'not_connected']


class RunStage(BaseModel):
    tool: str
    label: str
    units: int
    done: int
    per_row: bool | None = None
    error: StageError | None = None
