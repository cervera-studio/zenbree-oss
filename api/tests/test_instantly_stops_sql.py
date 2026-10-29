import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
import pytest

from app.core.errors import err
from app.core.scope import acting_for
from app.core.system_reasons import SystemReason
from app.integrations.instantly_sync import client, effects, mapping, reconcile, stops, store, subscription
from app.outreach import holds, stop_outbox
from app.outreach.stop_outbox import Refused
from tests.conftest import call_system, member, project, scoped_conn
from tests.test_instantly_sql import _as, _confirm, _connect, _run, _setup
from tests.test_instantly_sql import db as db
from tests.test_instantly_sql import fake as fake
from tests.test_instantly_sync_sql import Fake
from tests.test_tools_sql import _user

PATH = '/v1/integrations/instantly/stops'


class Leads:
    def __init__(self, campaign):
        self.fake = campaign
        self.status_by_lead_id: dict[str, int] = {}
        self.mode: Literal['ok', 'down', 'no_scope'] = 'ok'
        self.calls: list[str] = []
        self.inner = campaign.handler
        self.on_get: Callable[[], object] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith('/api/v2/leads/') or path in ('/api/v2/leads/add', '/api/v2/leads/list'):
            return self.inner(request)
        self.calls.append(f'{request.method} {path}')
        if request.headers.get('authorization') != f'Bearer {self.fake.valid_key}':
            return httpx.Response(401, json={})
        if self.mode == 'down':
            return httpx.Response(503, json={})
        lead_id = path.rsplit('/', 1)[1]
        email = next((e for e, i in self.fake.leads.items() if i == lead_id), None)
        if email is None:
            return httpx.Response(404, json={})
        if request.method == 'GET' and self.on_get:
            self.on_get()
        if request.method == 'DELETE':
            if self.mode == 'no_scope':  # Instantly answers a key without leads:delete with 401, after a good GET
                return httpx.Response(401, json={})
            del self.fake.leads[email]
        return httpx.Response(200, json={'id': lead_id, 'status': self.status_by_lead_id.get(lead_id, 1)})


@pytest.fixture
def instantly(db, fake, http, monkeypatch):
    leads = Leads(fake)
    http.handler = leads.handler
    for module in (reconcile, subscription, stops):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    monkeypatch.setattr(reconcile, 'over_limit', lambda *_: False)
    monkeypatch.setattr(reconcile, 'is_disabled', lambda _: False)
    monkeypatch.setattr('app.outreach.stop_routes.is_disabled', lambda _: False)
    monkeypatch.setattr(subscription.settings, 'api_public_base_url', 'https://api.zenbree.test')
    return leads


@pytest.fixture
def beat(instantly, monkeypatch):
    def run(reads: Fake | None = None) -> dict:
        reads = reads or Fake()
        for name in ('create_webhook', 'webhook_status', 'delete_webhook', 'list_emails', 'list_leads'):
            monkeypatch.setattr(client, name, getattr(reads, name))
        return call_system(SystemReason.DUE_WORK, asyncio.run, reconcile.run())

    return run


class NoEmails(Fake):
    async def list_emails(self, key, *, email_type, since, cursor):
        self.calls.append(f'emails:{email_type}:{cursor}')
        return err('INSTANTLY.MISSING_SCOPE')


class World:
    def __init__(self, db, n=3):
        self.db = db
        self.uid, self.client, lid, ids = _setup(db, n)
        _run(db, self.uid, _confirm(self.client, lid, ids).json()['id'])
        self.project = project(db, self.uid)
        self.connection = str(db.execute('SELECT id FROM instantly_connections').fetchone()['id'])
        self.emails = [r['email'] for r in db.execute('SELECT email FROM instantly_leads ORDER BY email')]

    def stop(self, *emails: str, source: str | None = None) -> None:
        stop_outbox.request(
            self.db,
            project_id=self.project,
            emails=list(emails),
            reason='human_reply',
            cause='inbound:test',
            source_lead_id=source,
        )

    def lead(self, email: str) -> dict:
        return self.db.execute(
            'SELECT id::text, instantly_lead_id FROM instantly_leads WHERE email = %s', (email,)
        ).fetchone()

    def rows(self) -> dict[str, dict]:
        return {
            r.pop('email'): r
            for r in self.db.execute(
                'SELECT email, status, attempts, last_error_code AS code, confirmed_by FROM outreach_stops'
            )
        }

    def hold(self, email: str) -> str | None:
        return holds.hold(self.db, self.project, 'email', email, None)

    def due_now(self) -> None:
        self.db.execute("UPDATE outreach_stops SET next_attempt_at = now() WHERE status = 'pending'")


def test_a_pass_removes_a_stopped_person_first_even_when_the_key_may_not_read_emails(db, beat, instantly, fake):
    w = World(db)
    ann = w.emails[0]
    w.stop(ann)
    assert w.hold(ann) == 'stop_pending'
    reads = NoEmails()
    beat(reads)
    assert 'emails:sent:None' in reads.calls
    assert w.rows()[ann] == {'status': 'confirmed', 'attempts': 1, 'code': None, 'confirmed_by': 'provider'}
    lead = w.lead(ann)['instantly_lead_id']
    assert instantly.calls == [f'GET /api/v2/leads/{lead}', f'DELETE /api/v2/leads/{lead}']
    assert ann not in fake.leads and len(fake.leads) == len(w.emails) - 1
    assert w.hold(ann) == 'stopped'


def test_a_lead_instantly_already_stopped_is_confirmed_and_kept(db, beat, instantly, fake):
    w = World(db)
    ann = w.emails[0]
    instantly.status_by_lead_id[w.lead(ann)['instantly_lead_id']] = -2
    w.stop(ann)
    beat()
    assert w.rows()[ann]['status'] == 'confirmed' and ann in fake.leads
    assert [c.split()[0] for c in instantly.calls] == ['GET']


def test_the_lead_whose_reply_caused_the_stop_is_never_deleted(db, beat, instantly, fake):
    w = World(db)
    ann, bea = w.emails[:2]
    w.stop(ann, bea, source=w.lead(ann)['id'])
    beat()
    rows = w.rows()
    assert rows[ann]['status'] == 'failed' and rows[ann]['code'] == 'INSTANTLY.STOP_IN_INSTANTLY'
    assert rows[bea]['status'] == 'confirmed'
    assert ann in fake.leads and bea not in fake.leads
    assert w.hold(ann) == 'stop_failed'


def test_an_instantly_reply_of_the_lead_while_the_drain_reads_it_sends_no_delete(db, beat, instantly, fake):
    w = World(db, n=1)
    ann = w.emails[0]
    w.stop(ann)
    lead = db.execute(
        'SELECT organization_id::text, workspace_id, campaign_id FROM instantly_leads WHERE email = %s', (ann,)
    ).fetchone()
    binding = store.Binding(lead['organization_id'], w.project, w.connection, lead['workspace_id'])
    now = datetime.now(UTC)
    payload = {
        'event_type': 'reply_received',
        'timestamp': now.isoformat(),
        'workspace': lead['workspace_id'],
        'campaign_id': lead['campaign_id'],
        'lead_email': ann,
        'step': 1,
        'reply_subject': 'Re: Ciao',
        'reply_text': 'Please remove me',
    }

    def the_lead_replies() -> None:
        with acting_for(lead['organization_id'], 'test'), scoped_conn(db)() as conn:
            effects.record_and_apply(conn, binding, [mapping.from_webhook(payload, now=now)])

    instantly.on_get = the_lead_replies
    beat()
    assert [c.split()[0] for c in instantly.calls] == ['GET'] and ann in fake.leads
    stop = db.execute('SELECT status, attempts, keep_lead FROM outreach_stops').fetchone()
    assert stop == {'status': 'pending', 'attempts': 0, 'keep_lead': True}
    instantly.on_get = None
    beat()
    assert (w.rows()[ann]['status'], w.rows()[ann]['code']) == ('failed', 'INSTANTLY.STOP_IN_INSTANTLY')
    assert ann in fake.leads


def test_a_pass_for_stops_alone_keeps_the_read_schedule(db, beat, instantly):
    w = World(db)
    beat()
    later = datetime.now(UTC) + timedelta(hours=1)
    db.execute('UPDATE instantly_sync_state SET next_run_at = %s', (later,))
    before = db.execute('SELECT last_attempt_at, next_run_at FROM instantly_sync_state').fetchone()
    w.stop(w.emails[0])
    reads = Fake()
    assert beat(reads) == {'connections': 1}
    assert w.rows()[w.emails[0]]['status'] == 'confirmed' and reads.calls == []
    after = db.execute('SELECT last_attempt_at, next_run_at, claim_token FROM instantly_sync_state').fetchone()
    assert after == {**before, 'claim_token': None}
    nothing = Fake()
    beat(nothing)
    assert nothing.calls == [] and len(instantly.calls) == 2


def test_an_outage_backs_off_every_stop_of_the_pass_then_fails_them(db, beat, instantly):
    w = World(db)
    w.stop(*w.emails)
    instantly.mode = 'down'
    start = datetime.now(UTC)
    beat()
    assert len(instantly.calls) == 1
    assert {(r['status'], r['attempts'], r['code']) for r in w.rows().values()} == {
        ('pending', 1, 'INSTANTLY.UNKNOWN_OUTCOME')
    }
    for row in db.execute('SELECT next_attempt_at FROM outreach_stops'):
        assert start + timedelta(seconds=55) < row['next_attempt_at'] < start + timedelta(minutes=2)
    db.execute('UPDATE outreach_stops SET attempts = %s', (stop_outbox.MAX_ATTEMPTS - 1,))
    w.due_now()
    beat()
    assert {r['status'] for r in w.rows().values()} == {'failed'}
    assert w.hold(w.emails[0]) == 'stop_failed'


def test_an_outage_backs_off_only_the_stops_that_need_a_call(db, beat, instantly):
    w = World(db)
    ann, bea, cid = w.emails
    w.stop(ann, bea, cid)
    db.execute("UPDATE outreach_stops SET next_attempt_at = now() - interval '1 minute' WHERE email <> %s", (bea,))
    db.execute("UPDATE outreach_stops SET next_attempt_at = now() - interval '1 second' WHERE email = %s", (bea,))
    db.execute("UPDATE instantly_leads SET outcome = 'failed' WHERE email = %s", (bea,))
    instantly.mode = 'down'
    beat()
    rows = w.rows()
    assert (rows[bea]['status'], rows[bea]['confirmed_by']) == ('confirmed', 'not_enrolled')
    assert {(rows[e]['status'], rows[e]['attempts']) for e in (ann, cid)} == {('pending', 1)}


def test_a_key_that_may_not_delete_leads_fails_the_stop_at_once(db, beat, instantly, fake):
    w = World(db)
    w.stop(w.emails[0])
    instantly.mode = 'no_scope'
    beat()
    assert w.rows()[w.emails[0]] == {
        'status': 'failed',
        'attempts': 1,
        'code': 'INSTANTLY.STOP_SCOPE',
        'confirmed_by': None,
    }
    assert w.emails[0] in fake.leads


def test_a_refused_key_fails_every_pending_stop_and_marks_the_connection(db, beat, instantly, fake):
    w = World(db)
    w.stop(*w.emails)
    db.execute("UPDATE outreach_stops SET next_attempt_at = now() + interval '1 hour' WHERE email = %s", (w.emails[2],))
    fake.valid_key = 'another-key'
    beat()
    assert len(instantly.calls) == 1
    assert {(r['status'], r['code']) for r in w.rows().values()} == {('failed', 'INSTANTLY.INVALID_KEY')}
    assert db.execute('SELECT status FROM instantly_connections').fetchone()['status'] == 'error'


def test_the_kill_switch_attempts_nothing_and_keeps_the_hold(db, beat, instantly, monkeypatch):
    w = World(db)
    w.stop(w.emails[0])
    monkeypatch.setattr(reconcile, 'is_disabled', lambda _: True)
    assert beat() == {'paused': True}
    assert instantly.calls == [] and w.rows()[w.emails[0]]['attempts'] == 0
    assert w.hold(w.emails[0]) == 'stop_pending'
    monkeypatch.setattr('app.outreach.stop_routes.is_disabled', lambda _: True)
    assert w.client.get(PATH).json()['paused'] is True


def test_disconnect_fails_the_open_stops_and_a_reconnect_attempts_them_again(db, beat, instantly, fake):
    w = World(db)
    ann, bea = w.emails[:2]
    w.stop(ann)
    assert w.client.delete('/v1/integrations/instantly').status_code == 200
    w.stop(bea)
    assert {e: (r['status'], r['code']) for e, r in w.rows().items()} == {
        ann: ('failed', 'INSTANTLY.DISCONNECTED'),
        bea: ('failed', 'INSTANTLY.DISCONNECTED'),
    }
    stop_id = str(db.execute('SELECT id FROM outreach_stops WHERE email = %s', (ann,)).fetchone()['id'])
    refused = w.client.post(f'{PATH}/{stop_id}/retry')
    assert refused.status_code == 409 and refused.json()['detail']['code'] == 'INSTANTLY.NOT_CONNECTED'
    assert w.hold(ann) == 'stop_failed'
    _connect(w.client)
    assert {(r['status'], r['attempts'], r['code']) for r in w.rows().values()} == {('pending', 0, None)}
    beat()
    assert {r['status'] for r in w.rows().values()} == {'confirmed'}
    assert ann not in fake.leads and bea not in fake.leads


def test_a_stop_on_a_connection_in_error_starts_failed_so_the_user_has_an_exit(db, beat, instantly, fake):
    w = World(db)
    ann, bea = w.emails[:2]
    db.execute("UPDATE instantly_connections SET status = 'error'")
    w.stop(ann, bea)
    assert {(r['status'], r['code']) for r in w.rows().values()} == {('failed', 'INSTANTLY.INVALID_KEY')}
    ids = _stop_ids(db)
    assert w.client.post(f'{PATH}/{ids[ann]}/confirm').json()['status'] == 'confirmed'
    _connect(w.client)
    assert (w.rows()[bea]['status'], w.rows()[bea]['attempts']) == ('pending', 0)
    beat()
    assert w.rows()[bea]['status'] == 'confirmed' and bea not in fake.leads


def test_a_reconnect_to_another_workspace_fails_the_stops_of_the_old_one(db, beat, instantly, fake):
    w = World(db)
    w.stop(w.emails[0])
    fake.workspace = 'ws-2'
    _connect(w.client)
    assert (w.rows()[w.emails[0]]['status'], w.rows()[w.emails[0]]['code']) == ('failed', 'INSTANTLY.WORKSPACE_CHANGED')
    w.stop(w.emails[1])
    assert w.rows()[w.emails[1]]['code'] == 'INSTANTLY.WORKSPACE_CHANGED'
    beat()
    assert instantly.calls == [] and w.emails[0] in fake.leads


def _read(w: World) -> stop_outbox.Due:
    return stop_outbox.due(w.db, w.connection, 10)[0]


def test_an_answer_read_before_a_user_retry_settles_nothing(db, instantly):
    w = World(db)
    w.stop(w.emails[0])
    stale = _read(w)
    stop_outbox.settle(db, stale.stop, Refused('INSTANTLY.STOP_SCOPE'), datetime.now(UTC))
    assert w.client.post(f'{PATH}/{stale.stop.id}/retry').json()['attempts'] == 0
    assert stop_outbox.settle(db, stale.stop, stop_outbox.Stopped(), datetime.now(UTC)) is None
    assert w.rows()[w.emails[0]]['status'] == 'pending'


def _stop_ids(db) -> dict[str, str]:
    return {r['email']: str(r['id']) for r in db.execute('SELECT id, email FROM outreach_stops')}


def test_the_card_lists_open_stops_failed_first_and_retry_and_confirm_leave_failed_only(db, beat, instantly):
    w = World(db)
    ann, bea, cid = w.emails
    w.stop(ann, bea, cid)
    db.execute("UPDATE outreach_stops SET next_attempt_at = now() + interval '1 hour' WHERE email <> %s", (cid,))
    instantly.mode = 'no_scope'
    beat()
    ids = _stop_ids(db)
    listed = w.client.get(PATH).json()
    assert listed['items'][0]['email'] == cid and {i['email'] for i in listed['items'][1:]} == {ann, bea}
    assert (listed['pending'], listed['failed'], listed['paused']) == (2, 1, False)
    first = listed['items'][0]
    assert first['status'] == 'failed' and first['error_code'] == 'INSTANTLY.STOP_SCOPE'
    assert first['next_attempt_at'] is None and first['campaign_url'].startswith('https://app.instantly.ai/')
    pending = w.client.post(f'{PATH}/{ids[ann]}/retry')
    assert pending.status_code == 409 and pending.json()['detail']['code'] == 'INSTANTLY.STOP_NOT_FAILED'
    retried = w.client.post(f'{PATH}/{ids[cid]}/retry').json()
    assert (retried['status'], retried['attempts'], retried['error_code']) == ('pending', 0, None)
    db.execute(
        "UPDATE outreach_stops SET status = 'failed', last_error_code = 'INSTANTLY.STOP_SCOPE' WHERE id = %s",
        (ids[cid],),
    )
    confirmed = w.client.post(f'{PATH}/{ids[cid]}/confirm').json()
    assert confirmed['status'] == 'confirmed' and confirmed['confirmed_at']
    row = db.execute('SELECT confirmed_by, confirmed_user_id::text AS u FROM outreach_stops WHERE id = %s', (ids[cid],))
    assert row.fetchone() == {'confirmed_by': 'user', 'u': w.uid}
    assert w.hold(cid) == 'stopped'
    assert {i['email'] for i in w.client.get(PATH).json()['items']} == {ann, bea}


def test_only_who_connected_or_an_admin_confirms_by_hand_and_another_project_sees_nothing(db, instantly):
    w = World(db, n=1)
    w.stop(w.emails[0])
    stop_id = _stop_ids(db)[w.emails[0]]
    db.execute("UPDATE outreach_stops SET status = 'failed', last_error_code = 'INSTANTLY.STOP_SCOPE'")
    org = member(db, w.uid).org_id
    mate = _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, mate),
    )
    here = {'X-Project-Id': w.project}
    assert [_as(u).get(PATH, headers=here).json()['may_confirm'] for u in (w.uid, mate)] == [True, False]
    teammate = _as(mate)
    denied = teammate.post(f'{PATH}/{stop_id}/confirm', headers=here)
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'INSTANTLY.NOT_ALLOWED'
    other_project = str(
        db.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Q') RETURNING id", (org, w.uid)
        ).fetchone()['id']
    )
    elsewhere = {'X-Project-Id': other_project}
    assert teammate.get(PATH, headers=elsewhere).json() == {
        'items': [],
        'pending': 0,
        'failed': 0,
        'paused': False,
        'may_confirm': False,
    }
    for action in ('retry', 'confirm'):
        missing = teammate.post(f'{PATH}/{stop_id}/{action}', headers=elsewhere)
        assert missing.status_code == 404 and missing.json()['detail']['code'] == 'INSTANTLY.STOP_NOT_FOUND'
    stranger = _as(_user(db))
    assert stranger.get(PATH, headers=here).json()['items'] == []
    assert stranger.post(f'{PATH}/{stop_id}/retry', headers=here).status_code == 404
    assert _as(mate).post(f'{PATH}/{stop_id}/retry', headers=here).json()['status'] == 'pending'


def _member(db, org, role: str) -> str:
    uid = _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, uid, role),
    )
    return uid


def test_after_a_disconnect_only_who_connected_or_an_admin_confirms_by_hand(db, instantly):
    w = World(db)
    ann, bea, _ = w.emails
    w.stop(ann, bea)
    assert w.client.delete('/v1/integrations/instantly').status_code == 200
    ids = _stop_ids(db)
    org = member(db, w.uid).org_id
    here = {'X-Project-Id': w.project}
    mate, admin = _member(db, org, 'member'), _member(db, org, 'admin')
    assert [_as(u).get(PATH, headers=here).json()['may_confirm'] for u in (mate, admin, w.uid)] == [False, True, True]
    denied = _as(mate).post(f'{PATH}/{ids[ann]}/confirm', headers=here)
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'INSTANTLY.NOT_ALLOWED'
    assert _as(admin).post(f'{PATH}/{ids[ann]}/confirm', headers=here).json()['status'] == 'confirmed'
    assert _as(w.uid).post(f'{PATH}/{ids[bea]}/confirm', headers=here).json()['status'] == 'confirmed'
