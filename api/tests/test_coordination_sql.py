import asyncio
import contextlib
import threading
import time
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.core import db as core_db
from app.core import scope as scopes
from app.core.system_reasons import SystemReason
from app.integrations import instantly_publish
from app.main import app
from app.outreach import away, coordination, holds, replies
from app.outreach.coordination import Person, Signal, SignalKind
from tests.conftest import call_system, conninfo_of, each_org, member, project, scoped_conn
from tests.test_claim_race_sql import _pending_lead
from tests.test_holds_sql import ANN, lead, org_of, person
from tests.test_replies_sql import (
    DMARC_PASS,
    _campaign,
    _post,
    fake_redis,  # noqa: F401  # pytest fixture, requested by name
)
from tests.test_replies_sql import db as db
from tests.test_tools_sql import _user

ANN_EMAIL = 'ann@acme.test'


class World:
    def __init__(self, c):
        self.c = c
        self.a = _user(c)
        self.org = member(c, self.a).org_id
        self.p = project(c, self.a)
        self.q = str(
            c.execute(
                "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Q') RETURNING id",
                (self.org, self.a),
            ).fetchone()['id']
        )
        self.b = _user(c)
        c.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (self.org, self.b, 'member'),
        )
        self.z = _user(c)
        self.zp = project(c, self.z)
        person(c, self.p, *ANN)
        self.t = {
            'a_p': _campaign(c, self.a, email=ANN_EMAIL, client=self.p)['target'],
            'a_q': self._mailed_before_p(_campaign(c, self.a, email=ANN_EMAIL, client=self.q)),
            'b_alias': _campaign(c, self.b, email='a.smith@acme.test', client=self.p)['target'],
            'b_linkedin': _campaign(
                c, self.b, email='', linkedin_url='https://www.linkedin.com/in/ann/', client=self.p
            )['target'],
            'b_q': _campaign(c, self.b, email=ANN_EMAIL, client=self.q)['target'],
            'b_other': _campaign(c, self.b, email='bea@acme.test', client=self.p)['target'],
            'z': _campaign(c, self.z, email=ANN_EMAIL)['target'],
        }
        self.leads = {
            'p_alias': lead(c, self.p, 'a.smith@acme.test'),
            'p_other': lead(c, self.p, 'bea@acme.test'),
            'q': lead(c, self.q, ANN_EMAIL),
        }
        c.execute("UPDATE instantly_leads SET campaign_id = 'camp-q' WHERE id = %s", (self.leads['q'],))
        self.leads['z'] = lead(c, self.zp, ANN_EMAIL)

    def _mailed_before_p(self, camp: dict) -> str:
        self.c.execute(
            "UPDATE outreach_campaign_sends SET delivered_at = now() - interval '2 days' WHERE campaign_id = %s",
            (camp['campaign'],),
        )
        return camp['target']

    def statuses(self) -> dict[str, str]:
        rows = self.c.execute('SELECT id, status FROM outreach_campaign_targets').fetchall()
        by_id = {r['id']: r['status'] for r in rows}
        return {name: by_id[tid] for name, tid in self.t.items() if tid in by_id}

    def stops(self) -> dict[str, tuple[str, str]]:
        rows = self.c.execute('SELECT lead_id::text, status, reason FROM outreach_stops').fetchall()
        named = {v: k for k, v in self.leads.items()}
        return {named[r['lead_id']]: (r['status'], r['reason']) for r in rows}


@pytest.fixture
def world(db):
    return World(db)


STOPPED_BY_A_REPLY = {'a_p', 'a_q', 'b_alias', 'b_linkedin'}


def _expect(stopped: set[str], status: str, rest: str = 'active') -> dict[str, str]:
    names = ('a_p', 'a_q', 'b_alias', 'b_linkedin', 'b_q', 'b_other', 'z')
    return {n: status if n in stopped else rest for n in names}


def test_a_human_reply_stops_every_owners_campaign_of_the_person_in_the_project(world):
    res = _post(TestClient(app), world.a, sender=f'Ann <{ANN_EMAIL}>', email_id='r1')
    assert res.json()['kind'] == 'reply'
    assert world.statuses() == _expect(STOPPED_BY_A_REPLY, 'replied')
    assert world.stops() == {'p_alias': ('pending', 'human_reply'), 'q': ('pending', 'human_reply')}
    assert holds.hold(world.c, world.p, 'linkedin_connect', '', 'https://linkedin.com/in/ann') == 'stop_pending'


def test_an_opt_out_by_reply_leaves_replied_and_queues_the_stops_as_system(world):
    res = _post(TestClient(app), world.a, sender=ANN_EMAIL, text='No grazie', authentication=DMARC_PASS)
    assert res.json()['kind'] == 'unsubscribe'
    assert world.statuses() == _expect(STOPPED_BY_A_REPLY, 'replied')
    assert world.stops() == {'p_alias': ('pending', 'opt_out'), 'q': ('pending', 'opt_out')}
    excluded = world.c.execute('SELECT DISTINCT project_id::text AS p FROM exclusions WHERE value = %s', (ANN_EMAIL,))
    assert {r['p'] for r in excluded} == {world.p, world.q}


def test_the_unsubscribe_link_keeps_its_reach_and_holds_the_aliases_through_the_stops(world, fake_redis):  # noqa: F811  # pytest fixture, requested by name
    assert call_system(SystemReason.OPT_OUT, replies.unsubscribe, world.a, ANN_EMAIL) == 'unsubscribed'
    assert world.statuses() == _expect({'a_p', 'a_q'}, 'excluded')
    assert world.stops() == {'p_alias': ('pending', 'opt_out'), 'q': ('pending', 'opt_out')}
    assert holds.hold(world.c, world.p, 'email', 'a.smith@acme.test', None) == 'stop_pending'


def test_an_autoresponder_pauses_the_same_people_and_never_stops_or_revives_a_reply(world):
    client = TestClient(app)
    auto = {'sender': ANN_EMAIL, 'subject': 'Fuori ufficio', 'text': 'Torno lunedì'}
    assert _post(client, world.a, email_id='ooo1', **auto).json()['kind'] == 'auto_reply'
    assert world.statuses() == _expect(STOPPED_BY_A_REPLY, 'paused')
    assert world.stops() == {}
    _post(client, world.a, sender=ANN_EMAIL, email_id='r1')
    _post(client, world.a, email_id='ooo2', **auto)
    assert world.statuses() == _expect(STOPPED_BY_A_REPLY, 'replied')
    assert each_org(world.c, holds.resume_paused, datetime.now(UTC) + timedelta(days=60)) == 0
    assert world.statuses() == _expect(STOPPED_BY_A_REPLY, 'replied')


def _signal(world, kind: SignalKind, at: datetime) -> Signal:
    reach = world.c.execute(
        'SELECT t.id, t.campaign_id, t.owner_id, t.email, t.linkedin_url, c.project_id, c.organization_id'
        ' FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id WHERE t.id = %s',
        (world.t['a_p'],),
    ).fetchall()
    return Signal(kind, Person(world.p, email=ANN_EMAIL), 'inbound:email:test', at, reach)


@pytest.mark.parametrize('kind', [SignalKind.HUMAN_REPLY, SignalKind.AUTO_REPLY])
def test_a_replayed_signal_writes_the_same_rows(world, kind):
    signal = _signal(world, kind, datetime(2026, 10, 15, 8, tzinfo=UTC))
    fake = scoped_conn(world.c)
    with scopes.acting_for(world.org, 'test'):
        with fake() as conn:
            first = coordination.apply(conn, signal)
        before = world.c.execute(
            'SELECT id, status, paused_until FROM outreach_campaign_targets ORDER BY id'
        ).fetchall()
        with fake() as conn:
            again = coordination.apply(conn, signal)
    after = world.c.execute('SELECT id, status, paused_until FROM outreach_campaign_targets ORDER BY id').fetchall()
    assert before == after and again.stopped == [] and again.stops_requested == 0
    if kind is SignalKind.AUTO_REPLY:
        assert len(first.paused) == len(again.paused) == 3
        until = {r['paused_until'] for r in after if r['status'] == 'paused'}
        assert until == {away.unknown_return_pause_end(signal.at)}
    else:
        assert first.stops_requested == 1


def test_a_reply_waits_for_an_instantly_publish_in_flight_and_then_stops_the_published_lead(world, monkeypatch):
    c, conninfo = world.c, conninfo_of(world.c)
    c.execute(
        'DELETE FROM outreach_campaigns WHERE id = (SELECT campaign_id FROM outreach_campaign_targets WHERE id = %s)',
        (world.t['b_alias'],),
    )
    c.execute('DELETE FROM instantly_leads')
    c.execute('DELETE FROM audience_identities')
    enrollment, chunk = _pending_lead(c, world.a, world.p, 'a.smith@acme.test')
    published_lead = str(chunk[0]['id'])
    parked, release = threading.Event(), threading.Event()

    @contextlib.contextmanager
    def parking_conn(timeout=None):
        with (
            psycopg.connect(conninfo, row_factory=dict_row) as conn,
            conn.transaction(),
            core_db.entered(conn, scopes.current()),
        ):
            yield conn
            if conn.execute("SELECT 1 FROM instantly_leads WHERE outcome = 'publishing'").fetchone():
                parked.set()
                release.wait(10)

    monkeypatch.setattr(instantly_publish, 'get_conn', parking_conn)

    going: list = []

    def judge():
        with scopes.acting_for(world.org, 'instantly enrollment'):
            going.extend(asyncio.run(instantly_publish._judge_chunk(enrollment, chunk)))

    reply_backend: list[int] = []
    applied: list = []

    def reply():
        with (
            scopes.acting_for(world.org, 'inbound reply'),
            psycopg.connect(conninfo, row_factory=dict_row) as conn,
            conn.transaction(),
            core_db.entered(conn, scopes.current()),
        ):
            reply_backend.append(conn.info.backend_pid)
            applied.append(coordination.apply(conn, _signal(world, SignalKind.HUMAN_REPLY, datetime.now(UTC))))

    judging, replying = threading.Thread(target=judge), threading.Thread(target=reply)
    judging.start()
    try:
        assert parked.wait(10), 'the judge never held the project lock and the publishing mark in one transaction'
        person(c, world.p, *ANN)
        replying.start()
        assert _waits_on_a_lock(c, reply_backend), 'the reply did not wait for the enrollment in flight'
    finally:
        release.set()
        judging.join(10)
        if replying.is_alive():
            replying.join(10)
    assert [r['email'] for r in going] == ['a.smith@acme.test']
    assert applied and applied[0].stops_requested == 1
    stop = c.execute('SELECT lead_id::text, status FROM outreach_stops').fetchone()
    assert stop == {'lead_id': published_lead, 'status': 'pending'}


@pytest.mark.parametrize('deleted', ['target', 'campaign'])
def test_a_reply_waiting_for_an_activation_is_filed_after_its_target_is_deleted(world, monkeypatch, deleted):
    target, conninfo = world.t['a_p'], conninfo_of(world.c)
    campaign = world.c.execute('SELECT campaign_id FROM outreach_campaign_targets WHERE id = %s', (target,))
    campaign = campaign.fetchone()['campaign_id']

    @contextlib.contextmanager
    def own_conn(timeout=None):
        with (
            psycopg.connect(conninfo, row_factory=dict_row) as conn,
            conn.transaction(),
            core_db.entered(conn, scopes.current()),
        ):
            yield conn

    monkeypatch.setattr(replies, 'get_conn', own_conn)
    answered: list = []
    with psycopg.connect(conninfo, row_factory=dict_row) as activation:
        activation.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (world.p,))
        replying = threading.Thread(
            target=lambda: answered.append(_post(TestClient(app), world.a, sender=ANN_EMAIL, email_id='r-race'))
        )
        replying.start()
        try:
            assert _waits_on_a_lock(world.c, _other_backends(world.c, activation)), 'the reply never waited'
            activation.execute("SET LOCAL lock_timeout = '500ms'")
            if deleted == 'target':
                activation.execute('DELETE FROM outreach_campaign_sends WHERE target_id = %s', (target,))
                activation.execute('DELETE FROM outreach_campaign_targets WHERE id = %s', (target,))
            else:
                activation.execute('DELETE FROM outreach_campaigns WHERE id = %s', (campaign,))
            activation.commit()
        finally:
            activation.rollback()
            replying.join(10)
    assert [r.json()['status'] for r in answered] == ['accepted'], 'the reply was lost'
    filed = world.c.execute(
        "SELECT target_id, campaign_id FROM outreach_inbound_messages WHERE provider_message_id = 'r-race'"
    )
    # As main files it when the insert lands first and ON DELETE SET NULL clears what the activation deleted.
    assert filed.fetchone() == {'target_id': None, 'campaign_id': campaign if deleted == 'target' else None}
    assert world.statuses() == {n: s for n, s in _expect(STOPPED_BY_A_REPLY, 'replied').items() if n != 'a_p'}


def _other_backends(c, activation) -> list[int]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = c.execute(
            "SELECT pid FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()"
            ' AND pid NOT IN (%s, %s)',
            (c.info.backend_pid, activation.info.backend_pid),
        ).fetchall()
        if rows:
            return [rows[0]['pid']]
        time.sleep(0.05)
    return []


def _waits_on_a_lock(c, backend: list[int]) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if backend:
            row = c.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s', (backend[0],)).fetchone()
            if row and row['wait_event_type'] == 'Lock':
                return True
        time.sleep(0.05)
    return False


def test_another_organizations_lead_and_target_are_never_touched(world):
    _post(TestClient(app), world.a, sender=ANN_EMAIL, text='No grazie', authentication=DMARC_PASS)
    assert world.statuses()['z'] == 'active'
    assert 'z' not in world.stops()
    assert org_of(world.c, world.zp) != world.org
