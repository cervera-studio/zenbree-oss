"""Instantly events (#930): the webhook route and the reconciliation beat against real SQL. Needs TEST_DATABASE_URL.

Instantly is a fake (``Fake``) that stands in for app/integrations/instantly_sync/client.py: no call leaves the test.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.errors import err, ok
from app.core.scope import acting_for
from app.core.system_reasons import SystemReason
from app.integrations import instantly_store
from app.integrations.instantly_sync import client, outcomes, reconcile, stops, subscription, webhook
from app.integrations.instantly_sync.client import HEADER, Page
from app.main import app
from tests.conftest import call_system, scoped_conn
from tests.test_instantly_events_sql import CAMPAIGN, DB_URL, WS, db, hook, record, seed
from tests.test_instantly_events_sql import email as api_email

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: Instantly sync SQL test NOT run')
fixtures = (db,)
SECRET = 'a-secret-of-the-test'
NOW = datetime.now(UTC).replace(microsecond=0)


def subscribe(db, b, *, secret=SECRET, status='registered') -> str:
    return db.execute(
        'INSERT INTO instantly_webhook_subscriptions (organization_id, project_id, connection_id, workspace_id,'
        ' secret_sha256, remote_id, status) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id::text',
        (b.organization_id, b.project_id, b.connection_id, WS, subscription.digest(secret), 'wh-1', status),
    ).fetchone()['id']


@pytest.fixture
def post(db, monkeypatch):
    monkeypatch.setattr(webhook, 'get_conn', scoped_conn(db))
    http = TestClient(app)

    def send(sub_id, payload=None, *, secret=SECRET, content=None):
        body = {
            'event_type': 'email_sent',
            'timestamp': NOW.isoformat(),
            'workspace': WS,
            'campaign_id': CAMPAIGN,
            'lead_email': 'ana@acme.test',
            'step': 1,
            **(payload or {}),
        }
        headers = {HEADER: secret} if secret else {}
        url = f'/v1/integrations/instantly/webhooks/{sub_id}'
        return (
            http.post(url, headers=headers, content=content) if content else http.post(url, headers=headers, json=body)
        )

    return send


def test_an_authenticated_delivery_is_recorded_once_and_makes_the_webhook_live(db, post):
    b = seed(db)
    sub = subscribe(db, b)
    assert post(sub).json() == {'status': 'recorded'}
    assert post(sub).json() == {'status': 'duplicate'}
    assert post(sub, {'event_type': 'email_opened'}).json() == {'status': 'ignored'}
    row = db.execute('SELECT status, last_delivery_at FROM instantly_webhook_subscriptions').fetchone()
    assert row['status'] == 'live' and row['last_delivery_at'] is not None
    assert db.execute('SELECT count(*) AS n FROM instantly_events').fetchone()['n'] == 1


def test_a_delivery_without_the_secret_of_its_subscription_is_refused(db, post):
    mine, theirs = seed(db), seed(db, campaign=str(uuid.uuid4()))
    sub = subscribe(db, mine)
    other = subscribe(db, theirs, secret='their-secret')
    for answer in (
        post(sub, secret=''),
        post(sub, secret='wrong'),
        post(other, secret=SECRET),  # my secret on another organization's subscription
        post('00000000-0000-0000-0000-000000000000'),
    ):
        assert answer.status_code == 401
        assert answer.json()['detail']['code'] == 'INTEGRATIONS.WEBHOOK_UNAUTHORIZED'
    assert db.execute('SELECT count(*) AS n FROM instantly_events').fetchone()['n'] == 0


def test_a_retired_subscription_or_a_disconnected_workspace_is_refused(db, post):
    retired, gone = seed(db), seed(db, campaign=str(uuid.uuid4()))
    first = subscribe(db, retired)
    with acting_for(retired.organization_id, 'test'), scoped_conn(db)() as conn:
        leftover = subscription.retire_all(conn, retired.project_id, 'key')
    assert leftover.remote_ids == ['wh-1']
    second = subscribe(db, gone)
    db.execute("UPDATE instantly_connections SET status = 'disconnected' WHERE id = %s", (gone.connection_id,))
    assert post(first).status_code == 401 and post(second).status_code == 401


def test_a_body_too_large_or_not_an_object_is_refused(db, post):
    sub = subscribe(db, seed(db))
    big = post(sub, content=b'{"x": "' + b'a' * 1_100_000 + b'"}')
    assert big.status_code == 413 and big.json()['detail']['code'] == 'REQUEST.TOO_LARGE'
    assert post(sub, content=b'[1, 2]').status_code == 422


class Fake:
    """Instantly as the beat sees it: two email lists, a campaign's leads and the webhook calls, each counted."""

    def __init__(self, *, create=None, emails=None, leads=None):
        self.calls: list[str] = []
        self.create = create or ok('wh-new')
        self.emails = emails or {'sent': [[]], 'received': [[]]}
        self.leads = leads or []

    async def create_webhook(self, key, *, url, secret, name):
        self.calls.append('create_webhook')
        return self.create

    async def webhook_status(self, key, remote_id):
        self.calls.append('webhook_status')
        return ok(1)

    async def delete_webhook(self, key, remote_id):
        self.calls.append('delete_webhook')
        return ok(None)

    async def list_emails(self, key, *, email_type, since, cursor):
        self.calls.append(f'emails:{email_type}:{cursor}')
        pages = self.emails[email_type]
        index = int(cursor or 0)
        return ok(Page(items=pages[index], next_cursor=str(index + 1) if index + 1 < len(pages) else None))

    async def list_leads(self, key, *, campaign_id, emails):
        self.calls.append(f'leads:{",".join(emails)}')
        return ok(Page(items=[lead for lead in self.leads if lead['email'] in emails], next_cursor=None))


@pytest.fixture
def beat(db, http, monkeypatch):  # http: a stop the beat attempts never reaches the real Instantly
    for module in (reconcile, subscription, stops):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    monkeypatch.setattr(reconcile, 'over_limit', lambda *_: False)
    monkeypatch.setattr(reconcile, 'is_disabled', lambda _: False)
    monkeypatch.setattr(instantly_store, 'api_key', lambda c: ok('key') if c['status'] == 'connected' else err('X'))
    monkeypatch.setattr(subscription.settings, 'api_public_base_url', 'https://api.zenbree.test')

    def run(fake: Fake) -> dict:
        for name in ('create_webhook', 'webhook_status', 'delete_webhook', 'list_emails', 'list_leads'):
            monkeypatch.setattr(client, name, getattr(fake, name))
        return call_system(SystemReason.DUE_WORK, asyncio.run, reconcile.run())

    return run


def state(db, b) -> dict:
    return db.execute('SELECT * FROM instantly_sync_state WHERE connection_id = %s', (b.connection_id,)).fetchone()


def sent(ref, at=NOW):
    return {
        'id': ref,
        'ue_type': 1,
        'lead': 'ana@acme.test',
        'campaign_id': CAMPAIGN,
        'timestamp_email': at.isoformat(),
    }


def test_a_pass_registers_the_webhook_reads_every_source_and_waits_for_the_next(db, beat):
    b = seed(db)
    reply = {**sent('em-2'), 'ue_type': 2, 'is_auto_reply': 0}
    fake = Fake(
        emails={'sent': [[sent('em-1')]], 'received': [[reply]]},
        leads=[{'email': 'ana@acme.test', 'status': -1}, {'email': 'own@acme.test', 'status': -1}],
    )
    assert beat(fake) == {'connections': 1}
    assert fake.calls == ['create_webhook', 'emails:sent:None', 'emails:received:None', 'leads:ana@acme.test']
    kinds = [r['kind'] for r in db.execute('SELECT kind FROM instantly_events ORDER BY kind').fetchall()]
    assert kinds == ['bounced', 'reply', 'sent']
    s = state(db, b)
    assert s['sent_watermark'] and s['received_watermark'] and s['leads_round_done_at'] and not s['sent_cursor']
    assert s['last_success_at'] and s['claim_token'] is None and s['next_run_at'] > NOW + timedelta(minutes=4)
    assert db.execute('SELECT status, remote_id FROM instantly_webhook_subscriptions').fetchone() == {
        'status': 'registered',
        'remote_id': 'wh-new',
    }
    assert db.execute('SELECT reason FROM outreach_stops').fetchone() == {'reason': 'human_reply'}
    again = Fake()
    assert beat(again) == {'connections': 1} and again.calls == []  # its stop is due, no read is due yet


def test_a_plan_without_webhooks_is_polled_and_asked_again_a_day_later(db, beat):
    b = seed(db)
    fake = Fake(create=err('INSTANTLY.PLAN_REQUIRED'), emails={'sent': [[sent('em-1')]], 'received': [[]]})
    beat(fake)
    assert state(db, b)['webhook_refusal'] == 'plan' and 'emails:sent:None' in fake.calls
    db.execute('UPDATE instantly_sync_state SET next_run_at = now()')
    again = Fake(create=err('INSTANTLY.PLAN_REQUIRED'))
    beat(again)
    assert 'create_webhook' not in again.calls
    assert (
        db.execute("SELECT count(*) AS n FROM instantly_webhook_subscriptions WHERE status <> 'retired'").fetchone()[
            'n'
        ]
        == 0
    )


def test_a_long_read_keeps_its_cursor_and_comes_back_soon(db, beat):
    b = seed(db)
    pages = [[sent(f'em-{i}', NOW - timedelta(minutes=30 - i))] for i in range(reconcile.PAGES + 2)]
    beat(Fake(emails={'sent': pages, 'received': [[]]}))
    s = state(db, b)
    assert s['sent_cursor'] == str(reconcile.PAGES) and s['sent_watermark'] is None
    assert s['next_run_at'] < datetime.now(UTC) + timedelta(minutes=2)
    db.execute('UPDATE instantly_sync_state SET next_run_at = now()')
    rest = Fake(emails={'sent': pages, 'received': [[]]})
    beat(rest)
    assert rest.calls[0] == f'emails:sent:{reconcile.PAGES}' and state(db, b)['sent_watermark'] is not None
    assert db.execute('SELECT count(*) AS n FROM instantly_events').fetchone()['n'] == reconcile.PAGES + 2


def test_a_pass_due_by_the_database_clock_reads_with_a_worker_clock_behind_it(db, beat, monkeypatch):
    b = seed(db)
    beat(Fake(emails={'sent': [[]], 'received': [[]]}))
    db.execute('UPDATE instantly_sync_state SET next_run_at = now()')

    class Behind(datetime):  # a worker host whose clock trails the database's
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) - timedelta(seconds=1)

    monkeypatch.setattr(reconcile, 'datetime', Behind)
    again = Fake(emails={'sent': [[sent('em-1')]], 'received': [[]]})
    beat(again)
    assert 'emails:sent:None' in again.calls and state(db, b)['next_run_at'] > datetime.now(UTC)


def test_a_refused_key_marks_the_connection_and_stops_its_passes(db, beat):
    b = seed(db)
    beat(Fake(create=err('INSTANTLY.INVALID_KEY')))
    assert state(db, b)['last_error_code'] == 'INSTANTLY.INVALID_KEY' and state(db, b)['failing_since']
    connection = db.execute('SELECT status FROM instantly_connections WHERE id = %s', (b.connection_id,)).fetchone()
    assert connection['status'] == 'error'
    db.execute('UPDATE instantly_sync_state SET next_run_at = now()')
    later = Fake()
    beat(later)
    assert later.calls == []


def test_the_kill_switch_and_a_disconnect_stop_every_call_and_keep_the_facts(db, beat, monkeypatch):
    b = seed(db)
    beat(Fake(emails={'sent': [[sent('em-1')]], 'received': [[]]}))
    db.execute('UPDATE instantly_sync_state SET next_run_at = now()')
    monkeypatch.setattr(reconcile, 'is_disabled', lambda _: True)
    paused = Fake()
    assert beat(paused) == {'paused': True} and paused.calls == []
    monkeypatch.setattr(reconcile, 'is_disabled', lambda _: False)
    db.execute("UPDATE instantly_connections SET status = 'disconnected' WHERE id = %s", (b.connection_id,))
    gone = Fake()
    beat(gone)
    assert gone.calls == []
    assert db.execute('SELECT count(*) AS n FROM instantly_events').fetchone()['n'] == 1


def test_a_webhook_reply_brings_the_next_pass_within_a_minute_and_it_stops_the_lead(db, post, beat):
    b = seed(db)
    subscribe(db, b, status='live')
    db.execute("UPDATE instantly_sync_state SET next_run_at = now() + interval '1 hour'")
    assert post(subscribe_id(db), {'event_type': 'reply_received'}).json() == {'status': 'recorded'}
    assert state(db, b)['next_run_at'] < datetime.now(UTC) + timedelta(seconds=70)
    db.execute('UPDATE instantly_sync_state SET next_run_at = now()')
    reply = {**sent('em-9'), 'ue_type': 2, 'is_auto_reply': 0}
    beat(Fake(emails={'sent': [[]], 'received': [[reply]]}))
    assert db.execute('SELECT reason FROM outreach_stops').fetchone() == {'reason': 'human_reply'}
    assert state(db, b)['next_run_at'] > datetime.now(UTC) + timedelta(minutes=50)  # classified, live: an hour


def test_a_key_without_emails_read_still_gets_the_leads_round(db, beat):
    b = seed(db)
    fake = Fake(leads=[{'email': 'ana@acme.test', 'status': -2}])

    async def refused(key, *, email_type, since, cursor):
        fake.calls.append(f'emails:{email_type}')
        return err('INSTANTLY.MISSING_SCOPE')

    fake.list_emails = refused
    beat(fake)
    assert fake.calls[-1] == 'leads:ana@acme.test'
    assert [r['kind'] for r in db.execute('SELECT kind FROM instantly_events').fetchall()] == ['unsubscribed']
    s = state(db, b)  # a limit of the key, not a failure: no backoff, the card says what is not read
    assert s['emails_refused_at'] and s['last_error_code'] is None and s['failing_since'] is None


def subscribe_id(db) -> str:
    return db.execute('SELECT id::text FROM instantly_webhook_subscriptions').fetchone()['id']


def test_a_reconnect_asks_for_the_webhook_again_at_once(db, beat):
    b = seed(db)
    beat(Fake(create=err('INSTANTLY.PLAN_REQUIRED')))
    # The user upgraded and reconnected: the pass comes at once, not after the wait the last pass set.
    db.execute('UPDATE instantly_connections SET connected_at = clock_timestamp()')
    assert state(db, b)['next_run_at'] > datetime.now(UTC)
    again = Fake()
    beat(again)
    assert 'create_webhook' in again.calls and state(db, b)['webhook_refusal'] is None


def extra_campaigns(db, b, count, email='ana@acme.test') -> None:
    """``count`` more campaigns that hold ``email``, in the enrollment of ``seed``."""
    eid = db.execute('SELECT id FROM instantly_enrollments WHERE connection_id = %s', (b.connection_id,)).fetchone()
    for _ in range(count):
        db.execute(
            'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email,'
            " outcome) VALUES (%s, %s, %s, %s, %s, %s, 'accepted')",
            (b.organization_id, b.project_id, eid['id'], WS, str(uuid.uuid4()), email),
        )


@pytest.mark.parametrize('shape', ['five_campaigns', 'one_large_campaign'])
def test_a_round_that_ends_on_the_last_call_of_a_pass_is_done(db, beat, shape):
    if shape == 'five_campaigns':
        b = seed(db)
        extra_campaigns(db, b, reconcile.PAGES - 1)
    else:  # 450 held leads: five calls of 100 or fewer
        b = seed(db, tuple(f'p{i:03}@acme.test' for i in range(450)))
    beat(Fake())
    s = state(db, b)
    assert s['leads_round_done_at'] is not None and s['leads_campaign_id'] is None
    assert s['next_run_at'] > datetime.now(UTC) + timedelta(minutes=4)  # not catching up for ever


def test_the_feed_gives_a_send_once_the_api_listed_it(db):
    b = seed(db)
    record(db, b, hook('email_sent', at=NOW), hook('email_sent', at=NOW + timedelta(seconds=7)))
    with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
        assert outcomes.changes(conn, b.project_id, after=0).items == []
    record(db, b, api_email(1, 'em-1', at=NOW + timedelta(seconds=2), step=1))
    with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
        assert [f.kind for f in outcomes.changes(conn, b.project_id, after=0).items] == ['sent']


def test_a_webhook_made_after_a_disconnect_is_deleted(db, beat):
    b = seed(db)
    fake = Fake()
    made = fake.create_webhook

    async def create_then_disconnect(key, *, url, secret, name):
        with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
            subscription.retire_all(conn, b.project_id, key)  # the user disconnects while the call is in flight
        return await made(key, url=url, secret=secret, name=name)

    fake.create_webhook = create_then_disconnect
    beat(fake)
    assert 'delete_webhook' in fake.calls
    assert (
        db.execute("SELECT count(*) AS n FROM instantly_webhook_subscriptions WHERE status <> 'retired'").fetchone()[
            'n'
        ]
        == 0
    )


def test_a_key_without_emails_read_gives_webhook_facts_classified_by_our_vote_alone(db, post, beat):
    b = seed(db)
    sub = subscribe(db, b, status='live')
    fake = Fake()

    async def refused(key, *, email_type, since, cursor):
        return err('INSTANTLY.MISSING_SCOPE')

    fake.list_emails = refused
    beat(fake)
    post(sub)  # a send
    before = state(db, b)['next_run_at']
    post(sub, {'event_type': 'reply_received'})
    assert state(db, b)['next_run_at'] == before  # no class will come: the reply moves no pass
    db.execute('UPDATE instantly_sync_state SET next_run_at = now()')
    beat(fake)
    with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
        # The feed shows the reply as the stop reads it: human, since no vote will come.
        items = outcomes.changes(conn, b.project_id, after=0).items
        assert [(f.kind, f.reply_class) for f in items] == [('sent', None), ('reply', 'human')]
        assert outcomes.coverage(conn, b.project_id).emails_refused
    assert db.execute('SELECT reason FROM outreach_stops').fetchone() == {'reason': 'human_reply'}
    assert state(db, b)['next_run_at'] > datetime.now(UTC) + timedelta(minutes=50)  # no class will come: no polling


def test_a_reconnect_clears_the_emails_limit_until_a_pass_reads_again(db, beat):
    b = seed(db)
    beat(Fake())
    db.execute('UPDATE instantly_sync_state SET emails_refused_at = now()')
    with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
        assert outcomes.coverage(conn, b.project_id).emails_refused
    db.execute("UPDATE instantly_connections SET connected_at = now() + interval '1 second'")
    with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
        assert not outcomes.coverage(conn, b.project_id).emails_refused


def test_a_disconnect_before_the_create_makes_no_webhook(db, beat):
    b = seed(db)
    db.execute("UPDATE instantly_connections SET status = 'disconnected' WHERE id = %s", (b.connection_id,))
    with acting_for(b.organization_id, 'test'):
        assert subscription._insert(b, subscription.digest('s')) is None
