"""HubSpot read gate + dispatcher against real SQL (#120); HubSpot and the token refresh faked.

Needs TEST_DATABASE_URL; loads db/*.sql alphabetically into a throwaway schema.
"""

import json
from collections import Counter
from datetime import timedelta

import fakeredis
import httpx
import pytest

from app.core.scope import acting_as, acting_for, system
from app.core.system_reasons import SystemReason
from app.crm import activities, gate, gate_recheck
from app.outreach import dispatcher
from app.scheduling import runner
from tests import test_dispatcher_sql as dispatcher_tests
from tests.conftest import campaign_orgs, each_org, each_org_async, project, scoped_conn
from tests.test_dispatcher_sql import NOW, TWO_STEPS, _campaign, _mailbox, _sends
from tests.test_dispatcher_sql import db as db  # the dispatcher's throwaway-schema fixture
from tests.test_dispatcher_sql import provider as provider  # fake mailbox + token refresh
from tests.test_dispatcher_sql import pytestmark as pytestmark  # skipped without TEST_DATABASE_URL
from tests.test_dispatcher_sql import switches as switches
from tests.test_dispatcher_sql import unipile as unipile  # fake LinkedIn
from tests.test_holds_sql import person, second_project
from tests.test_tools_sql import _user


@pytest.fixture
def gated(db, monkeypatch):
    """The gate reads the same throwaway schema; no kill switch on, an empty Redis, no retry queued."""
    for module in (
        gate,
        gate_recheck,
        activities,
    ):  # the gate asks activities for the touches Zenbree logged itself (#528)
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    QUEUED.clear()
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(gate, '_redis', lambda: fake)
    off: set[str] = set()
    for module in (gate, gate_recheck):
        monkeypatch.setattr(module, 'is_disabled', lambda key: key in off)
    return off


ACCESS = 'at-1'
QUEUED: list[tuple[str, int]] = []  # retries the gate asked the queue for: (owner id, countdown)


class Tokens:
    """Stands in for dispatcher._access_token: ``None`` = the grant could not be refreshed."""

    def __init__(self, token: str | None = ACCESS):
        self.token = token
        self.calls = 0

    async def __call__(self, connection, provider):
        assert provider.name == 'hubspot'
        self.calls += 1
        return self.token


ME = '77'  # the connected user's own HubSpot owner id
PORTAL = '4242'


def _hubspot(db, uid, status='connected', portal=PORTAL):
    db.execute(
        'INSERT INTO user_oauth_connections'
        ' (user_id, organization_id, provider, refresh_token_encrypted, status, provider_owner_id,'
        " provider_account_id) VALUES (%s, default_organization(%s), 'hubspot', 'ct', %s, %s, %s)",
        (uid, uid, status, ME, portal),
    )


def _contacts(
    last_contacted: dict[str, str | None],
    *,
    people: dict | None = None,
    companies: dict | None = None,
    deals: dict | None = None,
):
    """HubSpot answers batch reads: ``last_contacted`` (email → notes_last_contacted) and ``people``
    (email → more contact properties) are the contacts that exist; ``companies`` by id; ``deals``
    maps a deal id to the ids of its companies."""
    known = {e: {'notes_last_contacted': v} for e, v in last_contacted.items()}
    for email, props in (people or {}).items():
        known[email] = {**known.get(email, {}), **props}

    def handler(request: httpx.Request) -> httpx.Response:
        asked = [i['id'] for i in json.loads(request.content)['inputs']]
        if '/associations/deals/companies/' in request.url.path:
            found = [
                {'from': {'id': i}, 'to': [{'toObjectId': int(c)} for c in (deals or {})[i]]}
                for i in asked
                if i in (deals or {})
            ]
        elif '/companies/' in request.url.path:
            found = [{'id': i, 'properties': (companies or {})[i]} for i in asked if i in (companies or {})]
        else:
            found = [{'id': f'c-{e}', 'properties': {'email': e, **known[e]}} for e in asked if e in known]
        return httpx.Response(207, json={'results': found})

    return handler


def _claimed(db) -> set[str]:
    return {s['recipient'] for s in _sends(db)}


def _org(uid) -> str:
    return str(dispatcher_tests._DB[0].execute('SELECT default_organization(%s) AS id', (uid,)).fetchone()['id'])


def _queue(owner, delay) -> None:
    QUEUED.append((owner, delay))


async def _gate_read(now, tokens=None) -> Counter:
    """The gate read of one beat, as app/outreach/campaign_beat.py runs it in each organization: as each owner, whose
    grant it reads."""
    counts: Counter = Counter()
    for org in campaign_orgs(dispatcher_tests._DB[0]):
        with acting_for(org, 'campaign beat'):
            candidates = dispatcher.gate_candidates(now)
        for owner, emails in candidates.items():
            with acting_as(org, owner):
                counts += await gate.check({owner: emails}, now, tokens or Tokens(), _queue)
    return counts


async def _beat(now, tokens=None):
    """One beat up to the claims: the gate read, then the plan."""
    counts = await _gate_read(now, tokens)
    each_org(dispatcher_tests._DB[0], dispatcher.plan, now)
    return counts


async def _retry(uid, emails, now, tokens):
    """The retry task (app/worker.py ``retry_hubspot_read``) runs as the owner in their organization."""
    with acting_as(_org(uid), uid):
        return await gate.retry(uid, emails, now, tokens)


async def _recheck(events, now, tokens):
    """The webhook task (app/worker.py ``apply_hubspot_events``) crosses organizations: a portal names no organization."""
    with system(SystemReason.HUBSPOT_WEBHOOK):
        return await gate_recheck.recheck(events, now, tokens)


async def test_cooldown_holds_a_recently_contacted_person_until_it_lapses(db, gated, http):
    """Done when (#120, cooldown): a person contacted in HubSpot within 30 days gets no claim."""
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid, emails=('anna@client.test', 'bea@client.test', 'cleo@client.test'))
    http.handler = _contacts(
        {
            'anna@client.test': (NOW - timedelta(days=5)).isoformat(),  # called last week
            'bea@client.test': (NOW - timedelta(days=40)).isoformat(),
        }
    )  # cleo is not in HubSpot

    assert await _beat(NOW) == {'checked': 3, 'held': 1}
    anna = db.execute("SELECT * FROM crm_gate_checks WHERE email = 'anna@client.test'").fetchone()
    assert anna['reason'] == 'HUBSPOT_COOLDOWN' and anna['hold_until'] == NOW + timedelta(days=25)
    assert _claimed(db) == {'bea@client.test', 'cleo@client.test'}

    # No polling: a held person is not read again, and nobody else is owed a step.
    calls = len(http.calls)
    assert await _beat(NOW + timedelta(days=1)) == {}
    assert len(http.calls) == calls and 'anna@client.test' not in _claimed(db)

    # 26 days on the hold has lapsed: anna is read again before her step, clear, and claimed.
    assert await _beat(NOW + timedelta(days=26)) == {'checked': 1}
    assert 'anna@client.test' in _claimed(db)


async def test_each_step_is_read_once_before_it_is_prepared(db, gated, http):
    """A point read tied to the step: a contact logged between two steps holds the follow-up."""
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid, steps=TWO_STEPS)
    http.handler = _contacts({})
    await _beat(NOW)
    (first,) = _sends(db)
    db.execute(
        "UPDATE outreach_campaign_sends SET status = 'sent', delivered_at = %s WHERE id = %s", (NOW, first['id'])
    )
    db.execute('UPDATE outreach_campaign_targets SET next_step = 2')

    # The follow-up is due three days later; meanwhile sales called anna.
    due = NOW + timedelta(days=3, minutes=1)
    http.handler = _contacts({'anna@client.test': (NOW + timedelta(days=2)).isoformat()})
    assert await _beat(NOW + timedelta(days=1)) == {}  # not due yet: nothing is read
    assert await _beat(due) == {'checked': 1, 'held': 1}
    assert len(_sends(db)) == 1


async def test_a_failed_read_holds_everyone_and_is_retried_from_the_queue(db, gated, http):
    """the retry is a Celery task with a countdown, never the dispatcher beat."""
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    http.handler = lambda r: httpx.Response(503)
    assert await _beat(NOW) == {'errors': 1}
    assert _sends(db) == []  # fail-closed: HubSpot unreachable, nobody is sent to
    assert [(uid, 60)] == QUEUED

    # Until the queued retry ran, the beat asks HubSpot nothing, queues nothing, claims nothing.
    calls = len(http.calls)
    assert await _beat(NOW + timedelta(minutes=1)) == {'waiting': 1}
    assert len(http.calls) == calls and _sends(db) == [] and len(QUEUED) == 1

    # The retry task: still down → it raises, to be re-queued later with a longer countdown.
    with acting_for(_org(uid), 'campaign beat'):
        emails = dispatcher.gate_candidates(NOW)[uid]
    with pytest.raises(gate.ReadFailed):
        await _retry(uid, emails, NOW + timedelta(minutes=1), Tokens())
    assert await _beat(NOW + timedelta(minutes=2)) == {'waiting': 1}

    # HubSpot is back: the retry reads, and the beat takes over again.
    http.handler = _contacts({})
    assert await _retry(uid, emails, NOW + timedelta(minutes=3), Tokens()) == {'checked': 1}
    assert await _beat(NOW + timedelta(minutes=4)) == {'checked': 1}
    assert _claimed(db) == {'anna@client.test'}


async def test_a_rate_limit_waits_at_least_retry_after(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    http.handler = lambda r: httpx.Response(429, headers={'Retry-After': '300'})
    assert await _beat(NOW) == {'errors': 1}
    assert [(uid, 300)] == QUEUED
    with pytest.raises(gate.ReadFailed) as failed:
        await _retry(uid, ['anna@client.test'], NOW, Tokens())
    assert failed.value.retry_after == 300 and not failed.value.permanent


async def test_a_refused_grant_is_never_retried_until_the_user_reconnects(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    http.handler = lambda r: httpx.Response(401)
    assert await _beat(NOW) == {'errors': 1}
    assert QUEUED == []  # no automatic retry
    connection = db.execute("SELECT status, status_detail FROM user_oauth_connections WHERE provider = 'hubspot'")
    assert connection.fetchone() == {'status': 'error', 'status_detail': 'HUBSPOT.AUTH'}

    # The connection in error is not read, and its people are still not sent to.
    calls = len(http.calls)
    assert await _beat(NOW + timedelta(days=1)) == {}
    assert len(http.calls) == calls and _sends(db) == []

    # Reconnecting is the event that re-evaluates them.
    db.execute("UPDATE user_oauth_connections SET status = 'connected' WHERE provider = 'hubspot'")
    http.handler = _contacts({})
    assert await _beat(NOW + timedelta(days=1)) == {'checked': 1}
    assert _claimed(db) == {'anna@client.test'}


async def test_retry_ends_quietly_when_there_is_nothing_left_to_read(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    gate.wait_for_retry(uid, 60)
    assert await _retry(uid, [], NOW, Tokens()) == {}  # the campaign was paused meanwhile
    assert not gate._waiting(uid) and http.calls == []
    # A refused grant during the retry: connection in error, no further retry.
    gate.wait_for_retry(uid, 60)
    http.handler = lambda r: httpx.Response(403)
    assert await _retry(uid, ['anna@client.test'], NOW, Tokens()) == {}
    assert not gate._waiting(uid)
    assert db.execute("SELECT status FROM user_oauth_connections WHERE provider = 'hubspot'").fetchone() == {
        'status': 'error'
    }


async def test_a_clear_verdict_from_an_earlier_beat_is_not_enough(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, checked_at) VALUES (%s, 'anna@client.test', %s)",
        (uid, NOW - timedelta(minutes=1)),
    )
    each_org(db, dispatcher.plan, NOW)  # the plan alone, without this beat's read
    assert _sends(db) == []


async def test_revoked_grant_and_kill_switch_hold_without_calls(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    tokens = Tokens(token=None)  # refresh refused: _access_token marks the connection error
    assert await _beat(NOW, tokens) == {'token_failed': 1}
    db.execute("UPDATE user_oauth_connections SET status = 'error' WHERE provider = 'hubspot'")
    assert await _beat(NOW, tokens) == {}  # a connection in error is not read
    db.execute("UPDATE user_oauth_connections SET status = 'connected' WHERE provider = 'hubspot'")
    gated.add('vendor_disabled:hubspot')
    assert await _beat(NOW, tokens) == {}
    assert tokens.calls == 1 and http.calls == []
    assert _sends(db) == []  # none of them could read the CRM: nobody is sent to


async def test_owners_without_hubspot_and_people_without_email_are_not_gated(db, gated, http):
    plain, connected = _user(db), _user(db)
    _campaign(db, plain, emails=('anna@client.test',))
    _hubspot(db, connected)
    li = _campaign(db, connected, emails=())
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, linkedin_url, first_name, status)'
        " VALUES (%s, %s, 'https://www.linkedin.com/in/bo/', 'Bo', 'active')",
        (li, connected),
    )
    _hubspot(db, _user(db), status='disconnected')  # a disconnected portal gates nobody

    assert await _beat(NOW) == {}  # nobody with an email to check
    assert http.calls == []
    # bo has no email: not held by the gate, so the email step is skipped (NO_EMAIL) as without HubSpot.
    bo = next(s for s in _sends(db) if str(s['campaign_id']) == li)
    assert bo['status'] == 'skipped' and bo['error_code'] == 'NO_EMAIL'
    assert 'anna@client.test' in _claimed(db)


async def test_customer_open_deal_owner_and_opt_out_get_no_claim(db, gated, http):
    """Done when (#120): a contact whose company has an open deal, or who unsubscribed, never gets a send."""
    uid = _user(db)
    _hubspot(db, uid)
    emails = ('deal@client.test', 'out@client.test', 'cust@client.test', 'theirs@client.test', 'mine@client.test')
    _campaign(db, uid, emails=emails)
    http.handler = _contacts(
        {},
        people={
            'deal@client.test': {'associatedcompanyid': '1'},
            'out@client.test': {'hs_email_optout': 'true'},
            'cust@client.test': {'associatedcompanyid': '2'},
            'theirs@client.test': {'associatedcompanyid': '3'},
            'mine@client.test': {'associatedcompanyid': '4', 'hubspot_owner_id': ME},
        },
        companies={
            '1': {'lifecyclestage': 'opportunity', 'hs_num_open_deals': '1'},
            '2': {'lifecyclestage': 'customer', 'hs_num_open_deals': '0'},
            '3': {'lifecyclestage': 'lead', 'hubspot_owner_id': '12'},  # a colleague's account
            '4': {'lifecyclestage': 'lead', 'hubspot_owner_id': ME},
        },
    )

    assert await _beat(NOW) == {'checked': 5, 'held': 4}
    assert _claimed(db) == {'mine@client.test'}
    checks = {r['email']: r for r in db.execute('SELECT * FROM crm_gate_checks').fetchall()}
    assert {e: c['reason'] for e, c in checks.items()} == {
        'deal@client.test': 'HUBSPOT_OPEN_DEAL',
        'out@client.test': 'HUBSPOT_OPTED_OUT',
        'cust@client.test': 'HUBSPOT_CUSTOMER',
        'theirs@client.test': 'HUBSPOT_OWNED',
        'mine@client.test': None,
    }
    assert all(c['hold_until'] is None for c in checks.values())
    # The record ids a webhook will name are kept with the verdict.
    assert (checks['deal@client.test']['contact_id'], checks['deal@client.test']['company_id']) == (
        'c-deal@client.test',
        '1',
    )

    # No polling: a month on, the blocked people are still not read, and still not sent to.
    calls = len(http.calls)
    assert await _beat(NOW + timedelta(days=30)) == {}
    assert len(http.calls) == calls and _claimed(db) == {'mine@client.test'}


async def test_a_failed_company_read_holds_the_whole_batch(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)

    def handler(request: httpx.Request) -> httpx.Response:
        if '/companies/' in request.url.path:
            return httpx.Response(500)
        props = {'email': 'anna@client.test', 'associatedcompanyid': '1'}
        return httpx.Response(200, json={'results': [{'id': '5', 'properties': props}]})

    http.handler = handler
    assert await _beat(NOW) == {'errors': 1}
    assert _sends(db) == [] and db.execute('SELECT 1 FROM crm_gate_checks').fetchone() is None


def _event(kind, record, portal=PORTAL):
    return {'portal': portal, 'kind': kind, 'id': record}


async def test_a_closed_deal_webhook_lifts_the_block(db, gated, http):
    """No polling: the open-deal block lifts when HubSpot says the deal changed, and only then."""
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    people = {'anna@client.test': {'associatedcompanyid': '1'}}
    http.handler = _contacts({}, people=people, companies={'1': {'hs_num_open_deals': '1'}})
    assert await _beat(NOW) == {'checked': 1, 'held': 1}
    assert _sends(db) == []

    # The deal is closed lost: HubSpot sends deal.propertyChange(dealstage) for deal 55.
    http.handler = _contacts({}, people=people, companies={'1': {'hs_num_open_deals': '0'}}, deals={'55': ['1']})
    later = NOW + timedelta(days=2)
    assert await _recheck([_event('deal', '55')], later, Tokens()) == {'rechecked': 1}
    assert db.execute('SELECT reason FROM crm_gate_checks').fetchone()['reason'] is None
    assert await _beat(later) == {'checked': 1}
    assert _claimed(db) == {'anna@client.test'}


async def test_contact_and_company_webhooks_land_new_blocks(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid, approve=True, emails=('anna@client.test', 'bea@client.test'))
    people = {
        'anna@client.test': {'associatedcompanyid': '1'},
        'bea@client.test': {'associatedcompanyid': '2'},
    }
    http.handler = _contacts({}, people=people, companies={'1': {}, '2': {}})
    await _beat(NOW)  # both clear, both claims wait for approval

    # Sales logs a call with anna; bea's company becomes a customer.
    http.handler = _contacts(
        {'anna@client.test': NOW.isoformat()}, people=people, companies={'1': {}, '2': {'lifecyclestage': 'customer'}}
    )
    events = [_event('contact', 'c-anna@client.test'), _event('company', '2'), _event('company', '999')]
    assert await _recheck(events, NOW + timedelta(hours=1), Tokens()) == {'rechecked': 2}
    reasons = {r['email']: r['reason'] for r in db.execute('SELECT email, reason FROM crm_gate_checks').fetchall()}
    assert reasons == {'anna@client.test': 'HUBSPOT_COOLDOWN', 'bea@client.test': 'HUBSPOT_CUSTOMER'}


async def test_webhook_reaches_every_user_of_the_portal_and_no_other(db, gated, http):
    first, second, other = _user(db), _user(db), _user(db)
    for uid, portal in ((first, PORTAL), (second, PORTAL), (other, '9999')):
        _hubspot(db, uid, portal=portal)
        _campaign(db, uid)
    http.handler = _contacts({}, people={'anna@client.test': {'hs_email_optout': 'false'}})
    await _beat(NOW)
    http.handler = _contacts({}, people={'anna@client.test': {'hs_email_optout': 'true'}})
    assert await _recheck([_event('contact', 'c-anna@client.test')], NOW, Tokens()) == {'rechecked': 2}
    blocked = {str(r['owner_id']) for r in db.execute('SELECT owner_id FROM crm_gate_checks WHERE reason IS NOT NULL')}
    assert blocked == {first, second}


async def test_a_webhook_whose_token_is_not_available_is_retried_unless_the_grant_is_gone(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    http.handler = _contacts({}, people={'anna@client.test': {'associatedcompanyid': '1'}}, companies={'1': {}})
    await _beat(NOW)
    # A token endpoint 5xx gives None and leaves the connection connected: the delivery retries.
    with pytest.raises(gate.ReadFailed):
        await _recheck([_event('company', '1')], NOW, Tokens(None))
    with pytest.raises(gate.ReadFailed):
        await _recheck([_event('deal', '55')], NOW, Tokens(None))

    # A revoked grant: like dispatcher._access_token, mark the connection error, then give None. No retry.
    class Revoked(Tokens):
        async def __call__(self, connection, provider):
            db.execute("UPDATE user_oauth_connections SET status = 'error' WHERE id = %s", (connection['id'],))
            return None

    assert await _recheck([_event('company', '1')], NOW, Revoked()) == {'token_failed': 1}


async def test_a_webhook_that_cannot_read_hubspot_is_retried_not_lost(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid)
    http.handler = _contacts({}, people={'anna@client.test': {'associatedcompanyid': '1'}}, companies={'1': {}})
    await _beat(NOW)
    events = [_event('company', '1')]
    http.handler = lambda r: httpx.Response(503)
    with pytest.raises(gate.ReadFailed):
        await _recheck(events, NOW, Tokens())
    with pytest.raises(gate.ReadFailed):
        await _recheck([_event('deal', '55')], NOW, Tokens())  # the association read fails too
    http.handler = _contacts({}, people={'anna@client.test': {'associatedcompanyid': '1'}}, companies={'1': {}})
    gated.add('vendor_disabled:hubspot')  # HubSpot answers, yet the kill switch alone makes the delivery retry
    with pytest.raises(gate.ReadFailed):
        await _recheck(events, NOW, Tokens())
    gated.clear()

    # A refused grant is not retried: no raise, the connection goes to error.
    http.handler = lambda r: httpx.Response(401)
    assert await _recheck(events, NOW, Tokens()) == {'auth_failed': 1}
    assert db.execute('SELECT status FROM user_oauth_connections WHERE user_id = %s', (uid,)).fetchone() == {
        'status': 'error'
    }
    db.execute("UPDATE user_oauth_connections SET status = 'connected' WHERE user_id = %s", (uid,))

    # Nobody we know rests on that record, or the owner was never checked: no HubSpot call at all.
    calls = len(http.calls)
    assert await _recheck([_event('contact', 'unknown')], NOW, Tokens()) == {}
    fresh = _user(db)
    _hubspot(db, fresh, portal='777')
    assert await _recheck([_event('deal', '1', portal='777')], NOW, Tokens()) == {}
    assert len(http.calls) == calls


async def test_an_approved_send_does_not_go_out_once_a_block_lands(db, gated, http, provider, switches):
    """The approval queue can hold a claim for days: a block that lands meanwhile stops the send."""
    uid = _user(db)
    _hubspot(db, uid)
    _mailbox(db, uid)
    _campaign(db, uid, approve=True, emails=('anna@client.test', 'bea@client.test'))
    http.handler = _contacts({}, people={'anna@client.test': {}, 'bea@client.test': {}})
    await _beat(NOW)
    db.execute("UPDATE outreach_campaign_sends SET status = 'approved', approved_at = now()")

    # Sales calls anna before the mailbox's turn: HubSpot tells us, the verdict becomes a cooldown.
    http.handler = _contacts({'anna@client.test': NOW.isoformat()}, people={'bea@client.test': {}})
    await _recheck([_event('contact', 'c-anna@client.test')], NOW, Tokens())
    assert (await each_org_async(db, dispatcher.send_due, NOW))['sent'] == 1
    assert (await each_org_async(db, dispatcher.send_due, NOW + timedelta(hours=1)))[
        'sent'
    ] == 0  # anna is not the next in line
    assert [msg['To'] for msg, _ in provider.sent] == ['bea@client.test']
    held = db.execute("SELECT status FROM outreach_campaign_sends WHERE recipient = 'anna@client.test'").fetchone()
    assert held['status'] == 'approved'  # still queued, not refused

    # The cooldown lapses: the same approved message goes out.
    assert (await each_org_async(db, dispatcher.send_due, NOW + timedelta(days=31)))['sent'] == 1
    assert [msg['To'] for msg, _ in provider.sent] == ['bea@client.test', 'anna@client.test']


async def test_a_hubspot_opt_out_leaves_every_campaign_for_good(db, gated, http):
    uid = _user(db)
    _hubspot(db, uid)
    first = _campaign(db, uid, emails=('out@client.test', 'anna@client.test'))
    http.handler = _contacts({}, people={'out@client.test': {'hs_email_optout': 'true'}})
    await _gate_read(NOW)
    assert each_org(db, dispatcher.plan, NOW)['excluded_crm'] == 1  # in the very beat that read the opt-out
    statuses = {
        r['email']: r['status'] for r in db.execute('SELECT email, status FROM outreach_campaign_targets').fetchall()
    }
    assert statuses == {'out@client.test': 'excluded', 'anna@client.test': 'active'}
    assert _claimed(db) == {'anna@client.test'}

    # Enrollment (create, activate, saved-search refill) skips them like the owner's own exclusions.
    campaign = db.execute('SELECT * FROM outreach_campaigns WHERE id = %s', (first,)).fetchone()
    other = {**campaign, 'id': None}  # a campaign they are not in yet
    context = runner.enrollment_context(db, other)
    assert runner.enrollment_verdict({'email': 'Out@Client.test'}, other, context)[0] == 'skip'
    assert runner.enrollment_verdict({'email': 'new@client.test'}, other, context)[0] == 'enroll'
    # Without HubSpot connected the old verdict no longer decides anything.
    db.execute("UPDATE user_oauth_connections SET status = 'disconnected' WHERE provider = 'hubspot'")
    context = runner.enrollment_context(db, other)
    assert runner.enrollment_verdict({'email': 'out2@client.test'}, other, context)[0] == 'enroll'
    assert gate.opted_out_rules(db, uid) == frozenset()


def _people(db) -> dict[tuple[str, str], str]:
    """(client, address or else LinkedIn URL) of each target, and its status."""
    rows = db.execute(
        "SELECT c.project_id::text AS client, CASE WHEN t.email <> '' THEN t.email ELSE t.linkedin_url END AS who,"
        ' t.status FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
    ).fetchall()
    return {(r['client'], r['who']): r['status'] for r in rows}


def _opted_out(db, uid, email) -> None:
    """The verdict a HubSpot webhook stores for an address that opted out."""
    db.execute(
        'INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, %s, %s, %s)',
        (uid, email, gate.OPTED_OUT_REASON, NOW),
    )


PROFILE = 'https://www.linkedin.com/in/anna-rossi/'


async def test_a_hubspot_opt_out_follows_the_person_to_their_other_address_and_linkedin(db, gated, http):
    """#1121: HubSpot names one address. The person who holds it (#937) also holds a second address and a LinkedIn
    profile: all three leave the client's campaigns in the beat that read the opt-out, as enrollment skips them.
    Another client of the same owner does not know the link, so there only the named address leaves."""
    uid = _user(db)
    _hubspot(db, uid)
    client, other = project(db, uid), second_project(db, uid)
    person(
        db,
        client,
        ('email', 'a@client.test'),
        ('email', 'b@client.test'),
        ('linkedin_url', 'linkedin.com/in/anna-rossi'),
    )
    people = [('a@client.test', ''), ('B@Client.test', ''), ('', PROFILE), ('anna@client.test', '')]
    for pid in (client, other):
        dispatcher_tests._client_campaign(db, uid, pid, people)
    http.handler = _contacts(
        {}, people={'a@client.test': {'hs_email_optout': 'true'}, 'b@client.test': {}, 'anna@client.test': {}}
    )
    await _gate_read(NOW)
    assert each_org(db, dispatcher.plan, NOW)['excluded_crm'] == 4
    excluded = {person for person, status in _people(db).items() if status == 'excluded'}
    assert excluded == {
        (client, 'a@client.test'),
        (client, 'B@Client.test'),
        (client, PROFILE),
        (other, 'a@client.test'),
    }
    claimed_excluded = db.execute(
        'SELECT count(*) AS n FROM outreach_campaign_sends s JOIN outreach_campaign_targets t ON t.id = s.target_id'
        " WHERE t.status = 'excluded'"
    ).fetchone()
    assert claimed_excluded == {'n': 0}


async def test_an_approved_send_to_the_persons_other_address_waits_once_the_opt_out_lands(
    db, gated, http, provider, switches
):
    """#1121, send side: the claim for b@ waits for approval when the opt-out of a@, the same person, lands by
    webhook. No plan ran since, so the target is still active: the send gate holds the claim."""
    uid = _user(db)
    _hubspot(db, uid)
    _mailbox(db, uid)
    person(db, project(db, uid), ('email', 'a@client.test'), ('email', 'b@client.test'))
    _campaign(db, uid, approve=True, emails=('b@client.test', 'anna@client.test'))
    http.handler = _contacts({}, people={'b@client.test': {}, 'anna@client.test': {}})
    await _beat(NOW)
    db.execute("UPDATE outreach_campaign_sends SET status = 'approved', approved_at = now()")
    _opted_out(db, uid, 'a@client.test')

    assert (await each_org_async(db, dispatcher.send_due, NOW))['sent'] == 1
    assert (await each_org_async(db, dispatcher.send_due, NOW + timedelta(hours=1)))['sent'] == 0
    assert [msg['To'] for msg, _ in provider.sent] == ['anna@client.test']


async def test_an_approved_linkedin_invite_to_the_person_waits_once_the_opt_out_lands(db, gated, unipile, switches):
    """#1121, send side, a target with no address: only the person's LinkedIn profile links it to the opt-out."""
    uid = _user(db)
    _hubspot(db, uid)
    dispatcher_tests._linkedin(db, uid)
    person(db, project(db, uid), ('email', 'a@client.test'), ('linkedin_url', 'linkedin.com/in/anna-rossi'))
    dispatcher_tests._li_campaign(db, uid)
    db.execute("UPDATE outreach_campaign_targets SET email = ''")
    each_org(db, dispatcher.plan, NOW)
    assert [s['status'] for s in _sends(db)] == ['approved']
    _opted_out(db, uid, 'a@client.test')

    assert (await each_org_async(db, dispatcher.send_linkedin_due, NOW))['linkedin_sent'] == 0
    assert unipile.calls == []


async def test_the_exclusion_reaches_exactly_whom_the_send_gate_holds(db, gated, http):
    """The exclusion starts from the verdicts, the send gate from each target (``outreach_person_emails``): the two
    forms of the rule must reach the same people. Cases: case and spaces, a role address (never an alias, still the
    address itself), LinkedIn URL forms, a fragment that the alias normalization keeps, an identity of another
    client, and a person with no opt-out."""
    uid = _user(db)
    _hubspot(db, uid)
    client, other = project(db, uid), second_project(db, uid)
    person(db, client, ('email', 'a@x.test'), ('email', 'b@x.test'), ('linkedin_url', 'linkedin.com/in/p1'))
    person(db, client, ('email', 'c@x.test'), ('linkedin_url', 'linkedin.com/in/p2'))
    person(db, other, ('email', 'b@x.test'), ('linkedin_url', 'linkedin.com/in/p3'))
    people = [
        (' A@X.test', ''),
        ('b@x.test', ''),
        ('c@x.test', ''),
        ('info@x.test', ''),
        ('zed@x.test', 'https://linkedin.com/in/p2'),
        ('', 'https://www.linkedin.com/in/p1/?trk=1'),
        ('', 'http://LinkedIn.com/in/P1/'),
        ('', 'https://linkedin.com/in/p1#about'),
        ('', 'https://linkedin.com/in/p3'),
    ]
    for pid in (client, other):
        for one in people:  # a campaign each: one campaign holds a LinkedIn profile once
            dispatcher_tests._client_campaign(db, uid, pid, [one])
    for email in ('a@x.test', 'info@x.test'):
        _opted_out(db, uid, email)
    held = {
        r['id']
        for r in db.execute(
            'SELECT t.id FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
            ' WHERE EXISTS (SELECT 1 FROM outreach_person_emails(c.project_id, t.email, t.linkedin_url) e'
            ' JOIN crm_gate_checks g ON g.owner_id = t.owner_id AND g.email = e.email WHERE g.reason = %s)',
            (gate.OPTED_OUT_REASON,),
        )
    }
    each_org(db, dispatcher.plan, NOW)
    excluded = db.execute("SELECT id FROM outreach_campaign_targets WHERE status = 'excluded'").fetchall()
    assert {r['id'] for r in excluded} == held
    reached = {person for person, status in _people(db).items() if status == 'excluded'}
    assert reached == {
        (client, ' A@X.test'),
        (client, 'b@x.test'),
        (client, 'info@x.test'),
        (client, 'https://www.linkedin.com/in/p1/?trk=1'),
        (client, 'http://LinkedIn.com/in/P1/'),
        (other, ' A@X.test'),
        (other, 'info@x.test'),
    }
