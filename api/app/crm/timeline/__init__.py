"""HubSpot timeline write-back for activity that Zenbree did not send itself (#932).

A source puts facts in with :func:`record`, in the transaction that makes the event true. One sweep, run at the end
of the beat's HubSpot sweeps, reads the feeds (Instantly) and logs what is due. The two sources of #528 (campaign
sends, inbound messages) keep their own ledger, ``crm_activity_writes``; every later source uses :func:`record`, and
needs one member of ``facts.Source`` and one value in the CHECKs of ``crm_timeline_events``.
"""

from __future__ import annotations

from collections import Counter
from time import monotonic

import structlog

from app.crm.contacts import AccessToken
from app.crm.timeline import instantly_feed, writer
from app.crm.timeline.ledger import record, status

__all__ = ['record', 'status', 'sweep']

logger = structlog.get_logger()

# The time kept for the drain when the feeds are slow: the rows already recorded are logged first.
DRAIN_RESERVE_SECONDS = 60


def _ingest(deadline: float) -> Counter:
    counts: Counter = Counter()
    windows = instantly_feed.read_windows()
    for project_id, org_id in instantly_feed.projects(windows):
        if monotonic() > deadline:
            counts['cut'] += 1
            break
        try:
            if recorded := instantly_feed.ingest(project_id, org_id, windows):
                counts['recorded'] += recorded
        except Exception as exc:  # one project's feed never stops the others
            logger.error('crm.timeline.ingest_failed', project_id=project_id, error_type=type(exc).__name__)
            counts['ingest_failed'] += 1
    return counts


async def sweep(access_token: AccessToken, *, deadline: float) -> Counter:
    """Read every feed, then log what is due until ``deadline`` (monotonic, from the start of the task)."""
    counts = _ingest(deadline - DRAIN_RESERVE_SECONDS)
    counts.update(await writer.drain(access_token, deadline))
    return counts
