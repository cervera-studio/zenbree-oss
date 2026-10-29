"""Who may go to Instantly (#929): the one rule, read at preview and again just before each publish.

A person goes only when they have a deliverable email and none of these holds:

- ``excluded``: a project exclusion or a HubSpot unsubscribe (``app.exclusions.rules``, through ``enrollment_verdict``);
- ``replied_or_contacted``: they replied, were excluded in a campaign, or a native campaign already wrote to them;
- ``in_native_campaign``: they sit in an active or paused native campaign of the project that writes email, or whose
  ``block_active_duplicates`` is on;
- ``stopped``: a stop of the person in Instantly is open or done (``app.outreach.holds``): never published again;
- ``already_in_instantly``: Instantly already holds them (or may: an uncertain enrollment) for this project;
- ``crm_hold``: HubSpot says they belong to a customer, an open deal, another owner, or were contacted lately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.exclusions.rules import is_excluded
from app.integrations import instantly_fields
from app.integrations.instantly_store import HELD
from app.outreach import holds
from app.scheduling.runner import enrollment_context, enrollment_verdict

HUBSPOT_STATES = ('connected', 'error')  # an error grant is an owner whose CRM cannot be read: the dispatcher holds too


@dataclass(frozen=True, slots=True)
class Verdict:
    row_id: str
    email: str
    reason: str | None  # None: may go


def hubspot_owner(conn, owner_id: str) -> bool:
    row = conn.execute(
        "SELECT status FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot'", (owner_id,)
    ).fetchone()
    return bool(row and row['status'] in HUBSPOT_STATES)


def crm_blocked(conn, owner_id: str, emails: list[str], fresh_since: datetime | None) -> dict[str, str]:
    """``email -> reason`` of the HubSpot verdicts that hold now. With ``fresh_since`` (a check just ran) a person
    with no verdict newer than it is held too: a failed read must never read as "clear" (fail closed, as the
    dispatcher does)."""
    if not emails:
        return {}
    stored = {
        r['email']: r
        for r in conn.execute(
            'SELECT email, reason, hold_until, checked_at FROM crm_gate_checks WHERE owner_id = %s AND email = ANY(%s)',
            (owner_id, emails),
        ).fetchall()
    }
    now = datetime.now(UTC)
    blocked = {}
    for email in emails:
        row = stored.get(email)
        stale = fresh_since is not None and (row is None or row['checked_at'] < fresh_since)
        if stale or (row and row['reason'] and (row['hold_until'] is None or row['hold_until'] > now)):
            blocked[email] = 'crm_hold'
    return blocked


def _stand_in(project_id: str, owner_id: str) -> dict:
    return {'id': None, 'project_id': project_id, 'owner_id': owner_id, 'block_prior_contacts': True}


def _native_holds(target: dict) -> bool:
    return target['campaign_emails'] or target['campaign_blocks']


def judge(
    conn,
    *,
    project_id: str,
    owner_id: str,
    rows: list[dict],
    enrollment_id: str | None = None,
    crm_fresh_since: datetime | None = None,
    check_crm: bool = True,
) -> list[Verdict]:
    """A verdict for each of ``rows`` (``id``, ``cells``, ``excluded``). The caller holds the project's row lock, so
    a native enrollment in the same instant cannot slip between this read and the write that follows.
    ``enrollment_id``: the batch being retried, whose own rows do not count as "already in Instantly"."""
    campaign = _stand_in(project_id, owner_id)
    context = enrollment_context(conn, campaign)
    # The email the native verifier lets through: a rejected address is blank even when a LinkedIn URL keeps the row.
    checked = [enrollment_verdict(r['cells'], campaign, context, held_by=_native_holds) for r in rows]
    deliverable = [instantly_fields.clean_email({'email': email}) for _, email, _ in checked]
    emails = [e for e in deliverable if e]
    held = {
        r['email']
        for r in conn.execute(
            'SELECT email FROM instantly_leads WHERE project_id = %s AND outcome = ANY(%s) AND email = ANY(%s)'
            ' AND enrollment_id IS DISTINCT FROM %s',
            (project_id, list(HELD), emails, enrollment_id),
        ).fetchall()
    }
    crm = crm_blocked(conn, owner_id, emails, crm_fresh_since) if check_crm and hubspot_owner(conn, owner_id) else {}
    seen: set[str] = set()
    out = []
    for row, (verdict, _, linkedin), email in zip(rows, checked, deliverable, strict=True):
        reason = _reason(row, verdict, email, context, held, crm, seen)
        if reason is None and holds.stopped(conn, project_id, email, linkedin):
            reason = 'stopped'
        out.append(Verdict(str(row['id']), email, reason))
        if email:
            seen.add(email)
    return out


def _reason(row, verdict, email, context, held, crm, seen) -> str | None:
    if row.get('excluded') or is_excluded(context[2], row['cells']):
        return 'excluded'
    if not email:
        return 'no_email'
    if email in seen:
        return 'duplicate_in_selection'
    if verdict == 'hold':
        return 'in_native_campaign'
    if verdict == 'skip':
        return 'replied_or_contacted'
    if email in held:
        return 'already_in_instantly'
    return crm.get(email)
