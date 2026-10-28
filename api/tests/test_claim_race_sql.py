"""The native claim and an Instantly publish of the same person never both win (#931). Needs TEST_DATABASE_URL.

Two connections, as two workers: the judge is parked after it wrote ``publishing`` and before its commit, while the
#937 link beat makes its lead an alias of the person the claim is about to email.
"""

import asyncio
import contextlib
import threading

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core import db as core_db
from app.core import scope as scopes
from app.integrations import instantly_publish
from app.outreach.claims import claim
from tests.conftest import migrated_database, project
from tests.test_dispatcher_sql import _campaign
from tests.test_holds_sql import ANN, lead, org_of, person
from tests.test_instantly_sql import _list
from tests.test_tools_sql import _user


@pytest.fixture
def world():
    with migrated_database() as (c, conninfo):
        yield c, conninfo


def _pending_lead(c, uid, pid, email) -> tuple[dict, list[dict]]:
    _, (row_id,) = _list(c, uid, [{'full_name': 'Ann Smith', 'email': email, 'company_name': 'Acme'}])
    lead_id = lead(c, pid, email, 'pending')
    c.execute('UPDATE instantly_leads SET row_id = %s WHERE id = %s', (row_id, lead_id))
    c.execute(
        'UPDATE instantly_enrollments SET created_by = %s'
        ' WHERE id = (SELECT enrollment_id FROM instantly_leads WHERE id = %s)',
        (uid, lead_id),
    )
    enrollment = c.execute(
        'SELECT e.* FROM instantly_enrollments e JOIN instantly_leads l ON l.enrollment_id = e.id WHERE l.id = %s',
        (lead_id,),
    ).fetchone()
    chunk = c.execute(
        'SELECT l.id, l.row_id, l.email, r.cells, r.excluded FROM instantly_leads l JOIN list_rows r ON r.id = l.row_id'
        ' WHERE l.id = %s',
        (lead_id,),
    ).fetchall()
    return enrollment, chunk


def _target(c, email) -> dict:
    return c.execute(
        'SELECT t.*, c.project_id FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
        ' WHERE t.email = %s',
        (email,),
    ).fetchone()


def _claim_as_the_beat(conninfo, org, target) -> bool:
    with (
        scopes.acting_for(org, 'campaign beat'),
        psycopg.connect(conninfo, row_factory=dict_row) as conn,
        conn.transaction(),
        core_db.entered(conn, scopes.current()),
    ):
        return claim(conn, target, 1, 'approved', recipient=target['email'])


def test_a_publish_in_flight_makes_the_claim_skip_and_then_refuse(world, monkeypatch):
    c, conninfo = world
    uid = _user(c)
    pid = project(c, uid)
    org = org_of(c, pid)
    _campaign(c, uid, emails=('ann@acme.test', 'bea@acme.test'))
    enrollment, chunk = _pending_lead(c, uid, pid, 'a.smith@acme.test')
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
        with scopes.acting_for(org, 'instantly enrollment'):
            going.extend(asyncio.run(instantly_publish._judge_chunk(enrollment, chunk)))

    worker = threading.Thread(target=judge)
    worker.start()
    try:
        assert parked.wait(10), 'the judge never held the project lock and the publishing mark in one transaction'
        person(c, pid, *ANN)
        assert _claim_as_the_beat(conninfo, org, _target(c, 'ann@acme.test')) is False
    finally:
        release.set()
        worker.join(10)
    assert [r['email'] for r in going] == ['a.smith@acme.test']
    assert c.execute('SELECT outcome FROM instantly_leads').fetchone()['outcome'] == 'publishing'
    assert _claim_as_the_beat(conninfo, org, _target(c, 'ann@acme.test')) is False
    assert _claim_as_the_beat(conninfo, org, _target(c, 'bea@acme.test')) is True
    sends = c.execute('SELECT recipient FROM outreach_campaign_sends').fetchall()
    assert [s['recipient'] for s in sends] == ['bea@acme.test']


def test_a_reply_after_the_due_read_stops_the_claim(world):
    c, conninfo = world
    uid = _user(c)
    _campaign(c, uid, emails=('ann@acme.test',))
    due = _target(c, 'ann@acme.test')
    c.execute("UPDATE outreach_campaign_targets SET status = 'replied', replied_at = now()")
    assert _claim_as_the_beat(conninfo, org_of(c, due['project_id']), due) is False
    assert c.execute('SELECT count(*) AS n FROM outreach_campaign_sends').fetchone()['n'] == 0
