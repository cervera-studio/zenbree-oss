"""The HubSpot step (#939): the contact and its company of each person with an email, through the write-back of
``app.crm.contacts``.

What it keeps from the sweep of that module: only when write-back is on and the grant may write; never a person the
read gate holds; an existing contact only gains empty properties. A person written before is not written again
(``crm_contact_writes``), and a contact HubSpot already has is found by its email, so writing a person again after a
death between the write and its record repeats nothing. Each person is recorded as they are written, so a failure
midway keeps what was done. A step that failed after a write is not tried again by itself, and a later run does not
reach the people it left, because a definition that adds to a campaign has already taken them in.

A HubSpot write is hard to undo, so a run in review writes nobody: the step holds its people, and the task that approval
queues (``held_writes``) writes them, after the rules are checked again.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.admin.settings import is_disabled
from app.automations import gate
from app.automations.models import Definition, WriteHubspot
from app.automations.run import Outcome, Run, StepFailed
from app.core.db import get_conn
from app.core.errors import is_err
from app.crm import contacts
from app.integrations.providers import HUBSPOT, HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE
from app.lists.lookups import deliverable, email_lookup
from app.outreach.dispatcher import _access_token

logger = structlog.get_logger()
# People a step writes, at most: each takes up to five HubSpot calls.
MAX_PEOPLE = 25


def _people(
    user_id: str, project_id: str, list_id: str, row_ids: list[str], definition: Definition | None
) -> tuple[dict | None, list[dict], dict]:
    """``(connection, people to write, counts of the ones left out)``. With a ``definition`` the people were taken
    some time ago, so the rules that kept them out of the run apply again."""
    with get_conn() as conn:
        connection = conn.execute(
            'SELECT *, %s = ANY(granted_scopes) AS contacts, %s = ANY(granted_scopes) AS companies'
            " FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'"
            ' AND crm_write_back',
            (HUBSPOT_CONTACTS_WRITE, HUBSPOT_COMPANIES_WRITE, user_id),
        ).fetchone()
        if not connection or not connection['contacts']:
            return None, [], {}
        refused = gate.rescreen(conn, project_id, user_id, definition) if definition else lambda _: None
        with_email = [p for p in gate.load(conn, list_id, row_ids) if deliverable(email_lookup(p['cells'], {}))]
        rows = [p for p in with_email if not refused(p['cells'])]
        refused_since = len(with_email) - len(rows)
        emails = [str(p['cells']['email']).strip().lower() for p in rows]
        written = {
            r['email']
            for r in conn.execute(
                'SELECT email FROM crm_contact_writes WHERE owner_id = %s AND email = ANY(%s)', (user_id, emails)
            )
        }
        held = {
            r['email']
            for r in conn.execute(
                'SELECT email FROM crm_gate_checks WHERE owner_id = %s AND email = ANY(%s) AND reason IS NOT NULL'
                ' AND (hold_until IS NULL OR hold_until > %s)',
                (user_id, emails, datetime.now(UTC)),
            )
        }
    people, counts = [], {'already_written': 0, 'held_by_hubspot_gate': 0, 'refused_since': refused_since}
    for row, email in zip(rows, emails, strict=True):
        cells = row['cells']
        if email in written:
            counts['already_written'] += 1
        elif email in held:
            counts['held_by_hubspot_gate'] += 1
        else:
            people.append(
                {
                    'owner_id': user_id,
                    'email': email,
                    'first_name': cells.get('first_name'),
                    'last_name': cells.get('last_name'),
                    'role': cells.get('title'),
                    'company_name': cells.get('company_name'),
                    'companies': connection['companies'],
                }
            )
    return connection, people[:MAX_PEOPLE], counts


def _record(user_id: str, email: str, written: tuple[str, str | None, bool]) -> None:
    contact_id, company_id, created = written
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO crm_contact_writes (owner_id, email, contact_id, company_id, created)'
            ' VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
            (user_id, email, contact_id, company_id, created),
        )


async def write(
    run_id: str, user_id: str, project_id: str, list_id: str, row_ids: list[str], definition: Definition | None = None
) -> dict:
    if is_disabled('vendor_disabled:hubspot'):
        raise StepFailed('vendor_disabled', retry=False)
    connection, people, counts = _people(user_id, project_id, list_id, row_ids, definition)
    if connection is None:
        raise StepFailed('hubspot_off', retry=False)
    done = {**counts, 'created': 0, 'existing': 0}
    if not people:
        return done
    token = await _access_token(connection, HUBSPOT)
    if token is None:
        raise StepFailed('hubspot_token', retry=True)  # nothing written yet
    wrote = False
    for person in people:
        try:
            written = await contacts.write_person(token, person)
            if not is_err(written):
                wrote = True
                _record(user_id, person['email'], written.value)
        except Exception as exc:
            if not wrote:  # HubSpot holds nobody from this step yet, so it may be tried again
                raise
            # The exception text may carry the person's email: only its type is logged.
            logger.error('automation.hubspot_crashed', run_id=run_id, error_type=type(exc).__name__)
            raise StepFailed('hubspot_failed', retry=False, output=done) from exc
        if is_err(written):
            logger.warning('automation.hubspot_failed', run_id=run_id, error_code=written.error.code)
            raise StepFailed('hubspot_failed', retry=False, output=done)
        done['created' if written.value[2] else 'existing'] += 1
    return done


async def fetch_hubspot(run: Run, step: WriteHubspot, position: int) -> dict:
    if run.review:
        return {'held_for_approval': len(run.row_ids)}
    return await write(run.id, run.user_id, run.project_id, run.list_id, run.row_ids)


def _wrote(done: dict) -> Outcome:
    return Outcome(f'wrote {done["created"] + done["existing"]} contacts to HubSpot', done)


def apply_hubspot(conn, tab, run: Run, step: WriteHubspot, position: int, done: dict) -> Outcome:
    if run.review:
        conn.execute('UPDATE automation_runs SET hubspot_row_ids = %s::uuid[] WHERE id = %s', (run.row_ids, run.id))
        return Outcome(f'{done["held_for_approval"]} people wait for approval, none written to HubSpot', done)
    return _wrote(done)


def stopped_hubspot(run: Run, done: dict) -> Outcome | None:
    """What the step did when a cancel or a pause landed during its write: HubSpot holds those contacts, and each is
    recorded, so the step succeeded. ``None`` when it wrote nobody: a run in review only holds its people."""
    if run.review or not done['created'] + done['existing']:
        return None
    return _wrote(done)
