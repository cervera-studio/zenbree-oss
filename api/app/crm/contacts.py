"""Write-back of people to HubSpot (E13.2, #527): the contact, and its company, of everyone a campaign wrote to.

A beat sweep, not a hook in the dispatcher: a send is never slowed or failed by HubSpot, and a failure needs no
retry queue, because a person stays "not written" until ``crm_contact_writes`` has their row and the next sweep
takes them again. Only for a connection with write-back on (``app.crm.write_back``), and only for people written
to since it was turned on.

What Zenbree never does here: overwrite a property that has a value, set an owner or a lifecycle stage, or touch
a person the read gate holds.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime

import structlog

from app.admin.settings import is_disabled
from app.core import tasks
from app.core.db import get_conn
from app.core.errors import Result, err, is_err, ok
from app.crm import hubspot_client, hubspot_writes
from app.integrations.providers import HUBSPOT, HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE, OAuthProvider

logger = structlog.get_logger()

AccessToken = Callable[[dict, OAuthProvider], Awaitable[str | None]]

# People per sweep, over all users: each takes up to five HubSpot calls, and the task has four minutes.
BATCH = 40
_READ = ('email', 'firstname', 'lastname', 'jobtitle', 'company', 'associatedcompanyid')
# Addresses at these domains say nothing about the employer: no company is matched or created from them.
_PERSONAL_DOMAINS = frozenset(
    {
        'gmail.com',
        'googlemail.com',
        'outlook.com',
        'outlook.it',
        'hotmail.com',
        'hotmail.it',
        'live.com',
        'live.it',
        'yahoo.com',
        'yahoo.it',
        'icloud.com',
        'libero.it',
        'virgilio.it',
        'tiscali.it',
        'alice.it',
        'proton.me',
        'protonmail.com',
    }
)

# One row per person (the latest target that names them) with a send delivered since write-back went on, not yet
# written, and not held by the read gate now. ``companies``: the grant may write companies too. Only sends of an
# organization the owner is still a live member of: the sweep writes each as Member(that organization, owner), and
# a removed owner's rows would otherwise fill the batch on every sweep.
_DUE_SQL = """
SELECT DISTINCT ON (h.user_id, lower(btrim(t.email)))
       h.user_id AS owner_id, s.organization_id, lower(btrim(t.email)) AS email, t.first_name, t.last_name, t.role,
       t.company_name, %s = ANY(h.granted_scopes) AS companies, h.provider_account_id AS portal_id
FROM user_oauth_connections h
JOIN outreach_campaign_sends s ON s.owner_id = h.user_id AND s.status = 'sent' AND s.delivered_at >= h.crm_write_back_since
JOIN outreach_campaign_targets t ON t.id = s.target_id
WHERE h.provider = 'hubspot' AND h.status = 'connected' AND h.crm_write_back AND %s = ANY(h.granted_scopes)
  AND (h.user_id, s.organization_id) IN (SELECT user_id, organization_id FROM zb.live_members)
  AND coalesce(btrim(t.email), '') <> '' AND btrim(t.email) !~ '[,;[:space:]]'
  AND NOT EXISTS (SELECT 1 FROM crm_contact_writes w
                  WHERE w.owner_id = h.user_id AND w.email = lower(btrim(t.email)))
  AND NOT EXISTS (SELECT 1 FROM crm_gate_checks g
                  WHERE g.owner_id = h.user_id AND g.email = lower(btrim(t.email))
                    AND g.reason IS NOT NULL AND (g.hold_until IS NULL OR g.hold_until > %s))
ORDER BY h.user_id, lower(btrim(t.email)), s.delivered_at DESC
LIMIT %s"""


def company_domain(email: str) -> str | None:
    """The employer's domain as the address gives it, or None for a personal mailbox."""
    domain = email.rpartition('@')[2].strip().lower()
    return domain if domain and '.' in domain and domain not in _PERSONAL_DOMAINS else None


def _wanted(person: dict) -> dict[str, str]:
    props = {
        'firstname': person.get('first_name'),
        'lastname': person.get('last_name'),
        'jobtitle': person.get('role'),
        'company': person.get('company_name'),
    }
    return {k: v.strip() for k, v in props.items() if isinstance(v, str) and v.strip()}


async def _contact(token: str, person: dict) -> Result[tuple[str, bool, bool]]:
    """``(contact id, created by us, already has a company)``. An existing contact only gains the properties it
    has empty; a create that loses a race with another writer is read again as existing."""
    for _attempt in range(2):
        found = await hubspot_client.read_contacts(token, [person['email']], _READ)
        if is_err(found):
            return found
        if existing := found.value.get(person['email']):
            missing = {k: v for k, v in _wanted(person).items() if not str(existing.get(k) or '').strip()}
            if missing:
                filled = await hubspot_writes.update_contact(token, existing['id'], missing)
                if is_err(filled):
                    return filled
            return ok((existing['id'], False, bool(str(existing.get('associatedcompanyid') or '').strip())))
        made = await hubspot_writes.create_contact(token, {'email': person['email'], **_wanted(person)})
        if not is_err(made):
            return ok((made.value, True, False))
        if made.error.code != 'HUBSPOT.CONFLICT':
            return made
    # Refused as existing twice, yet not readable by email: left for the next sweep.
    return err('HUBSPOT.CONFLICT', detail='the contact exists but cannot be read by its email')


def _known_company(owner_id: str, domain: str) -> str | None:
    """The company this user's write-back already linked someone at ``domain`` to. HubSpot's search lags a create
    by seconds: asked right after, it does not find the company we just made, and a colleague of the first person
    written would get a second one (seen on a real portal, #527)."""
    with get_conn() as conn:
        row = conn.execute(
            'SELECT company_id FROM crm_contact_writes WHERE owner_id = %s AND company_id IS NOT NULL'
            " AND split_part(email, '@', 2) = %s ORDER BY written_at DESC LIMIT 1",
            (owner_id, domain),
        ).fetchone()
    return row['company_id'] if row else None


async def _company(token: str, contact_id: str, person: dict) -> Result[str | None]:
    """The company the contact is now linked to: the one we linked a colleague to, the portal's own by domain, or a
    new one. None for a personal mailbox, where nothing says who the employer is."""
    domain = company_domain(person['email'])
    if domain is None:
        return ok(None)
    if known := _known_company(str(person['owner_id']), domain):
        linked = await hubspot_writes.associate(token, contact_id, known)
        if not is_err(linked):
            return ok(known)
        if linked.error.code != 'HUBSPOT.HTTP_ERROR':  # anything but "that company is gone": not ours to work around
            return linked
    found = await hubspot_writes.find_company(token, domain)
    if is_err(found):
        return found
    company_id = found.value
    if company_id is None:
        name = (person.get('company_name') or '').strip() or domain
        made = await hubspot_writes.create_company(token, {'name': name, 'domain': domain})
        if is_err(made):
            return made
        company_id = made.value
    linked = await hubspot_writes.associate(token, contact_id, company_id)
    return linked if is_err(linked) else ok(company_id)


async def write_person(token: str, person: dict) -> Result[tuple[str, str | None, bool]]:
    """Make sure the person is in HubSpot with a company. ``(contact id, company id, created by us)``.
    ``person`` carries ``owner_id``: the user whose earlier writes are remembered."""
    contact = await _contact(token, person)
    if is_err(contact):
        return contact
    contact_id, created, has_company = contact.value
    # The team's own association stands. And a grant without the companies permission (HubSpot lets the user
    # tick the two separately) writes the contact alone.
    if has_company or not person.get('companies'):
        return ok((contact_id, None, created))
    company = await _company(token, contact_id, person)
    return company if is_err(company) else ok((contact_id, company.value, created))


def _due(now: datetime) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(_DUE_SQL, (HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE, now, BATCH)).fetchall()


def _connection(owner_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'",
            (owner_id,),
        ).fetchone()


def record(owner_id: str, email: str, written: tuple[str, str | None, bool], portal_id: str | None) -> None:
    """Only while the owner is still connected to the portal the write went to: a portal change that landed
    meanwhile has dropped that history and must not get it back."""
    contact_id, company_id, created = written
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO crm_contact_writes (owner_id, email, contact_id, company_id, created)'
            ' SELECT %s, %s, %s, %s, %s WHERE EXISTS (SELECT 1 FROM user_oauth_connections'
            " WHERE user_id = %s AND provider = 'hubspot' AND provider_account_id IS NOT DISTINCT FROM %s FOR SHARE)"
            ' ON CONFLICT DO NOTHING',
            (owner_id, email, contact_id, company_id, created, owner_id, portal_id),
        )


async def sweep(now: datetime, access_token: AccessToken) -> Counter:
    """Write everyone who is due. A user whose HubSpot fails (a refused grant, a rate limit, an outage) is left
    for the next sweep at the first failure, so one portal's trouble costs one call and delays nobody else."""
    counts: Counter = Counter()
    if is_disabled('vendor_disabled:hubspot'):
        return counts
    tokens: dict[str, str | None] = {}
    for person in _due(now):
        # As the owner, in the organization of the send: an owner who left it gets nothing more written from it.
        with tasks.as_member(str(person['organization_id']), str(person['owner_id'])):
            await _write(person, tokens, access_token, counts)
    return counts


async def _write(person: dict, tokens: dict[str, str | None], access_token: AccessToken, counts: Counter) -> None:
    owner = str(person['owner_id'])
    if owner not in tokens:
        connection = _connection(owner)
        if connection and connection['provider_account_id'] != person['portal_id']:
            tokens[owner] = None  # reconnected to another portal since the sweep read it: wait for the next sweep
            return
        tokens[owner] = await access_token(connection, HUBSPOT) if connection else None
        if tokens[owner] is None:
            counts['token_failed'] += 1
    token = tokens[owner]
    if token is None:
        return
    written = await write_person(token, person)
    if is_err(written):
        # ponytail: retried every sweep with no backoff; a portal that keeps refusing costs one call a sweep.
        logger.warning('crm.contacts.write_failed', user_id=owner, error_code=written.error.code)
        tokens[owner] = None
        counts['failed'] += 1
        return
    record(owner, person['email'], written.value, person['portal_id'])
    counts['created' if written.value[2] else 'existing'] += 1
