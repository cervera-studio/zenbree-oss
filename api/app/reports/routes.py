"""The campaign report (#942): what each campaign of the active project did in a date range. Read-only."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db import get_conn
from app.integrations.instantly_sync import outcomes
from app.projects.routes import current_project
from app.reports import native
from app.reports.models import CampaignReport
from app.reports.window import parse

router = APIRouter(prefix='/v1/reports')


@router.get('/campaigns', response_model=CampaignReport)
def campaign_report(
    start: Annotated[date, Query(alias='from')],
    end: Annotated[date, Query(alias='to')],
    tz: Annotated[str, Query(max_length=64)],
    project_id: str = Depends(current_project),
) -> dict:
    """``from`` and ``to`` are dates in ``tz``, both included."""
    window = parse(start, end, tz)
    with get_conn() as conn:
        rows = native.campaigns(conn, project_id, window)
        rows += outcomes.campaign_counts(conn, project_id, window.start_at, window.end_at)
        coverage = outcomes.coverage(conn, project_id)
    return {
        'window': {'start': window.start, 'end': window.end, 'time_zone': window.time_zone},
        'campaigns': rows,
        'instantly': None
        if coverage is None
        else {
            'mode': coverage.mode,
            'complete_until': coverage.complete_until,
            'failing_since': coverage.failing_since,
            'last_error_code': coverage.last_error_code,
        },
    }
