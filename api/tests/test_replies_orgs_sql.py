"""A reply is filed, stops targets and is announced in one organization; an opt-out applies in all (#414).

Needs TEST_DATABASE_URL. Webhook routing is real here: an endpoint in each organization, so an event that reaches
the wrong one shows.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.reply_address import build_campaign_reply_address, build_reply_address
from app.integrations import webhooks
from app.integrations.webhooks import _subscribed as real_subscribed
from app.main import app
from app.outreach import sending
from tests.conftest import member, project, scoped_conn
from tests.test_replies_sql import (
    QUEUED,
    _campaign,
    _post,
    _target,
    _token,
    fake_redis,  # noqa: F401  # pytest fixture, requested by name
)
from tests.test_replies_sql import db as db
from tests.test_tools_sql import _user

EMAIL = 'anna@client.test'
ONE_CLICK = {
    'content': b'List-Unsubscribe=One-Click',
    'headers': {'Content-Type': 'application/x-www-form-urlencoded'},
}


class World:
    def __init__(self, c):
        self.c = c
        self.uid = _user(c)
        self.own = member(c, self.uid).org_id

    def client(self) -> tuple[str, str]:
        boss = _user(self.c)
        pid, org = project(self.c, boss), member(self.c, boss).org_id
        self.c.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (org, self.uid, 'member'),
        )
        return org, pid

    def campaign(self, project_id: str | None = None, *, age_days: int = 0) -> dict:
        camp = _campaign(self.c, self.uid, client=project_id)
        self.c.execute(
            'UPDATE outreach_campaign_sends SET created_at = now() - make_interval(days => %(d)s),'
            ' delivered_at = CASE WHEN delivered_at IS NOT NULL THEN now() - make_interval(days => %(d)s) END'
            ' WHERE campaign_id = %(c)s',
            {'d': age_days, 'c': camp['campaign']},
        )
        return camp

    def endpoint(self, org: str) -> None:
        self.c.execute(
            'INSERT INTO user_webhooks (organization_id, user_id, url, secret_encrypted, event_names, label)'
            " VALUES (%s, %s, 'https://hooks.test/a', 'x', %s, 'L')",
            (org, self.uid, ['contact.replied', 'sequence.stopped']),
        )

    def excluded_in(self) -> set[str]:
        rows = self.c.execute(
            "SELECT project_id::text AS p FROM exclusions WHERE kind = 'email' AND value = %s", (EMAIL,)
        )
        return {r['p'] for r in rows}


@pytest.fixture
def world(db, monkeypatch):
    monkeypatch.setattr(webhooks, '_subscribed', real_subscribed)
    monkeypatch.setattr(webhooks, 'get_conn', scoped_conn(db))
    return World(db)


def _events(webhook_events, event) -> list[str]:
    return [org for org, payload in webhook_events if payload['type'] == event]


def _filed_in(c, reply_id) -> str:
    return c.execute(
        'SELECT project_id::text AS p FROM outreach_inbound_messages WHERE id = %s', (reply_id,)
    ).fetchone()['p']


def _assert_reply_in(w, webhook_events, res, *, answered, other, org, project_id):
    assert res.status_code == 200 and res.json()['targets'] == 1
    assert _filed_in(w.c, res.json()['id']) == project_id
    assert (
        w.c.execute('SELECT campaign_id FROM outreach_inbound_messages').fetchone()['campaign_id']
        == answered['campaign']
    )
    assert _target(w.c, answered['target'])['status'] == 'replied'
    assert _target(w.c, other['target'])['status'] == 'active'
    assert _events(webhook_events, 'contact.replied') == [str(org)]
    assert _events(webhook_events, 'sequence.stopped') == [str(org)]


@pytest.mark.parametrize('quoted_own', [True, False])
def test_a_reply_quoting_a_send_stays_in_the_organization_of_that_campaign(world, webhook_events, quoted_own):
    """The quoted campaign is the older one: the newest mail does not outrank a quote."""
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca = w.campaign(age_days=2 if quoted_own else 0)
    cb = w.campaign(b_project, age_days=0 if quoted_own else 2)
    answered, other = (ca, cb) if quoted_own else (cb, ca)
    org, pid = (w.own, project(w.c, w.uid)) if quoted_own else (b_org, b_project)
    res = _post(TestClient(app), w.uid, headers={'References': answered['message_id']})
    _assert_reply_in(w, webhook_events, res, answered=answered, other=other, org=org, project_id=pid)


def test_a_reply_quoting_nothing_goes_where_the_newest_mail_to_the_person_went(world, webhook_events):
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(age_days=3), w.campaign(b_project, age_days=1)
    res = _post(TestClient(app), w.uid)
    _assert_reply_in(w, webhook_events, res, answered=cb, other=ca, org=b_org, project_id=b_project)
    assert [res.json()['id']] == QUEUED


def test_a_reply_from_a_person_only_clients_wrote_to_is_filed_and_stops_the_newest_organization(world, webhook_events):
    w = world
    (b_org, b_project), (c_org, c_project) = w.client(), w.client()
    w.endpoint(b_org)
    w.endpoint(c_org)
    cb, cc = w.campaign(b_project, age_days=2), w.campaign(c_project, age_days=1)
    res = _post(TestClient(app), w.uid)
    assert res.json()['status'] == 'accepted'
    _assert_reply_in(w, webhook_events, res, answered=cc, other=cb, org=c_org, project_id=c_project)


def test_an_opt_out_link_stops_and_excludes_in_every_organization(world, fake_redis, webhook_events):  # noqa: F811  # pytest fixture, requested by name
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(), w.campaign(b_project)
    res = TestClient(app).post(f'/v1/unsubscribe/{_token(w.uid)}', **ONE_CLICK)
    assert res.json() == {'status': 'unsubscribed'}
    assert [_target(w.c, t['target'])['status'] for t in (ca, cb)] == ['excluded', 'excluded']
    assert w.excluded_in() == {project(w.c, w.uid), b_project}
    assert sorted(_events(webhook_events, 'sequence.stopped')) == sorted([str(w.own), str(b_org)])


def test_an_opt_out_link_reaches_clients_when_the_owner_has_no_campaign_of_their_own(world, fake_redis):  # noqa: F811  # pytest fixture, requested by name
    w = world
    (_, b_project), (_, c_project) = w.client(), w.client()
    cb, cc = w.campaign(b_project), w.campaign(c_project)
    res = TestClient(app).post(f'/v1/unsubscribe/{_token(w.uid)}', **ONE_CLICK)
    assert res.json() == {'status': 'unsubscribed'}
    assert [_target(w.c, t['target'])['status'] for t in (cb, cc)] == ['excluded', 'excluded']
    assert w.excluded_in() == {b_project, c_project}


def test_an_opt_out_mail_stops_and_excludes_in_every_organization(world, webhook_events):
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(age_days=2), w.campaign(b_project)
    res = _post(TestClient(app), w.uid, subject='unsubscribe', text='')
    assert res.json()['kind'] == 'unsubscribe' and res.json()['targets'] == 2
    assert [_target(w.c, t['target'])['status'] for t in (ca, cb)] == ['replied', 'replied']
    assert w.excluded_in() == {project(w.c, w.uid), b_project}
    assert _filed_in(w.c, res.json()['id']) == b_project
    assert sorted(_events(webhook_events, 'sequence.stopped')) == sorted([str(w.own), str(b_org)])


def test_a_send_that_was_never_delivered_is_not_the_newest_mail(world, webhook_events):
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(age_days=3), w.campaign(b_project, age_days=5)
    w.c.execute(
        "UPDATE outreach_campaign_sends SET created_at = now() WHERE campaign_id = %s AND status = 'approved'",
        (cb['campaign'],),
    )
    res = _post(TestClient(app), w.uid)
    _assert_reply_in(w, webhook_events, res, answered=ca, other=cb, org=w.own, project_id=project(w.c, w.uid))


def test_an_auto_reply_pauses_the_person_in_every_organization_and_is_filed_in_one(world):
    w = world
    _, b_project = w.client()
    ca, cb = w.campaign(age_days=3), w.campaign(b_project, age_days=1)
    res = _post(TestClient(app), w.uid, subject='Out of office', headers={'Auto-Submitted': 'auto-replied'})
    assert res.json()['kind'] == 'auto_reply'
    assert [_target(w.c, t['target'])['status'] for t in (ca, cb)] == ['paused', 'paused']
    assert _filed_in(w.c, res.json()['id']) == b_project


def test_a_later_linkedin_send_is_not_the_newest_mail(world, webhook_events):
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(age_days=3), w.campaign(b_project, age_days=5)
    w.c.execute(
        "UPDATE outreach_campaign_sends SET channel = 'linkedin_message', status = 'sent', delivered_at = now()"
        " WHERE campaign_id = %s AND status = 'approved'",
        (cb['campaign'],),
    )
    res = _post(TestClient(app), w.uid)
    _assert_reply_in(w, webhook_events, res, answered=ca, other=cb, org=w.own, project_id=project(w.c, w.uid))


@pytest.mark.parametrize('quoted_own', [True, False])
def test_a_reply_to_a_deleted_members_old_address_stays_in_the_organization_it_quotes(
    world, webhook_events, quoted_own
):
    """#467: their campaigns outlive them in both organizations (``former_owner_id``), the quote still decides."""
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca = w.campaign(age_days=2 if quoted_own else 0)
    cb = w.campaign(b_project, age_days=0 if quoted_own else 2)
    answered, other = (ca, cb) if quoted_own else (cb, ca)
    org, pid = (w.own, project(w.c, w.uid)) if quoted_own else (b_org, b_project)
    w.c.execute('DELETE FROM "user" WHERE id = %s', (w.uid,))
    res = _post(TestClient(app), w.uid, headers={'References': answered['message_id']})
    _assert_reply_in(w, webhook_events, res, answered=answered, other=other, org=org, project_id=pid)


def test_a_deleted_members_opt_out_mail_stops_and_excludes_in_every_organization(world):
    w = world
    _, b_project = w.client()
    ca, cb = w.campaign(age_days=2), w.campaign(b_project)
    a_project = project(w.c, w.uid)
    w.c.execute('DELETE FROM "user" WHERE id = %s', (w.uid,))
    res = _post(TestClient(app), w.uid, subject='unsubscribe', text='')
    assert res.json()['kind'] == 'unsubscribe' and res.json()['targets'] == 2
    assert [_target(w.c, t['target'])['status'] for t in (ca, cb)] == ['replied', 'replied']
    assert w.excluded_in() == {a_project, b_project}


def _reply_to(camp) -> str:
    """The Reply-To the dispatcher mints for a send of ``camp``."""
    address = sending.reply_to(camp['campaign'])
    assert address is not None
    return address


def _written_in(c, org) -> int:
    return c.execute(
        'SELECT count(*) AS n FROM outreach_inbound_messages m JOIN projects p ON p.id = m.project_id'
        ' WHERE p.organization_id = %s',
        (org,),
    ).fetchone()['n']


@pytest.mark.parametrize('sender', ['Anna <anna@client.test>', 'Marco <marco@client.test>'])
def test_an_address_only_reply_to_a_campaign_is_filed_in_that_campaigns_organization(world, webhook_events, sender):
    """The person replies to B's older mail with no thread headers, from their own address or a colleague's: A wrote
    to them since. The address names B's campaign, so nothing ranks A's newer mail above it."""
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(age_days=1), w.campaign(b_project, age_days=3)
    res = _post(TestClient(app), w.uid, sender=sender, to=_reply_to(cb))
    assert res.status_code == 200 and res.json()['status'] == 'accepted'
    row = w.c.execute('SELECT project_id::text AS p, campaign_id, target_id FROM outreach_inbound_messages').fetchone()
    own_address = sender.startswith('Anna')
    assert row == {'p': b_project, 'campaign_id': cb['campaign'], 'target_id': cb['target'] if own_address else None}
    assert _target(w.c, cb['target'])['status'] == ('replied' if own_address else 'active')
    assert _target(w.c, ca['target'])['status'] == 'active'
    assert _written_in(w.c, w.own) == 0
    assert set(_events(webhook_events, 'contact.replied')) == {str(b_org)}


def test_the_old_owner_address_still_ranks_the_owners_campaigns(world, webhook_events):
    """Mail sent before the campaign form carries the owner's address: the person's newest mail still decides."""
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(age_days=1), w.campaign(b_project, age_days=3)
    old = build_reply_address(w.uid, domain='in.zenbree.test', secret='reply-secret')
    res = _post(TestClient(app), w.uid, to=old)
    _assert_reply_in(w, webhook_events, res, answered=ca, other=cb, org=w.own, project_id=project(w.c, w.uid))


@pytest.mark.parametrize('forgery', ['wrong secret', 'owner signature'])
def test_a_campaign_address_that_does_not_verify_is_dropped(world, forgery):
    """A signed id that names no campaign: tests/test_replies_deleted_campaign_sql.py."""
    w = world
    _, b_project = w.client()
    ca, cb = w.campaign(), w.campaign(b_project)
    if forgery == 'wrong secret':
        to = build_campaign_reply_address(cb['campaign'], domain='in.zenbree.test', secret='wrong')
    else:  # an owner's valid old-form signature, moved onto the campaign form
        old = build_reply_address(str(cb['campaign']), domain='in.zenbree.test', secret='reply-secret')
        to = 'reply+c' + str(old).removeprefix('reply+')
    assert _post(TestClient(app), w.uid, to=to).json() == {'status': 'ignored'}
    assert w.c.execute('SELECT count(*) AS n FROM outreach_inbound_messages').fetchone()['n'] == 0
    assert _target(w.c, ca['target'])['status'] == 'active'


def test_an_opt_out_to_a_campaign_address_stops_and_excludes_in_every_organization(world, webhook_events):
    """The List-Unsubscribe mailto is the campaign's address: the opt-out still reaches every organization (#460)."""
    w = world
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    ca, cb = w.campaign(), w.campaign(b_project, age_days=2)
    res = _post(TestClient(app), w.uid, subject='unsubscribe', text='', to=_reply_to(cb))
    assert res.json()['kind'] == 'unsubscribe' and res.json()['targets'] == 2
    assert [_target(w.c, t['target'])['status'] for t in (ca, cb)] == ['replied', 'replied']
    assert w.excluded_in() == {project(w.c, w.uid), b_project}
    assert _filed_in(w.c, res.json()['id']) == b_project
    assert sorted(_events(webhook_events, 'sequence.stopped')) == sorted([str(w.own), str(b_org)])


def test_an_auto_reply_to_a_campaign_address_pauses_everywhere_and_is_filed_in_that_campaign(world):
    w = world
    _, b_project = w.client()
    ca, cb = w.campaign(age_days=1), w.campaign(b_project, age_days=3)
    auto = {'subject': 'Out of office', 'headers': {'Auto-Submitted': 'auto-replied'}, 'to': _reply_to(cb)}
    res = _post(TestClient(app), w.uid, email_id='ooo', **auto)
    assert res.json()['kind'] == 'auto_reply'
    assert [_target(w.c, t['target'])['status'] for t in (ca, cb)] == ['paused', 'paused']
    assert _filed_in(w.c, res.json()['id']) == b_project
    assert _post(TestClient(app), w.uid, email_id='ooo', **auto).json() == {'status': 'duplicate'}
