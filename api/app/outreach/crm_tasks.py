"""The HubSpot task step (E13.4, #123): a campaign step that hands a person to a colleague instead of writing.

Planned and claimed like every other step (``outreach_campaign_sends``, channel ``hubspot_task``), but never held
for approval: whoever gets the task is the one who reads the draft and sends it by hand. Delivery creates a
HubSpot task with the drafted message, on the person's contact (created when HubSpot has none, as write-back
does) and its company, assigned to the contact's owner, else the company's, else the connected user.

A person whose record belongs to a colleague is held from every send by the read gate; this step is the one
thing that reaches them (``TASK_FOR_OWNER`` in ``app.outreach.due``).

As for a send, an outcome that cannot be known (a timeout after the request left, a 5xx) leaves the claim
``sending`` and is not retried by itself: a second task would be a second job for the colleague.
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime

import structlog

from app.admin.settings import is_disabled
from app.auth.middleware import creator_not_banned
from app.core import tasks
from app.core.db import get_conn
from app.core.errors import Result, is_err, ok
from app.crm import contacts, hubspot_client, hubspot_writes
from app.integrations.providers import HUBSPOT, HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE
from app.outreach import holds, send_states, sending
from app.outreach.claims import advance, claim
from app.outreach.due import GATE_SEND
from app.outreach.freeze import frozen_values

logger = structlog.get_logger()

CHANNEL = 'hubspot_task'
BATCH = 40

# Claims whose campaign and person are still live, of owners whose HubSpot grant can write and has write-back on, oldest first.
_DUE_SQL = f"""
SELECT s.id, s.organization_id, s.campaign_id, s.target_id, s.owner_id, s.step, s.subject, s.body, c.name AS campaign,
       lower(btrim(t.email)) AS email, t.first_name, t.last_name, t.role, t.company_name,
       %s = ANY(h.granted_scopes) AS companies
FROM outreach_campaign_sends s
JOIN outreach_campaigns c ON c.id = s.campaign_id AND c.status = 'active'
JOIN outreach_campaign_targets t ON t.id = s.target_id AND t.status = 'active' AND t.next_step = s.step
JOIN user_oauth_connections h
  ON h.user_id = s.owner_id AND h.provider = 'hubspot' AND h.status = 'connected' AND %s = ANY(h.granted_scopes)
  AND h.crm_write_back
WHERE s.channel = '{CHANNEL}' AND s.status IN ('approved', 'failed') AND s.attempts < %s
  AND {creator_not_banned('c.owner_id')}
  {GATE_SEND} AND {holds.not_held('c.project_id', 's.channel', 't.email', 't.linkedin_url')}
ORDER BY s.created_at
LIMIT %s"""


def can_run(conn, owner_id) -> bool:
    """Whether this user's HubSpot grant can create tasks: connected, with the contacts write permission and the
    write-back switch on (a teammate's step writes through the owner's grant, so the owner's consent applies)."""
    return bool(
        conn.execute(
            "SELECT 1 FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot'"
            " AND status = 'connected' AND %s = ANY(granted_scopes) AND crm_write_back",
            (owner_id, HUBSPOT_CONTACTS_WRITE),
        ).fetchone()
    )


def plan(conn, t: dict, n: int, step: dict) -> str | None:
    """Claim the step for one person: ``approved`` at once, or skipped when nothing identifies them in HubSpot."""
    email = sending.single_address(t['email'])
    if email is None:
        shown = ((t['email'] or '').strip() or t['linkedin_url'] or '-')[:500]
        if claim(conn, t, n, 'skipped', recipient=shown, channel=CHANNEL, code='NO_EMAIL'):
            advance(conn, t['id'], n)
        return 'skipped_no_email'
    values = frozen_values(t)
    subject, body = (
        sending.render_values(step.get('subject') or '', values),
        sending.render_values(step['body'], values),
    )
    if is_err(subject) or is_err(body):
        code = subject.error.code if is_err(subject) else body.error.code if is_err(body) else None
        claim(conn, t, n, 'refused', recipient=email, channel=CHANNEL, code=code)
        return 'refused_copy'
    if len(subject.value) > sending.MAX_SUBJECT or len(body.value) > sending.MAX_BODY:
        claim(conn, t, n, 'refused', recipient=email, channel=CHANNEL, code='COPY_TOO_LONG')
        return 'refused_copy'
    planned = claim(conn, t, n, 'approved', recipient=email, channel=CHANNEL, subject=subject.value, body=body.value)
    return 'task_planned' if planned else None


def task(s: dict, owner_id: str | None, due: datetime) -> dict[str, str]:
    """The HubSpot task for one claim. The draft goes in as text: HubSpot shows the body as HTML."""
    note = ' · '.join(('Zenbree', s['campaign'], f'step {s["step"]}'))
    parts = [f'<strong>{html.escape(s["subject"])}</strong>'] if s['subject'] else []
    parts += [html.escape(s['body']).replace('\n', '<br>'), f'— {html.escape(note)}']
    who = ' '.join(p for p in (s['first_name'], s['last_name']) if p) or s['email']
    props = {
        'hs_timestamp': str(int(due.timestamp() * 1000)),
        'hs_task_subject': f'{who}: {s["subject"] or s["campaign"]}'[:300],
        'hs_task_body': '<br><br>'.join(parts),
        'hs_task_status': 'NOT_STARTED',
        'hs_task_type': 'EMAIL' if s['subject'] else 'TODO',
    }
    return {**props, 'hubspot_owner_id': owner_id} if owner_id else props


async def _assignee(token: str, s: dict, me: str | None) -> Result[str | None]:
    """The contact's HubSpot owner, else its company's, else the connected user."""
    read = await hubspot_client.read_contacts(token, [s['email']], ('hubspot_owner_id', 'associatedcompanyid'))
    if is_err(read):
        return read
    contact = read.value.get(s['email']) or {}
    if owner := str(contact.get('hubspot_owner_id') or '').strip():
        return ok(owner)
    if company_id := str(contact.get('associatedcompanyid') or '').strip():
        companies = await hubspot_client.read_companies(token, [company_id])
        if is_err(companies):
            return companies
        if owner := str((companies.value.get(company_id) or {}).get('hubspot_owner_id') or '').strip():
            return ok(owner)
    return ok(me)


def _due(now: datetime) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            _DUE_SQL, (HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE, sending.MAX_ATTEMPTS, now, BATCH)
        ).fetchall()


def _connection(owner_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'",
            (owner_id,),
        ).fetchone()


def _take(s: dict, now: datetime) -> bool:
    """``sending`` before HubSpot is called, only while the claim is still waiting."""
    with get_conn() as conn:
        return bool(
            conn.execute(
                "UPDATE outreach_campaign_sends SET status = 'sending', attempts = attempts + 1, attempted_at = %s,"
                f' error_code = NULL WHERE id = %s AND {send_states.guard("sending")}',
                (now, s['id']),
            ).rowcount
        )


def _settle(s: dict, status: str, *, code: str | None = None, task_id: str = '', at: datetime | None = None) -> None:
    with get_conn() as conn:
        moved = send_states.move(
            conn,
            s['id'],
            ('sending',),
            status,
            set_='error_code = %s, provider_receipt_id = %s, delivered_at = %s',
            params=(code, task_id or None, at),
        )
        if moved and status == 'sent':
            advance(conn, s['target_id'], s['step'])


def _unknown(error) -> bool:
    """HubSpot may have made the task: the request left and no answer, or a server error, came back."""
    status = error.context.get('status') or 0
    return error.code in ('HUBSPOT.TRANSPORT', 'HUBSPOT.INVALID_JSON') or status >= 500


async def _deliver(token: str, s: dict, me: str | None, now: datetime) -> str:
    """One claim. ``'held'``: HubSpot could not be read, nothing was taken, the owner waits for the next beat."""
    person = {**s, 'owner_id': str(s['owner_id'])}
    written = await contacts.write_person(token, person)
    if is_err(written):
        return 'held'
    contact_id, company_id, _created = written.value
    assignee = await _assignee(token, s, me)
    if is_err(assignee) or not _take(s, now):
        return 'held'
    made = await hubspot_writes.log_activity(token, 'tasks', task(s, assignee.value, now), contact_id, company_id)
    if not is_err(made):
        _settle(s, 'sent', task_id=made.value, at=now)
        return 'task_created'
    logger.warning('campaign.task.failed', send_id=str(s['id']), error_code=made.error.code)
    if _unknown(made.error):
        with get_conn() as conn:
            send_states.keep_sending(conn, s['id'], 'UNKNOWN_OUTCOME')
        return 'task_unknown'
    _settle(s, 'failed', code=made.error.code[:100])
    return 'task_failed'


async def deliver_due(now: datetime, access_token) -> Counter:
    """Create the task of every waiting claim. A user whose HubSpot is in trouble waits for the next beat.

    Each owner's claims run as ``Member(campaign org, owner)``: the task is written with their grant, so an owner who
    left the organization, or is banned, gets none written for it, and nothing is retried for them."""
    counts: Counter = Counter()
    if is_disabled('vendor_disabled:hubspot'):
        return counts
    owners: dict[tuple[str, str], list[dict]] = {}
    for s in _due(now):
        owners.setdefault((str(s['organization_id']), str(s['owner_id'])), []).append(s)
    for (org, owner), claims in owners.items():
        with tasks.as_member(org, owner):
            await _deliver_owner(owner, claims, now, access_token, counts)
    return counts


async def _deliver_owner(owner: str, claims: list[dict], now: datetime, access_token, counts: Counter) -> None:
    connection = _connection(owner)
    token = await access_token(connection, HUBSPOT) if connection else None
    if connection is None or token is None:
        return
    for s in claims:
        outcome = await _deliver(token, s, connection.get('provider_owner_id'), now)
        if outcome == 'held':
            return
        counts[outcome] += 1
