"""HubSpot contact write-back (#527) against real SQL; HubSpot is a small in-memory portal.

Needs TEST_DATABASE_URL.
"""

import json
from datetime import timedelta

import httpx
import pytest

from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.crm import contacts, write_back
from app.integrations.providers import HUBSPOT_CONTACTS_WRITE, HUBSPOT_READ_SCOPES, HUBSPOT_WRITE_SCOPES
from tests.conftest import call_as, member, scoped_conn
from tests.test_crm_gate_sql import Tokens
from tests.test_dispatcher_sql import NOW, _campaign
from tests.test_dispatcher_sql import db as db  # the dispatcher's throwaway-schema fixture
from tests.test_dispatcher_sql import pytestmark as pytestmark  # skipped without TEST_DATABASE_URL
from tests.test_tools_sql import _user

READ = ['oauth', *HUBSPOT_READ_SCOPES]
WRITE = [*READ, *HUBSPOT_WRITE_SCOPES]
OBJECTS = '/crm/objects/2026-03'


class Portal:
    """What the write-back needs of HubSpot: contacts by email, companies by domain, and their links."""

    def __init__(self):
        self.contacts: dict[str, dict] = {}  # id -> properties
        self.companies: dict[str, dict] = {}
        self.links: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], httpx.Response] = {}  # (method, path) -> the answer to give once
        self.unindexed: set[str] = set()  # companies the search does not find yet, as on the real portal
        self._next = 100

    def _id(self) -> str:
        self._next += 1
        return str(self._next)

    def add_contact(self, **properties) -> str:
        self.contacts[cid := self._id()] = properties
        return cid

    def add_company(self, **properties) -> str:
        self.companies[cid := self._id()] = properties
        return cid

    def by_email(self, email: str) -> dict:
        return next(p for p in self.contacts.values() if p['email'] == email)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if failure := self.fail.pop((method, path), None):
            return failure
        body = json.loads(request.content) if request.content else {}
        if path == f'{OBJECTS}/contacts/batch/read':
            wanted = {i['id'] for i in body['inputs']}
            found = [{'id': i, 'properties': p} for i, p in self.contacts.items() if p['email'] in wanted]
            return httpx.Response(200, json={'results': found})
        if path == f'{OBJECTS}/contacts' and method == 'POST':
            if any(p['email'] == body['properties']['email'] for p in self.contacts.values()):
                return httpx.Response(409, json={'category': 'CONFLICT'})
            return httpx.Response(201, json={'id': self.add_contact(**body['properties'])})
        if path.startswith(f'{OBJECTS}/contacts/') and method == 'PATCH':
            cid = path.rsplit('/', 1)[1]
            self.contacts[cid].update(body['properties'])
            return httpx.Response(200, json={'id': cid})
        if path == f'{OBJECTS}/companies/search':
            domain = body['filterGroups'][0]['filters'][0]['value']
            found = [
                {'id': i, 'properties': p}
                for i, p in self.companies.items()
                if p.get('domain') == domain and i not in self.unindexed
            ]
            return httpx.Response(200, json={'results': found[:1]})
        if path == f'{OBJECTS}/companies' and method == 'POST':
            self.unindexed.add(cid := self.add_company(**body['properties']))
            return httpx.Response(201, json={'id': cid})
        if '/associations/default/companies/' in path and path.rsplit('/', 1)[1] not in self.companies:
            return httpx.Response(404, json={'category': 'OBJECT_NOT_FOUND'})
        if '/associations/default/companies/' in path and method == 'PUT':
            parts = path.split('/')
            self.links.append((parts[5], parts[-1]))
            self.contacts[parts[5]]['associatedcompanyid'] = parts[-1]
            return httpx.Response(200, json={'status': 'COMPLETE', 'results': []})
        return httpx.Response(404)


@pytest.fixture
def portal(db, http, monkeypatch, vendor_db):
    for module in (contacts, write_back):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    monkeypatch.setattr(contacts, 'is_disabled', lambda key: False)
    http.handler = Portal()
    return http.handler


def _connect(db, uid, scopes=WRITE, *, on=True):
    db.execute(
        'INSERT INTO user_oauth_connections (user_id, organization_id, provider, refresh_token_encrypted,'
        " granted_scopes, status, provider_account_id) VALUES (%s, default_organization(%s), 'hubspot', 'ct', %s,"
        " 'connected', '4242')",
        (uid, uid, scopes),
    )
    if on:
        assert call_as(member(db, uid), write_back.set_enabled, uid, True) == 'ok'
        # The switch went on a day ago, so the sends of these tests come after it.
        db.execute('UPDATE user_oauth_connections SET crm_write_back_since = %s', (NOW - timedelta(days=1),))


def _sent(db, uid, email, *, at=NOW, status='sent', **target):
    """A campaign of ``uid`` with one person and one delivered send to them."""
    cid = _campaign(db, uid, emails=(email,))
    if target:
        sets = ', '.join(f'{k} = %s' for k in target)
        db.execute(f'UPDATE outreach_campaign_targets SET {sets} WHERE campaign_id = %s', (*target.values(), cid))
    db.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               subject, body, idempotency_key, delivered_at)
           SELECT t.campaign_id, t.owner_id, t.id, 1, 'email', %s, t.email, 's', 'b', t.id::text, %s
           FROM outreach_campaign_targets t WHERE t.campaign_id = %s""",
        (status, at if status == 'sent' else None, cid),
    )


async def _sweep(tokens=None):
    with system(SystemReason.HUBSPOT_WEBHOOK):  # as app/worker.py sync_hubspot_contacts
        return dict(await contacts.sweep(NOW, tokens or Tokens()))


def _writes(db):
    rows = db.execute('SELECT email, contact_id, company_id, created FROM crm_contact_writes ORDER BY email').fetchall()
    return [(r['email'], r['created'], r['company_id'] is not None) for r in rows]


async def test_a_person_written_to_becomes_a_contact_with_its_company(db, portal):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, ' Anna.Neri@Acme.it ', first_name='Anna', last_name='Neri', role='CEO', company_name='Acme Srl')

    assert await _sweep() == {'created': 1}
    assert portal.by_email('anna.neri@acme.it') == {
        'email': 'anna.neri@acme.it',
        'firstname': 'Anna',
        'lastname': 'Neri',
        'jobtitle': 'CEO',
        'company': 'Acme Srl',
        'associatedcompanyid': next(iter(portal.companies)),
    }
    assert list(portal.companies.values()) == [{'name': 'Acme Srl', 'domain': 'acme.it'}]
    assert _writes(db) == [('anna.neri@acme.it', True, True)]

    portal.calls.clear()
    assert await _sweep() == {}  # written once: a second sweep, or a second campaign, asks HubSpot nothing
    _sent(db, uid, 'anna.neri@acme.it')
    assert await _sweep() == {} and portal.calls == []


async def test_an_existing_contact_only_gains_what_it_has_empty(db, portal):
    uid = _user(db)
    _connect(db, uid)
    theirs = portal.add_company(name='Acme (ours)', domain='acme.it')
    kept = portal.add_contact(email='anna@acme.it', firstname='Annalisa', jobtitle='', associatedcompanyid=theirs)
    _sent(db, uid, 'anna@acme.it', first_name='Anna', last_name='Neri', role='CEO', company_name='Acme Srl')

    assert await _sweep() == {'existing': 1}
    assert portal.contacts[kept] == {
        'email': 'anna@acme.it',
        'firstname': 'Annalisa',  # the team's value stands
        'jobtitle': 'CEO',  # an empty one is filled
        'lastname': 'Neri',
        'company': 'Acme Srl',
        'associatedcompanyid': theirs,  # and so does their association: no company call at all
    }
    assert not any('companies' in path for _, path in portal.calls)
    assert _writes(db) == [('anna@acme.it', False, False)]


async def test_the_portals_own_company_is_matched_by_domain_and_a_personal_mailbox_has_none(db, portal):
    uid = _user(db)
    _connect(db, uid)
    theirs = portal.add_company(name='Acme (ours)', domain='acme.it')
    _sent(db, uid, 'anna@acme.it')
    _sent(db, uid, 'bruno@gmail.com')

    assert await _sweep() == {'created': 2}
    assert portal.companies == {theirs: {'name': 'Acme (ours)', 'domain': 'acme.it'}}  # none created
    assert portal.links == [(next(i for i, p in portal.contacts.items() if p['email'] == 'anna@acme.it'), theirs)]
    assert _writes(db) == [('anna@acme.it', True, True), ('bruno@gmail.com', True, False)]


async def test_colleagues_at_a_new_company_share_one_company_although_the_search_lags(db, portal):
    """Seen on a real portal: the search does not find a company for seconds after its create."""
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@newco.it', company_name='NewCo')
    _sent(db, uid, 'bruno@newco.it', company_name='NewCo')

    assert await _sweep() == {'created': 2}
    assert len(portal.companies) == 1  # Bruno is linked to the company made for Anna, not to a second one
    (company,) = portal.companies
    assert sorted(c for _, c in portal.links) == [company, company]

    _sent(db, uid, 'carla@newco.it', company_name='NewCo')  # a later sweep, the search still behind
    assert await _sweep() == {'created': 1}
    assert len(portal.companies) == 1

    # The remembered company was deleted in HubSpot: the portal is asked again and a new one is made.
    del portal.companies[company]
    _sent(db, uid, 'dino@newco.it', company_name='NewCo')
    assert await _sweep() == {'created': 1}
    assert len(portal.companies) == 1 and company not in portal.companies


async def test_a_grant_without_the_companies_permission_writes_the_contact_alone(db, portal):
    """On HubSpot's consent screen a user can tick the contacts permission and not the companies one."""
    uid = _user(db)
    _connect(db, uid, [*READ, HUBSPOT_CONTACTS_WRITE])
    _sent(db, uid, 'anna@acme.it', company_name='Acme Srl')

    assert await _sweep() == {'created': 1}
    assert portal.by_email('anna@acme.it')['company'] == 'Acme Srl'  # the name stays on the contact
    assert portal.companies == {} and portal.links == []
    assert not any('companies' in path for _, path in portal.calls)  # not even a search
    assert _writes(db) == [('anna@acme.it', True, False)]


async def test_nobody_is_written_without_the_switch_the_scopes_a_delivered_send_or_a_clear_gate(db, portal):
    off, read_only, early, unsent, held = (_user(db) for _ in range(5))
    _connect(db, off, on=False)
    _connect(db, read_only, READ, on=False)
    db.execute('UPDATE user_oauth_connections SET crm_write_back = true WHERE user_id = %s', (read_only,))
    for uid in (early, unsent, held):
        _connect(db, uid)
    _sent(db, off, 'a@acme.it')
    _sent(db, read_only, 'b@acme.it')
    _sent(db, early, 'c@acme.it', at=NOW - timedelta(days=2))  # delivered before the switch went on
    _sent(db, unsent, 'd@acme.it', status='awaiting_approval')
    _sent(db, held, 'e@acme.it')
    db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason) VALUES (%s, 'e@acme.it', 'HUBSPOT_OWNED')", (held,)
    )

    assert await _sweep() == {} and portal.calls == []
    assert _writes(db) == []


async def test_a_portal_in_trouble_is_left_for_the_next_sweep_and_delays_nobody_else(db, portal, monkeypatch):
    ann, bob = _user(db), _user(db)
    for uid in (ann, bob):
        _connect(db, uid)
    _sent(db, ann, 'x@acme.it')
    _sent(db, ann, 'y@acme.it')
    _sent(db, bob, 'z@other.it')
    portal.fail[('POST', f'{OBJECTS}/contacts')] = httpx.Response(429, headers={'Retry-After': '30'})

    counts = await _sweep()
    # One of the two users met the 429 on their first person and was left alone; the other went through.
    assert counts['failed'] == 1 and counts['created'] >= 1
    assert len(_writes(db)) == counts['created']
    assert await _sweep() == {'created': 3 - counts['created']}  # the rest, with HubSpot answering again
    assert [w[0] for w in _writes(db)] == ['x@acme.it', 'y@acme.it', 'z@other.it']

    monkeypatch.setattr(contacts, 'is_disabled', lambda key: key == 'vendor_disabled:hubspot')
    _sent(db, ann, 'late@acme.it')
    portal.calls.clear()
    assert await _sweep() == {} and portal.calls == []  # the kill switch stops the writes too


async def test_a_grant_that_cannot_be_refreshed_writes_nothing(db, portal):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')
    assert await _sweep(Tokens(None)) == {'token_failed': 1}
    assert portal.calls == [] and _writes(db) == []


async def test_a_batch_read_before_a_portal_change_is_not_written_to_the_new_portal(db, portal, monkeypatch):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')
    real_due = contacts._due

    def due_then_switch(now):
        rows = real_due(now)
        db.execute("UPDATE user_oauth_connections SET provider_account_id = '9999'")  # reconnected to portal B
        return rows

    monkeypatch.setattr(contacts, '_due', due_then_switch)
    assert await _sweep() == {}
    assert portal.calls == [] and _writes(db) == []


async def test_a_portal_change_during_a_write_records_nothing_for_the_old_portal(db, portal, monkeypatch):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')
    real_write = contacts.write_person

    async def write_then_switch(token, person):
        done = await real_write(token, person)
        db.execute("UPDATE user_oauth_connections SET provider_account_id = '9999'")  # reconnected to portal B
        return done

    monkeypatch.setattr(contacts, 'write_person', write_then_switch)
    assert await _sweep() == {'created': 1}
    assert _writes(db) == []


async def test_a_contact_created_by_someone_else_meanwhile_is_taken_as_existing(db, portal):
    uid, colleague = _user(db), _user(db)
    for u in (uid, colleague):
        _connect(db, u)
    _sent(db, uid, 'anna@acme.it')
    _sent(db, colleague, 'anna@acme.it')  # the same person, the same portal, two connected colleagues

    counts = await _sweep()
    assert counts == {'created': 1, 'existing': 1}
    assert len(portal.contacts) == 1 and len(portal.companies) == 1  # one contact, one company

    # A create that loses the race answers 409: the contact is read again, never duplicated.
    other = _user(db)
    _connect(db, other)
    _sent(db, other, 'carla@new.it')
    real_read = f'{OBJECTS}/contacts/batch/read'
    portal.fail[('POST', real_read)] = httpx.Response(200, json={'results': []})  # not there yet...
    portal.add_contact(email='carla@new.it')  # ...and there by the time we create
    assert await _sweep() == {'existing': 1}
    assert sum(p['email'] == 'carla@new.it' for p in portal.contacts.values()) == 1


def test_company_domain_reads_the_employer_from_the_address():
    assert contacts.company_domain('anna@acme.it') == 'acme.it'
    assert contacts.company_domain('Anna@Sub.Acme.IT') == 'sub.acme.it'
    for personal in ('a@gmail.com', 'a@libero.it', 'a@outlook.it'):
        assert contacts.company_domain(personal) is None
    assert contacts.company_domain('not-an-address') is None
