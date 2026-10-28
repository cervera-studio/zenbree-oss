"""Stops (#931) against answers that come late: a key that a reconnect replaced, and a beat that runs out of time."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.core.errors import err
from app.integrations.instantly_sync import stops, store
from app.outreach import stop_outbox
from app.outreach.stop_outbox import Refused
from tests.conftest import call_for
from tests.test_instantly_sql import _confirm, _connect, _list, _person, _run
from tests.test_instantly_sql import db as db
from tests.test_instantly_sql import fake as fake
from tests.test_instantly_stops_sql import PATH, World, _read
from tests.test_instantly_stops_sql import beat as beat
from tests.test_instantly_stops_sql import instantly as instantly
from tests.test_instantly_sync_sql import Fake


def _current(w: World) -> tuple[store.Binding, datetime]:
    c = w.db.execute(
        'SELECT organization_id::text, project_id::text, id::text, workspace_id, connected_at FROM instantly_connections'
    ).fetchone()
    binding = store.Binding(
        organization_id=c['organization_id'],
        project_id=c['project_id'],
        connection_id=c['id'],
        workspace_id=c['workspace_id'],
    )
    return binding, c['connected_at']


def _status(w: World) -> str:
    return w.db.execute('SELECT status FROM instantly_connections').fetchone()['status']


@pytest.mark.parametrize('disconnect_first', [False, True])
def test_a_drain_on_the_key_before_a_reconnect_fails_no_stop_and_keeps_the_new_connection(
    db, instantly, disconnect_first
):
    w = World(db, n=1)
    w.stop(w.emails[0])
    binding, connected_at = _current(w)
    if disconnect_first:
        w.client.delete('/v1/integrations/instantly')
    _connect(w.client)
    assert _current(w)[1] > connected_at, 'the reconnect moves connected_at'
    drain = stops.drain(binding, 'the-old-revoked-key', connected_at, stops.deadline())
    call_for(binding.organization_id, asyncio.run, drain)
    assert instantly.calls, 'the old key was asked and refused'
    assert w.rows()[w.emails[0]] == {'status': 'pending', 'attempts': 0, 'code': None, 'confirmed_by': None}
    assert _status(w) == 'connected'


def test_an_answer_to_the_old_key_read_before_a_direct_reconnect_settles_nothing(db, instantly):
    w = World(db, n=1)
    w.stop(w.emails[0])
    stale = _read(w)
    _connect(w.client)
    assert stop_outbox.settle(db, stale.stop, Refused('INSTANTLY.STOP_SCOPE'), datetime.now(UTC)) is None
    assert w.rows()[w.emails[0]]['status'] == 'pending'


class KeyRefused(Fake):
    async def list_emails(self, key, *, email_type, since, cursor):
        self.calls.append(f'emails:{email_type}:{cursor}')
        return err('INSTANTLY.INVALID_KEY')


def test_a_key_the_read_pass_finds_refused_fails_the_stops_that_wait_so_the_user_may_confirm_them(db, beat, instantly):
    w = World(db)
    w.stop(*w.emails)
    db.execute("UPDATE outreach_stops SET attempts = 1, next_attempt_at = now() + interval '1 hour'")
    reads = KeyRefused()
    beat(reads)
    assert 'emails:sent:None' in reads.calls and instantly.calls == []
    assert {(r['status'], r['code']) for r in w.rows().values()} == {('failed', 'INSTANTLY.INVALID_KEY')}
    assert _status(w) == 'error'
    stop_id = w.client.get(PATH).json()['items'][0]['id']
    assert w.client.post(f'{PATH}/{stop_id}/confirm').json()['status'] == 'confirmed'


def _reconnected_while_asked(db, http) -> None:
    """Instantly refuses the key of a campaign read after a reconnect gave the connection a new key meanwhile."""
    answer = http.handler

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith('/api/v2/campaigns'):
            db.execute('UPDATE instantly_connections SET connected_at = now()')
            return httpx.Response(401, json={})
        return answer(request)

    http.handler = handle


def test_a_campaign_read_refused_after_a_reconnect_fails_no_stop(db, instantly, http):
    w = World(db, n=1)
    w.stop(w.emails[0])
    _reconnected_while_asked(db, http)
    refused = w.client.get('/v1/integrations/instantly/campaigns')
    assert refused.json()['detail']['code'] == 'INSTANTLY.INVALID_KEY'
    assert w.rows()[w.emails[0]]['status'] == 'pending'
    assert _status(w) == 'connected'


def test_a_batch_refused_after_a_reconnect_fails_no_stop(db, instantly, http):
    w = World(db, n=1)
    w.stop(w.emails[0])
    lid, ids = _list(db, w.uid, [_person(i) for i in range(1, 3)])
    batch = _confirm(w.client, lid, ids).json()['id']
    _reconnected_while_asked(db, http)
    _run(db, w.uid, batch)
    assert db.execute('SELECT error_code FROM instantly_enrollments WHERE id = %s', (batch,)).fetchone() == {
        'error_code': 'INSTANTLY.INVALID_KEY'
    }
    assert w.rows()[w.emails[0]]['status'] == 'pending'
    assert _status(w) == 'connected'


def _second_connection(w: World, fake, n: int) -> list[str]:
    """Another project of the same organization, connected to the same workspace, with ``n`` held leads of its own
    campaign; returns their emails."""
    a = w.db.execute('SELECT * FROM instantly_connections').fetchone()
    project_b = w.db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'B') RETURNING id",
        (a['organization_id'], w.uid),
    ).fetchone()['id']
    connection_b = w.db.execute(
        'INSERT INTO instantly_connections (organization_id, project_id, connected_by, name, api_key_encrypted,'
        " workspace_id) VALUES (%s, %s, %s, 'B', %s, %s) RETURNING id",
        (a['organization_id'], project_b, w.uid, a['api_key_encrypted'], a['workspace_id']),
    ).fetchone()['id']
    enrollment_b = w.db.execute(
        'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, workspace_id, campaign_id,'
        " status) VALUES (%s, %s, %s, %s, 'campaign-b', 'completed') RETURNING id",
        (a['organization_id'], project_b, connection_b, a['workspace_id']),
    ).fetchone()['id']
    emails = [f'b{i}@other.test' for i in range(n)]
    for email in emails:
        fake.leads[email] = str(uuid4())
        w.db.execute(
            'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email,'
            " outcome, instantly_lead_id) VALUES (%s, %s, %s, %s, 'campaign-b', %s, 'accepted', %s)",
            (a['organization_id'], project_b, enrollment_b, a['workspace_id'], email, fake.leads[email]),
        )
    stop_outbox.request(w.db, project_id=str(project_b), emails=emails, reason='opt_out', cause='inbound:test')
    return emails


def test_a_stop_backlog_in_one_connection_leaves_the_beat_time_for_the_next(
    db, beat, instantly, fake, http, monkeypatch
):
    w = World(db)
    w.stop(*w.emails)
    _second_connection(w, fake, 3)
    clock = [0.0]
    answer = instantly.handler

    def slow(request):
        clock[0] += 5.0
        return answer(request)

    http.handler = slow
    monkeypatch.setattr(stops, '_clock', lambda: clock[0])
    beat()
    attempts = db.execute('SELECT sum(attempts) AS n FROM outreach_stops').fetchone()['n']
    assert clock[0] <= stops.DRAIN_SECONDS + 10.0, 'no attempt starts after the budget of the beat'
    assert attempts == 2
    passes = db.execute('SELECT last_attempt_at FROM instantly_sync_state').fetchall()
    assert len(passes) == 2 and all(p['last_attempt_at'] for p in passes), 'both connections read'
