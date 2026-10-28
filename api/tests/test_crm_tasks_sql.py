"""The HubSpot task step (#123) against real SQL; HubSpot is the in-memory portal of the write-back tests.

Needs TEST_DATABASE_URL.
"""

from datetime import timedelta

import httpx
import pytest

from app.crm import activities, contacts, write_back
from app.outreach import crm_tasks, dispatcher
from tests import test_dispatcher_sql as dispatcher_tests
from tests.conftest import each_org, each_org_async, scoped_conn
from tests.test_crm_activities_sql import Timeline
from tests.test_crm_contacts_sql import OBJECTS, READ, _connect
from tests.test_crm_gate_sql import Tokens, _gate_read
from tests.test_crm_gate_sql import gated as gated
from tests.test_dispatcher_sql import NOW, _campaign, _mailbox
from tests.test_dispatcher_sql import db as db  # the dispatcher's throwaway-schema fixture
from tests.test_dispatcher_sql import provider as provider  # fake mailbox + token refresh
from tests.test_dispatcher_sql import pytestmark as pytestmark  # skipped without TEST_DATABASE_URL
from tests.test_dispatcher_sql import switches as switches
from tests.test_tools_sql import _user

ME, COLLEAGUE = '77', '99'
TASK_THEN_EMAIL = [
    {
        'step': 1,
        'channel': 'hubspot_task',
        'subject': 'Idea per {{companyName}}',
        'body': 'Ciao {{firstName}},\nti va?',
        'send_after_days': 0,
    },
    {'step': 2, 'channel': 'email', 'subject': 'Ciao {{firstName}}', 'body': 'Ti scrivo io', 'send_after_days': 3},
]


@pytest.fixture
def portal(db, http, monkeypatch, vendor_db):
    for module in (activities, contacts, write_back, crm_tasks):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    monkeypatch.setattr(crm_tasks, 'is_disabled', lambda key: False)
    http.handler = Timeline()
    return http.handler


def _owner(db, scopes=None):
    """A user whose HubSpot grant can write (write-back on: the task step honours the owner's switch)."""
    uid = _user(db)
    _connect(db, uid, on=not scopes, **({'scopes': scopes} if scopes else {}))
    db.execute('UPDATE user_oauth_connections SET provider_owner_id = %s WHERE user_id = %s', (ME, uid))
    return uid


async def _deliver(now=NOW) -> dict:
    return dict(await each_org_async(dispatcher_tests._DB[0], crm_tasks.deliver_due, now, Tokens()))


async def _beat(now=NOW):
    await _gate_read(now)
    planned = each_org(dispatcher_tests._DB[0], dispatcher.plan, now)
    return dict(planned), await _deliver(now)


def _sends(db):
    rows = db.execute(
        'SELECT recipient, step, channel, status, error_code FROM outreach_campaign_sends ORDER BY step, recipient'
    ).fetchall()
    return [tuple(r.values()) for r in rows]


async def test_a_task_step_makes_a_task_for_the_owner_and_the_sequence_goes_on(db, portal, gated, provider, switches):
    """Done when (#123): a campaign mixes task steps and send steps, and the task carries the draft."""
    uid = _owner(db)
    _mailbox(db, uid)
    theirs = portal.add_contact(email='bea@acme.it', hubspot_owner_id=COLLEAGUE)
    cid = _campaign(db, uid, TASK_THEN_EMAIL, approve=True, emails=('anna@acme.it', 'bea@acme.it'))
    db.execute("UPDATE outreach_campaigns SET name = 'Autunno' WHERE id = %s", (cid,))
    db.execute(  # nothing identifies cleo in HubSpot
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, first_name, linkedin_url, status)'
        " VALUES (%s, %s, 'Cleo', 'https://www.linkedin.com/in/cleo', 'active')",
        (cid, uid),
    )

    # Not held for approval although the campaign asks for it: the colleague who gets the task is the reviewer.
    assert await _beat() == ({'task_planned': 2, 'skipped_no_email': 1}, {'task_created': 2})
    mine, hers = sorted(portal.logged, key=lambda a: a['hs_task_subject'])
    anna = next(i for i, p in portal.contacts.items() if p['email'] == 'anna@acme.it')  # created for the task
    assert mine == {
        'id': mine['id'],
        'kind': 'tasks',
        'contact': anna,
        'company': next(iter(portal.companies)),
        'hs_timestamp': str(int(NOW.timestamp() * 1000)),
        'hs_task_subject': 'Anna: Idea per Client',
        'hs_task_body': '<strong>Idea per Client</strong><br><br>Ciao Anna,<br>ti va?<br><br>— Zenbree · Autunno · step 1',
        'hs_task_status': 'NOT_STARTED',
        'hs_task_type': 'EMAIL',
        'hubspot_owner_id': ME,
    }
    # bea belongs to a colleague: the gate holds her from every send, and the task is how she reaches them.
    assert (hers['contact'], hers['hubspot_owner_id']) == (theirs, COLLEAGUE)
    assert 'notes_last_contacted' not in portal.contacts[anna]
    assert _sends(db)[:2] == [
        ('anna@acme.it', 1, 'hubspot_task', 'sent', None),
        ('bea@acme.it', 1, 'hubspot_task', 'sent', None),
    ]

    portal.calls.clear()
    assert (await _beat(NOW + timedelta(days=1)))[1] == {}  # one task each, and nothing asked again
    assert not [c for c in portal.calls if c[1].endswith('/tasks')]

    # Three days on the email step is due: anna's waits for approval, bea's is still held as a colleague's.
    planned, _ = await _beat(NOW + timedelta(days=3, hours=1))
    assert planned == {'awaiting_approval': 1}
    assert _sends(db)[-1] == ('anna@acme.it', 2, 'email', 'awaiting_approval', None)


async def test_a_company_owner_gets_the_task_when_the_contact_has_none(db, portal, gated):
    uid = _owner(db)
    company = portal.add_company(domain='acme.it', hubspot_owner_id=COLLEAGUE)
    portal.add_contact(email='anna@acme.it', associatedcompanyid=company)
    _campaign(db, uid, TASK_THEN_EMAIL[:1], emails=('anna@acme.it',))

    assert (await _beat())[1] == {'task_created': 1}
    assert portal.logged[0]['hubspot_owner_id'] == COLLEAGUE and portal.logged[0]['company'] is None


async def test_other_blocks_still_hold_a_task_and_a_customer_gets_none(db, portal, gated):
    uid = _owner(db)
    company = portal.add_company(domain='acme.it', lifecyclestage='customer')
    portal.add_contact(email='anna@acme.it', associatedcompanyid=company)
    portal.add_contact(email='bea@other.it', notes_last_contacted=(NOW - timedelta(days=2)).isoformat())
    _campaign(db, uid, TASK_THEN_EMAIL[:1], emails=('anna@acme.it', 'bea@other.it'))

    assert await _beat() == ({}, {}) and portal.logged == []


async def test_an_unknown_outcome_is_not_repeated_and_a_refusal_is_retried(db, portal, gated):
    uid = _owner(db)
    _campaign(db, uid, TASK_THEN_EMAIL[:1], emails=('anna@acme.it', 'bea@other.it'))

    # HubSpot cannot be read at delivery: nothing is taken, the claims wait.
    await _gate_read(NOW)
    each_org(dispatcher_tests._DB[0], dispatcher.plan, NOW)
    portal.fail[('POST', f'{OBJECTS}/contacts/batch/read')] = httpx.Response(429, headers={'Retry-After': '5'})
    assert await _deliver() == {} and portal.logged == []
    assert {s[3] for s in _sends(db)} == {'approved'}

    portal.fail[('POST', f'{OBJECTS}/tasks')] = httpx.Response(502)
    assert (await _beat())[1] == {'task_unknown': 1, 'task_created': 1}
    assert [s[3:] for s in _sends(db)] == [('sending', 'UNKNOWN_OUTCOME'), ('sent', None)]
    assert (await _beat(NOW + timedelta(hours=1)))[1] == {}  # never a second task by itself

    db.execute("UPDATE outreach_campaign_sends SET status = 'approved', attempts = 0 WHERE status = 'sending'")
    for _ in range(3):
        portal.fail[('POST', f'{OBJECTS}/tasks')] = httpx.Response(400)
        assert (await _beat(NOW + timedelta(hours=2)))[1] == {'task_failed': 1}
    assert (await _beat(NOW + timedelta(hours=3)))[1] == {}  # given up after three refusals
    assert _sends(db)[0][3:] == ('failed', 'HUBSPOT.HTTP_ERROR')


async def test_claims_wait_while_the_grant_cannot_write(db, portal, gated):
    uid = _owner(db, scopes=READ)
    _campaign(db, uid, TASK_THEN_EMAIL[:1], emails=('anna@acme.it',))

    assert await _beat() == ({'task_planned': 1}, {}) and portal.logged == []
    assert not crm_tasks.can_run(db, uid)
    db.execute(
        "UPDATE user_oauth_connections SET granted_scopes = granted_scopes || '{crm.objects.contacts.write}',"
        ' crm_write_back = true'
    )
    assert crm_tasks.can_run(db, uid)
    assert (await _beat())[1] == {'task_created': 1}
    assert portal.companies == {}  # without the companies permission the contact is written alone


async def test_claims_wait_while_the_owner_has_write_back_off(db, portal, gated):
    uid = _owner(db)
    _campaign(db, uid, TASK_THEN_EMAIL[:1], emails=('anna@acme.it',))

    db.execute('UPDATE user_oauth_connections SET crm_write_back = false')
    assert not crm_tasks.can_run(db, uid)
    assert await _beat() == ({'task_planned': 1}, {}) and portal.logged == []
    db.execute('UPDATE user_oauth_connections SET crm_write_back = true')
    assert crm_tasks.can_run(db, uid)
    assert (await _beat())[1] == {'task_created': 1}
