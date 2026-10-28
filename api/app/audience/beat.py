"""The audience link beat (#937): link the rows that writers marked, one organization at a time.

The task runs as ``System(DUE_WORK)`` (app/worker.py), which only picks the organizations that have marked rows. Each
one is then linked as ``ForOrg`` of itself, so no read or write of one organization's links can reach another's rows.
A beat links at most ``ORGANIZATIONS * BATCH`` rows and leaves the rest for the next one.
"""

from __future__ import annotations

import time

import structlog
from celery.exceptions import SoftTimeLimitExceeded

from app.audience.link import MAX_ATTEMPTS, link_pending
from app.core.db import get_conn
from app.core.scope import acting_for

logger = structlog.get_logger()

ORGANIZATIONS = 25
# The task's soft limit is 50 s: stop starting organizations before it.
BUDGET_S = 35.0
# One row per organization, so an organization with many old marks takes one place, not the whole pick. A given-up
# row is out of the query and out of its index (idx_audience_link_queue_due), so it never holds its organization first.
DUE_SQL = (
    f'SELECT organization_id FROM audience_link_queue WHERE attempts < {MAX_ATTEMPTS}'
    ' GROUP BY organization_id ORDER BY min(queued_at) LIMIT %s'
)


def due_organizations() -> list[str]:
    """The organizations with marks that are not given up, the one with the oldest mark first."""
    with get_conn() as conn:
        rows = conn.execute(DUE_SQL, (ORGANIZATIONS,)).fetchall()
    return [str(r['organization_id']) for r in rows]


def run() -> dict:
    started = time.monotonic()
    linked = 0
    organizations = 0
    for org in due_organizations():
        if time.monotonic() - started > BUDGET_S:
            break
        try:
            with acting_for(org, 'audience link beat'):
                linked += link_pending(deadline=started + BUDGET_S)
            organizations += 1
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:  # one organization must not stop the others
            logger.error('audience.beat.organization_failed', org_id=org, error_type=type(exc).__name__)
    return {'organizations': organizations, 'linked': linked}
