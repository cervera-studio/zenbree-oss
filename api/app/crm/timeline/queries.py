"""The reads of the timeline ledger that join other people's state: which rows are due, and why a pending row waits.

A row waits for a reason that has an owner elsewhere (the grant, the membership, the read gate, the kill switch), so
the reason is computed here from that state each time it is asked, and never stored: it cannot go stale.
"""

from __future__ import annotations

from datetime import datetime

from app.core.db import get_conn
from app.crm.activities import MAX_ATTEMPTS
from app.crm.timeline.facts import Code
from app.integrations.providers import HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE

# Over e (the event), h (the owner's HubSpot connection), w (the contact written for them) and g (the gate verdict).
# A hold blocks the contact write only: once the contact exists, the activity is logged, as #528 logs it.
WAIT_SQL = f"""CASE
  WHEN e.owner_id IS NULL THEN NULL
  WHEN (e.owner_id, e.organization_id) NOT IN (SELECT user_id, organization_id FROM zb.live_members)
    THEN '{Code.OWNER_LEFT}'
  WHEN h.id IS NULL OR h.status <> 'connected' OR NOT coalesce(%(contacts)s = ANY(h.granted_scopes), false)
    THEN '{Code.GRANT_REVOKED}'
  WHEN %(disabled)s THEN '{Code.VENDOR_DISABLED}'
  WHEN w.email IS NULL AND g.reason IS NOT NULL AND (g.hold_until IS NULL OR g.hold_until > %(now)s)
    THEN '{Code.HELD}'
END"""

# The write-back start moves forward only, so a row older than it, or a switch that is off, can never be due.
OUTSIDE_SQL = 'h.id IS NOT NULL AND (NOT h.crm_write_back OR e.occurred_at < h.crm_write_back_since)'

JOINS = """
LEFT JOIN user_oauth_connections h ON h.user_id = e.owner_id AND h.provider = 'hubspot'
LEFT JOIN crm_contact_writes w ON w.owner_id = e.owner_id AND w.email = e.email
LEFT JOIN crm_gate_checks g ON g.owner_id = e.owner_id AND g.email = e.email"""

# An incoming email #528 already claimed or logged for this owner: one physical email, one activity.
_NATIVE_DUPLICATE = f"""e.state = 'pending' AND e.kind = 'incoming_email' AND e.message_id IS NOT NULL AND EXISTS (
  SELECT 1 FROM crm_activity_writes a JOIN outreach_inbound_messages m ON m.id = a.source_id
  WHERE a.kind = 'reply' AND a.owner_id = e.owner_id
    AND (a.activity_id IS NOT NULL OR a.attempts < {MAX_ATTEMPTS})
    AND nullif(lower(btrim(m.rfc_message_id, '<> ')), '') = e.message_id)"""

_DUE_SQL = f"""
SELECT e.*, {OUTSIDE_SQL} AS outside, {_NATIVE_DUPLICATE} AS native_duplicate,
       e.state = 'claimed' AND e.claimed_at > now() - interval '1 minute' AS too_soon,
       h.provider_account_id AS current_portal, h.provider_owner_id, h.user_id AS connected_user,
       coalesce(%(companies)s = ANY(h.granted_scopes), false) AS companies,
       w.contact_id, w.company_id, g.email IS NOT NULL AS gated
FROM crm_timeline_events e {JOINS}
WHERE e.state IN ('pending', 'claimed') AND e.owner_id IS NOT NULL
  AND ({OUTSIDE_SQL} OR ({_NATIVE_DUPLICATE}) OR ({WAIT_SQL}) IS NULL)
ORDER BY e.occurred_at
LIMIT %(batch)s"""


def params(now: datetime, *, disabled: bool = False) -> dict:
    return {
        'contacts': HUBSPOT_CONTACTS_WRITE,
        'companies': HUBSPOT_COMPANIES_WRITE,
        'now': now,
        'disabled': disabled,
    }


def due(now: datetime, batch: int) -> list[dict]:
    """As System: the rows to work now, oldest first. A row that waits is not returned, so it never takes a slot."""
    with get_conn() as conn:
        return conn.execute(_DUE_SQL, {**params(now), 'batch': batch}).fetchall()
