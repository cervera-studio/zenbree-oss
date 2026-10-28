import contextlib
import os
import shutil
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.core import db as core_db
from app.core import scope as scopes
from app.core.config import settings
from app.core.migrate import MIGRATIONS, migrate
from app.main import app
from app.outreach import (
    coordination,
    dispatcher,
    field_routes,
    pool_routes,
    routes,
    sender_slot,
    test_email_routes,
    variant_routes,
)
from app.outreach.claims import claim
from app.outreach.coordination import Person, Signal, SignalKind
from app.outreach.drafts import enroll_rows
from app.outreach.freeze import freezing, frozen_values
from app.outreach.reply_targets import TARGET_COLUMNS
from app.outreach.routes import SendIdsIn
from app.scheduling.runner import enrollment_context
from tests.conftest import (
    call_as,
    each_org,
    empty_database,
    member,
    migrated_database,
    pool_on,
    project,
    scoped_conn,
    sign_in,
)
from tests.test_campaign_fields_sql import NEWS, STEP, _list
from tests.test_campaign_routes_sql import _mailbox
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: freeze SQL test NOT run')

MIGRATION = '20261019124500_target_personalization.sql'
BODY = 'Hi {{firstName}}, {{recentNews}}'
ANNA = {'first_name': 'Anna', 'email': 'anna@a.test', 'recent_news': 'a new office in [Milan]'}
BO = {'first_name': 'Bo', 'email': 'bo@a.test'}
CY = {'first_name': 'Cy', 'email': 'cy@a.test', 'recent_news': 'a funding round'}


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (routes, field_routes, dispatcher, pool_routes, test_email_routes, variant_routes):
                monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
            monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
            monkeypatch.setattr('app.notifications.service.get_conn', scoped_conn(c))
            monkeypatch.setattr(test_email_routes, 'is_disabled', lambda key: False)
            monkeypatch.setattr(sys.modules[__name__], '_DB', [c])
            yield c, conninfo
        finally:
            app.dependency_overrides.clear()


_DB: list = []


def _as(uid: str) -> TestClient:
    sign_in(app, member(_DB[0], uid))
    return TestClient(app)


def _activate(c, uid, rows, *, body=BODY, status=200) -> tuple[TestClient, str]:
    _mailbox(c, uid)
    client = _as(uid)
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(c, uid, rows)}).json()['id']
    res = client.patch(f'/v1/campaigns/{cid}', json={'fields': [NEWS], 'steps': [STEP | {'body': body}]})
    assert res.status_code == 200, res.text
    res = client.post(f'/v1/campaigns/{cid}/activate')
    assert res.status_code == status, res.text
    return client, cid


def _people(c) -> dict[str, dict]:
    return {t['first_name']: t for t in c.execute('SELECT * FROM outreach_campaign_targets')}


def _cell(c, name: str, news: str | None) -> None:
    change = "cells - 'recent_news'" if news is None else "cells || jsonb_build_object('recent_news', %(news)s::text)"
    c.execute(
        f"UPDATE list_rows SET cells = {change} WHERE cells ->> 'first_name' = %(name)s", {'news': news, 'name': name}
    )


def _plan(c) -> None:
    each_org(c, dispatcher.plan, datetime.now(UTC) + timedelta(minutes=1))


def _sends(c) -> list[tuple[str, str, str]]:
    rows = c.execute('SELECT recipient, status, body FROM outreach_campaign_sends ORDER BY recipient')
    return [(r['recipient'], r['status'], r['body']) for r in rows]


def test_activation_freezes_the_ready_people_and_holds_back_one_with_no_value(db):
    c, _ = db
    uid = _user(c)
    client, cid = _activate(c, uid, [ANNA, BO])

    anna, bo = _people(c)['Anna'], _people(c)['Bo']
    assert (anna['status'], bo['status']) == ('active', 'draft')
    row = c.execute("SELECT id::text FROM list_rows WHERE cells ->> 'first_name' = 'Anna'").fetchone()['id']
    snapshot = anna['personalization']
    assert (snapshot['values']['recentNews'], snapshot['values']['firstName'], snapshot['missing']) == (
        'a new office in Milan',
        'Anna',
        [],
    )
    assert snapshot['sources'] == {
        'recentNews': {'kind': 'column', 'key': 'recent_news', 'row_id': row, 'url': None, 'title': None}
    }
    assert bo['personalization']['missing'] == ['recentNews']
    assert anna['frozen_at'] is not None and bo['frozen_at'] is not None
    shown = {t['first_name']: t for t in client.get(f'/v1/campaigns/{cid}/targets').json()['items']}
    assert (shown['Anna']['status'], shown['Anna']['missing']) == ('active', [])
    assert (shown['Bo']['status'], shown['Bo']['missing']) == ('draft', ['recentNews'])


def test_activation_with_nobody_ready_changes_nothing(db):
    c, _ = db
    uid = _user(c)
    client, cid = _activate(c, uid, [BO, BO | {'first_name': 'Di', 'email': 'di@a.test'}], status=422)

    detail = client.post(f'/v1/campaigns/{cid}/activate').json()['detail']
    assert (detail['code'], detail['waiting'], detail['missing']) == ('CAMPAIGN.NO_TARGETS', 2, ['recentNews'])
    assert c.execute('SELECT status FROM outreach_campaigns').fetchone()['status'] == 'draft'
    bo = _people(c)['Bo']
    assert (bo['status'], bo['personalization'], bo['frozen_at']) == ('draft', None, None)
    regenerate = client.post(f'/v1/campaigns/{cid}/targets/regenerate')
    assert (regenerate.status_code, regenerate.json()['detail']['code']) == (409, 'CAMPAIGN.INVALID_TRANSITION')


def test_only_freeze_makes_a_draft_person_active(db):
    c, _ = db
    _activate(c, _user(c), [ANNA, BO])

    assert (_people(c)['Anna']['status'], _people(c)['Bo']['status']) == ('active', 'draft')
    with pytest.raises(psycopg.errors.CheckViolation, match='becomes active only through'):
        c.execute("UPDATE outreach_campaign_targets SET status = 'active' WHERE first_name = 'Bo'")
    assert _people(c)['Bo']['status'] == 'draft'


def test_a_copy_variant_is_set_only_by_the_freeze_that_activates_the_person(db):
    c, _ = db
    _activate(c, _user(c), [ANNA, BO])

    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match='variant <NULL> is final'):
        c.execute("UPDATE outreach_campaign_targets SET variant = 'b' WHERE first_name = 'Anna'")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match='variant <NULL> is final'), freezing(c):
        c.execute("UPDATE outreach_campaign_targets SET variant = 'b' WHERE first_name = 'Bo'")
    with freezing(c):
        c.execute("UPDATE outreach_campaign_targets SET variant = 'b', status = 'active' WHERE first_name = 'Bo'")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match='variant b is final'), freezing(c):
        c.execute("UPDATE outreach_campaign_targets SET variant = 'a' WHERE first_name = 'Bo'")
    assert (_people(c)['Anna']['variant'], _people(c)['Bo']['variant']) == (None, 'b')


def test_an_edit_after_activation_changes_nothing_until_a_regenerate(db):
    c, _ = db
    uid = _user(c)
    client, cid = _activate(c, uid, [ANNA])
    _cell(c, 'Anna', 'a second office')

    _plan(c)
    assert _sends(c) == [('anna@a.test', 'awaiting_approval', 'Hi Anna, a new office in Milan')]

    res = client.post(f'/v1/campaigns/{cid}/targets/regenerate')
    assert res.json() == {'active': 0, 'renewed': 1, 'waiting': 0, 'kept': 0}
    assert _sends(c) == []
    _plan(c)
    assert _sends(c) == [('anna@a.test', 'awaiting_approval', 'Hi Anna, a second office')]


def test_a_regenerate_starts_a_waiting_person_once_their_value_exists(db):
    c, _ = db
    uid = _user(c)
    client, cid = _activate(c, uid, [ANNA, BO])
    url = f'/v1/campaigns/{cid}/targets/regenerate'

    assert client.post(url).json() == {'active': 0, 'renewed': 1, 'waiting': 1, 'kept': 0}
    _cell(c, 'Bo', 'a fresh round')
    assert client.post(url).json() == {'active': 1, 'renewed': 1, 'waiting': 0, 'kept': 0}

    bo = _people(c)['Bo']
    assert (bo['status'], bo['personalization']['values']['recentNews'], bo['personalization']['missing']) == (
        'active',
        'a fresh round',
        [],
    )
    _plan(c)
    assert [s[2] for s in _sends(c)] == ['Hi Anna, a new office in Milan', 'Hi Bo, a fresh round']


def test_a_regenerate_never_touches_a_taken_claim_nor_a_person_whose_value_is_gone(db):
    c, _ = db
    uid = _user(c)
    client, cid = _activate(c, uid, [ANNA, CY])
    _plan(c)
    c.execute("UPDATE outreach_campaign_sends SET status = 'sending', attempts = 1 WHERE recipient = 'anna@a.test'")
    _cell(c, 'Anna', 'a second office')
    _cell(c, 'Cy', None)
    cy = _people(c)['Cy']

    res = client.post(f'/v1/campaigns/{cid}/targets/regenerate')

    assert res.json() == {'active': 0, 'renewed': 1, 'waiting': 0, 'kept': 1}
    assert _sends(c) == [
        ('anna@a.test', 'sending', 'Hi Anna, a new office in Milan'),
        ('cy@a.test', 'awaiting_approval', 'Hi Cy, a funding round'),
    ]
    assert _people(c)['Anna']['personalization']['values']['recentNews'] == 'a second office'
    kept = _people(c)['Cy']
    assert (kept['status'], kept['personalization'], kept['frozen_at']) == (
        'active',
        cy['personalization'],
        cy['frozen_at'],
    )


def test_a_bracketed_company_name_is_planned_without_its_brackets(db):
    c, _ = db
    uid = _user(c)
    _activate(c, uid, [ANNA | {'company_name': 'Acme [EU]'}], body='Hi {{firstName}} of {{companyName}}')
    _plan(c)
    assert _sends(c) == [('anna@a.test', 'awaiting_approval', 'Hi Anna of Acme EU')]


def test_a_person_who_waits_for_a_value_keeps_the_campaign_open(db):
    c, _ = db
    uid = _user(c)
    _activate(c, uid, [ANNA, BO])
    c.execute("UPDATE outreach_campaign_targets SET next_step = 2 WHERE first_name = 'Anna'")

    assert each_org(c, dispatcher.complete_finished) == 0
    c.execute("DELETE FROM outreach_campaign_targets WHERE first_name = 'Bo'")
    assert each_org(c, dispatcher.complete_finished) == 1


def _claim_as_the_beat(c, uid, target) -> bool:
    with (
        scopes.acting_for(member(c, uid).org_id, 'campaign beat'),
        c.transaction(),
        core_db.entered(c, scopes.current()),
    ):
        return claim(c, target, 1, 'approved', recipient=target['email'])


def test_a_claim_planned_from_values_a_regenerate_replaced_writes_nothing(db):
    c, _ = db
    uid = _user(c)
    _activate(c, uid, [ANNA])
    read = 'SELECT t.*, c.project_id FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
    stale = c.execute(read).fetchone()
    c.execute("UPDATE outreach_campaign_targets SET frozen_at = frozen_at + interval '1 second'")

    assert _claim_as_the_beat(c, uid, stale) is False
    assert _claim_as_the_beat(c, uid, c.execute(read).fetchone()) is True


def test_an_enrollment_into_a_running_campaign_freezes_and_a_person_with_no_value_waits(db):
    c, _ = db
    uid = _user(c)
    _, cid = _activate(c, uid, [ANNA])
    later = _list(c, uid, [CY, {'first_name': 'Dea', 'email': 'dea@a.test'}])
    campaign = c.execute('SELECT * FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()
    rows = c.execute('SELECT id, cells FROM list_rows WHERE list_id = %s', (later,)).fetchall()

    enrolled, _ = enroll_rows(c, campaign, uid, rows, enrollment_context(c, campaign), room=10, status='active')

    assert len(enrolled) == 2
    people = _people(c)
    assert (people['Cy']['status'], people['Cy']['personalization']['values']['recentNews']) == (
        'active',
        'a funding round',
    )
    assert (people['Dea']['status'], people['Dea']['personalization']['missing']) == ('draft', ['recentNews'])


B_COPY = {'subject': 'Due minuti, {{firstName}}?', 'body': 'Ciao {{firstName}}, {{recentNews}}'}


def _ab(c, uid, rows, *, b_percent: int, a_body: str = 'Hi {{firstName}}') -> tuple[TestClient, str]:
    """A test campaign whose copy B alone names {{recentNews}}, activated."""
    _mailbox(c, uid)
    client = _as(uid)
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(c, uid, rows)}).json()['id']
    steps = [STEP | {'body': a_body, 'b': B_COPY}]
    assert client.patch(f'/v1/campaigns/{cid}', json={'fields': [NEWS], 'steps': steps}).status_code == 200
    assert client.put(f'/v1/campaigns/{cid}/allocation', json={'b_percent': b_percent}).status_code == 200
    assert client.post(f'/v1/campaigns/{cid}/activate').status_code == 200
    return client, cid


def _variants(c) -> dict[str, str | None]:
    return {t['first_name']: t['variant'] for t in _people(c).values()}


def test_thirty_people_at_thirty_percent_give_nine_b_and_keep_it_through_a_regenerate(db):
    c, _ = db
    uid = _user(c)
    rows = [{'first_name': f'P{n}', 'email': f'p{n}@a.test', 'recent_news': 'news'} for n in range(30)]
    client, cid = _ab(c, uid, rows, b_percent=30)

    split = _variants(c)
    assert sorted(split.values()) == ['a'] * 21 + ['b'] * 9
    assert client.post(f'/v1/campaigns/{cid}/targets/regenerate').json()['renewed'] == 30
    assert _variants(c) == split
    later = _list(c, uid, [{'first_name': f'Q{n}', 'email': f'q{n}@a.test', 'recent_news': 'n'} for n in range(10)])
    campaign = c.execute('SELECT * FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()
    joined = c.execute('SELECT id, cells FROM list_rows WHERE list_id = %s', (later,)).fetchall()
    enroll_rows(c, campaign, uid, joined, enrollment_context(c, campaign), room=10, status='active')
    assert list(_variants(c).values()).count('b') == 12  # 30% of 40: the count goes on from the campaign's split


def test_a_value_only_copy_b_names_holds_a_joiner_until_the_allocation_pauses(db):
    c, _ = db
    uid = _user(c)
    client, cid = _ab(c, uid, [ANNA, BO], b_percent=50)
    url = f'/v1/campaigns/{cid}'

    bo = _people(c)['Bo']
    assert (bo['status'], bo['variant'], bo['personalization']['missing']) == ('draft', None, ['recentNews'])
    assert client.put(f'{url}/allocation', json={'paused': True}).status_code == 200
    assert client.post(f'{url}/targets/regenerate').json() == {'active': 1, 'renewed': 1, 'waiting': 0, 'kept': 0}
    assert (_people(c)['Bo']['status'], _variants(c)) == ('active', {'Anna': 'b', 'Bo': None})
    client.put(f'{url}/allocation', json={'paused': False})
    client.post(f'{url}/targets/regenerate')
    assert _variants(c) == {'Anna': 'b', 'Bo': None}  # a paused joiner stays outside the test


def test_a_regenerate_needs_only_the_values_of_each_persons_own_copy(db):
    c, _ = db
    uid = _user(c)
    client, cid = _ab(c, uid, [ANNA, CY], b_percent=50)
    url = f'/v1/campaigns/{cid}'
    assert sorted(_variants(c).values()) == ['a', 'b']
    client.put(f'{url}/allocation', json={'paused': True})
    _cell(c, 'Anna', None)
    _cell(c, 'Cy', None)

    assert client.post(f'{url}/targets/regenerate').json() == {'active': 0, 'renewed': 1, 'waiting': 0, 'kept': 1}
    kept = next(t for t in _people(c).values() if t['variant'] == 'b')
    assert kept['personalization']['values']['recentNews']  # B's copy still has its value


def _parking(conninfo, park_when: str | None, parked: threading.Event, release: threading.Event):
    @contextlib.contextmanager
    def get_conn(timeout=None):
        with (
            psycopg.connect(conninfo, row_factory=dict_row) as conn,
            conn.transaction(),
            core_db.entered(conn, scopes.current()),
        ):
            yield conn
            if park_when and conn.execute(park_when).fetchone():
                parked.set()
                release.wait(10)

    return get_conn


def _wait_for_a_lock(c, waiters: int = 1) -> None:
    for _ in range(100):
        waiting = c.execute(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ).fetchone()['n']
        if waiting >= waiters:
            return
        time.sleep(0.05)
    raise AssertionError('the second transaction never waited on the first one')


class _Race:
    def __init__(self, db, monkeypatch, *, first: str):
        c, conninfo = db
        self.c = c
        uid = _user(c)
        self.user = member(c, uid)
        _, self.cid = _activate(c, uid, [ANNA])
        _plan(c)
        c.execute("UPDATE outreach_campaign_sends SET status = 'approved', approved_at = now()")
        self.send = c.execute('SELECT * FROM outreach_campaign_sends').fetchone()
        self.mailbox = c.execute('SELECT * FROM user_oauth_connections').fetchone()
        _cell(c, 'Anna', 'a second office')
        self.parked, self.release = threading.Event(), threading.Event()
        park = {
            'regenerate': 'SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM outreach_campaign_sends)',
            'take': "SELECT 1 FROM outreach_campaign_sends WHERE status = 'sending'",
        }
        for name, module in (('regenerate', field_routes), ('take', sender_slot)):
            when = park[name] if name == first else None
            monkeypatch.setattr(module, 'get_conn', _parking(conninfo, when, self.parked, self.release))
        self.results: dict = {}

    def regenerate(self):
        self.results['regenerate'] = call_as(self.user, field_routes.regenerate, UUID(self.cid), self.user)

    def take(self):
        with scopes.acting_for(self.user.org_id, 'campaign beat'):
            self.results['take'] = sender_slot.take(self.send, self.mailbox, 'google', datetime.now(UTC))

    def run(self, first, second) -> dict:
        threads = [threading.Thread(target=first)]
        threads[0].start()
        try:
            assert self.parked.wait(10), 'the first transaction never reached its commit'
            threads.append(threading.Thread(target=second))
            threads[1].start()
            _wait_for_a_lock(self.c)
        finally:
            self.release.set()
            for t in threads:
                t.join(10)
        return self.results


def test_a_regenerate_first_leaves_the_mailbox_nothing_to_take(db, monkeypatch):
    race = _Race(db, monkeypatch, first='regenerate')
    assert race.run(race.regenerate, race.take) == {
        'regenerate': {'active': 0, 'renewed': 1, 'waiting': 0, 'kept': 0},
        'take': False,
    }
    assert _sends(race.c) == []


def test_a_send_the_mailbox_took_first_survives_the_regenerate(db, monkeypatch):
    race = _Race(db, monkeypatch, first='take')
    assert race.run(race.take, race.regenerate) == {
        'take': True,
        'regenerate': {'active': 0, 'renewed': 1, 'waiting': 0, 'kept': 0},
    }
    assert _sends(race.c) == [('anna@a.test', 'sending', 'Hi Anna, a new office in Milan')]


RENEWED = {'active': 0, 'renewed': 1, 'waiting': 0, 'kept': 0}


class _RunOnceBefore:
    def __init__(self, conn, prefix: str, then):
        self._conn, self._prefix, self._then = conn, prefix, then

    def execute(self, query, *args, **kwargs):
        if self._then and str(query).lstrip().startswith(self._prefix):
            then, self._then = self._then, None
            then()
        return self._conn.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _own_connections(conninfo, wrap=lambda conn: conn):
    @contextlib.contextmanager
    def get_conn(timeout=None):
        with (
            psycopg.connect(conninfo, row_factory=dict_row) as conn,
            conn.transaction(),
            core_db.entered(conn, scopes.current()),
        ):
            yield wrap(conn)

    return get_conn


class _InAThread:
    def __init__(self, c, run):
        self.c, self.result = c, None
        self.thread = threading.Thread(target=self._run, args=(run,))

    def _run(self, run):
        try:
            self.result = run()
        except psycopg.Error as e:
            self.result = type(e).__name__

    def start_and_wait_on_a_lock(self):
        self.thread.start()
        _wait_for_a_lock(self.c)


def test_a_regenerate_and_a_claim_that_skips_a_step_both_commit(db, monkeypatch):
    c, conninfo = db
    uid = _user(c)
    _, cid = _activate(c, uid, [ANNA])
    _cell(c, 'Anna', 'a second office')
    user = member(c, uid)
    anna = _people(c)['Anna']
    monkeypatch.setattr(field_routes, 'get_conn', _own_connections(conninfo))
    regenerate = _InAThread(c, lambda: call_as(user, field_routes.regenerate, UUID(cid), user))

    with (
        scopes.acting_for(user.org_id, 'campaign beat'),
        psycopg.connect(conninfo, row_factory=dict_row) as beat,
        beat.transaction(),
        core_db.entered(beat, scopes.current()),
    ):
        between = _RunOnceBefore(beat, 'INSERT INTO outreach_campaign_sends', regenerate.start_and_wait_on_a_lock)
        claimed = claim(between, anna, 1, 'skipped', recipient='anna@a.test', code='NOT_ACCEPTED')
    regenerate.thread.join(10)

    assert (claimed, regenerate.result) == (True, RENEWED)
    assert _sends(c) == [('anna@a.test', 'skipped', None)]
    assert _people(c)['Anna']['personalization']['values']['recentNews'] == 'a second office'


def test_a_reject_and_a_regenerate_of_the_same_person_both_commit(db, monkeypatch):
    c, conninfo = db
    uid = _user(c)
    _, cid = _activate(c, uid, [ANNA])
    _plan(c)
    send = c.execute('SELECT id, status FROM outreach_campaign_sends').fetchone()
    assert send['status'] == 'awaiting_approval'
    _cell(c, 'Anna', 'a second office')
    user = member(c, uid)
    regenerate = _InAThread(c, lambda: call_as(user, field_routes.regenerate, UUID(cid), user))
    monkeypatch.setattr(field_routes, 'get_conn', _own_connections(conninfo))
    monkeypatch.setattr(
        routes,
        'get_conn',
        _own_connections(
            conninfo,
            lambda conn: _RunOnceBefore(conn, 'UPDATE outreach_campaign_targets', regenerate.start_and_wait_on_a_lock),
        ),
    )

    rejected = call_as(user, routes.reject, UUID(cid), SendIdsIn(send_ids=[send['id']]), user)
    regenerate.thread.join(10)

    assert (rejected, regenerate.result) == ({'rejected': 1}, RENEWED)
    assert c.execute('SELECT status, error_code FROM outreach_campaign_sends').fetchone() == {
        'status': 'skipped',
        'error_code': 'REJECTED',
    }
    anna = _people(c)['Anna']
    assert (anna['next_step'], anna['personalization']['values']['recentNews']) == (2, 'a second office')


def _reply_of(c, conninfo, org_id, wrap=lambda conn: conn, kind=SignalKind.HUMAN_REPLY):
    """Anna's reply as the inbox worker applies it, on a connection of its own: how many rows it stopped or paused."""
    reach = c.execute(
        f'SELECT {TARGET_COLUMNS} FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
    ).fetchall()
    signal = Signal(kind, Person(None, email=ANNA['email']), 'inbound:email:test', datetime.now(UTC), reach)

    def reply() -> int:
        with (
            scopes.acting_for(org_id, 'inbound reply'),
            psycopg.connect(conninfo, row_factory=dict_row) as conn,
            conn.transaction(),
            core_db.entered(conn, scopes.current()),
        ):
            applied = coordination.apply(wrap(conn), signal)
            return len(applied.stopped) + len(applied.paused)

    return reply


@pytest.mark.parametrize('first', ['reject', 'reply'])
def test_a_reject_and_a_reply_of_the_same_person_both_commit(db, monkeypatch, first):
    c, conninfo = db
    uid = _user(c)
    _, cid = _activate(c, uid, [ANNA])
    _plan(c)
    send = c.execute('SELECT id, status FROM outreach_campaign_sends').fetchone()
    assert send['status'] == 'awaiting_approval'
    user = member(c, uid)

    def reject():
        return call_as(user, routes.reject, UUID(cid), SendIdsIn(send_ids=[send['id']]), user)

    if first == 'reject':
        # Between the reject's write of the send and its move of the person, the reply arrives.
        second = _InAThread(c, _reply_of(c, conninfo, user.org_id))
        hook = 'UPDATE outreach_campaign_targets', second.start_and_wait_on_a_lock
        monkeypatch.setattr(routes, 'get_conn', _own_connections(conninfo, lambda conn: _RunOnceBefore(conn, *hook)))
        results = {'reject': _InAThread(c, reject), 'reply': second}
        results['reject'].thread.start()
    else:
        # Between the reply's stop of the person and its skip of the send, the reject arrives.
        monkeypatch.setattr(routes, 'get_conn', _own_connections(conninfo))
        second = _InAThread(c, reject)
        hook = 'UPDATE outreach_campaign_sends', second.start_and_wait_on_a_lock
        reply = _InAThread(c, _reply_of(c, conninfo, user.org_id, lambda conn: _RunOnceBefore(conn, *hook)))
        results = {'reply': reply, 'reject': second}
        reply.thread.start()
    for side in results.values():  # the first side starts the second, so it is joined first
        side.thread.join(10)

    rejected = 1 if first == 'reject' else 0
    assert {k: v.result for k, v in results.items()} == {'reject': {'rejected': rejected}, 'reply': 1}
    code = 'REJECTED' if first == 'reject' else 'REPLIED'
    assert c.execute('SELECT status, error_code FROM outreach_campaign_sends').fetchone() == {
        'status': 'skipped',
        'error_code': code,
    }
    anna = _people(c)['Anna']
    assert (anna['status'], anna['next_step']) == ('replied', 1 + rejected)


def _seq_scan(conn):
    conn.execute('SET LOCAL enable_indexscan = off')
    conn.execute('SET LOCAL enable_bitmapscan = off')
    return conn


@pytest.mark.parametrize(('kind', 'status'), [(SignalKind.HUMAN_REPLY, 'replied'), (SignalKind.AUTO_REPLY, 'paused')])
def test_a_reply_waits_for_a_reject_of_both_rows_of_one_person(db, monkeypatch, kind, status):
    c, conninfo = db
    uid = _user(c)
    _, cid = _activate(c, uid, [ANNA, ANNA | {'email': 'anna@b.test'}])
    _plan(c)
    sends = [r['id'] for r in c.execute("SELECT id FROM outreach_campaign_sends WHERE status = 'awaiting_approval'")]
    assert len(sends) == 2
    first, last = (r['id'] for r in c.execute('SELECT id FROM outreach_campaign_targets ORDER BY id'))
    for _ in range(5):
        heap = [r['id'] for r in c.execute('SELECT id FROM outreach_campaign_targets ORDER BY ctid')]
        if heap == [last, first]:
            break
        c.execute('UPDATE outreach_campaign_targets SET status = status WHERE id = %s', (first,))
    assert heap == [last, first]
    user = member(c, uid)
    monkeypatch.setattr(routes, 'get_conn', _own_connections(conninfo))
    reject = _InAThread(c, lambda: call_as(user, routes.reject, UUID(cid), SendIdsIn(send_ids=sends), user))
    reply = _InAThread(c, _reply_of(c, conninfo, user.org_id, _seq_scan, kind))

    with psycopg.connect(conninfo) as blocker, blocker.transaction():
        locked = blocker.execute(
            'SELECT 1 FROM outreach_campaign_targets WHERE id = %s FOR NO KEY UPDATE', (first,)
        ).fetchall()
        assert locked == [(1,)]
        reject.start_and_wait_on_a_lock()
        reply.thread.start()
        _wait_for_a_lock(c, waiters=2)
    for side in (reject, reply):
        side.thread.join(10)

    assert (reject.result, reply.result) == ({'rejected': 2}, 2)
    assert [(r['status'], r['error_code']) for r in c.execute('SELECT * FROM outreach_campaign_sends')] == [
        ('skipped', 'REJECTED')
    ] * 2
    assert [(r['status'], r['next_step']) for r in c.execute('SELECT * FROM outreach_campaign_targets')] == [
        (status, 2)
    ] * 2


@pytest.fixture
def before(tmp_path):
    for f in MIGRATIONS.glob('*.sql'):
        if f.name < MIGRATION:
            shutil.copy(f, tmp_path / f.name)
    with empty_database() as c:
        assert migrate(c, tmp_path)
        shutil.copy(MIGRATIONS / MIGRATION, tmp_path / MIGRATION)
        yield c, tmp_path


def test_people_in_a_sequence_before_the_migration_render_every_contact_alias_from_their_columns(before):
    c, directory = before
    uid = _user(c)
    cid = c.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps)'
        " VALUES (default_organization(%s), %s, %s, 'C', 'active', '[]') RETURNING id",
        (uid, project(c, uid), uid),
    ).fetchone()['id']
    people = {
        'active': (' Anna\n', 'De  Luca', 'Acme [EU]', '{{CEO}}', ' anna@a.test '),
        'paused': ('Bo', '', 'Beta\tLtd', 'Head of {Sales}', None),
        'draft': ('Cy', '', 'Gamma', '', 'cy@a.test'),
        'replied': ('Dea', '', 'Delta', '', 'dea@a.test'),
    }
    for status, (first, last, company, role, email) in people.items():
        c.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, status, first_name, last_name, company_name,'
            ' role, email, linkedin_url, paused_until) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (
                cid,
                uid,
                status,
                first,
                last,
                company,
                role,
                email,
                f'https://www.linkedin.com/in/{first.strip().lower()}',
                '2026-11-01' if status == 'paused' else None,
            ),
        )

    assert migrate(c, directory) == [MIGRATION]

    after = {t['status']: t for t in c.execute('SELECT * FROM outreach_campaign_targets')}
    assert {(t['personalization'], t['frozen_at']) for t in after.values()} == {(None, None)}
    assert frozen_values(after['active']) == {
        'firstName': 'Anna',
        'first_name': 'Anna',
        'lastName': 'De Luca',
        'last_name': 'De Luca',
        'companyName': 'Acme EU',
        'company_name': 'Acme EU',
        'company': 'Acme EU',
        'role': 'CEO',
        'title': 'CEO',
        'email': 'anna@a.test',
    }
    assert frozen_values(after['paused']) == {
        'firstName': 'Bo',
        'first_name': 'Bo',
        'lastName': '',
        'last_name': '',
        'companyName': 'Beta Ltd',
        'company_name': 'Beta Ltd',
        'company': 'Beta Ltd',
        'role': 'Head of Sales',
        'title': 'Head of Sales',
        'email': '',
    }
