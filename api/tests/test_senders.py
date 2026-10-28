"""Senders page (#89): DNS grading with lookups faked, and the listing/pause routes against real SQL."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.migrate import MIGRATIONS
from app.integrations import sender_state, senders
from app.main import app
from tests.conftest import member, scoped_conn, sign_in
from tests.test_dispatcher_sql import _campaign, _mailbox, db  # noqa: F401 — `db` is a fixture
from tests.test_tools_sql import _user


@pytest.fixture
def dns(monkeypatch):
    """``name -> TXT records``; a name mapped to ``None`` fails the lookup, a missing one has no record."""
    zone: dict[str, list[str] | None] = {}

    async def txt(name):
        lookups.append(name)
        return zone.get(name, [])

    monkeypatch.setattr(senders, '_txt', txt)
    monkeypatch.setattr(senders, '_cache', {})
    lookups.clear()
    return zone


lookups: list[str] = []
ALWAYS_OPEN = {'send_window_start_hour': 0, 'send_window_end_hour': 24, 'send_window_days': [0, 1, 2, 3, 4, 5, 6]}


async def test_google_domain_passes_through_a_nested_spf_include(dns):
    dns['acme.test'] = ['google-site-verification=x', 'v=spf1 include:spf.acme.test ~all']
    dns['spf.acme.test'] = ['v=spf1 include:_spf.google.com -all']
    dns['google._domainkey.acme.test'] = ['v=DKIM1; k=rsa; p=MIIBIjANBg']
    dns['_dmarc.acme.test'] = ['v=DMARC1; p=none']
    assert await senders.check_domain('acme.test', 'google') == {'spf': 'pass', 'dkim': 'pass', 'dmarc': 'pass'}


async def test_microsoft_selector2_counts_and_wrong_spf_warns(dns):
    dns['acme.test'] = ['v=spf1 include:_spf.google.com -all']
    dns['selector2._domainkey.acme.test'] = ['v=DKIM1; p=MIGfMA0']
    assert await senders.check_domain('acme.test', 'microsoft') == {'spf': 'warn', 'dkim': 'pass', 'dmarc': 'fail'}


async def test_missing_records_fail_failed_lookups_are_unknown_and_not_cached(dns):
    dns['google._domainkey.acme.test'] = ['v=DKIM1; p=']  # revoked key
    assert await senders.check_domain('acme.test', 'google') == {'spf': 'fail', 'dkim': 'fail', 'dmarc': 'fail'}
    assert await senders.check_domain('acme.test', 'google')
    assert len(lookups) == 3  # second load came from the cache

    dns['other.test'] = None
    dns['_dmarc.other.test'] = None
    grades = await senders.check_domain('other.test', 'google')
    assert grades['spf'] == 'unknown' and grades['dmarc'] == 'unknown'
    await senders.check_domain('other.test', 'google')
    assert len(lookups) == 9


async def test_custom_selector_and_consumer_domains(dns):
    dns['s1._domainkey.acme.test'] = ['p=MIIB']
    assert (await senders.check_domain('acme.test', 'google', 's1'))['dkim'] == 'pass'
    assert set((await senders.check_domain('gmail.com', 'google')).values()) == {'managed'}
    assert senders.sender_domain('Me@Acme.TEST') == 'acme.test'
    assert senders.sender_domain('not an address') is None


async def test_spf_chain_stops_at_ten_lookups_counting_the_root(dns):
    for i in range(20):
        dns[f'{i}.chain.test'] = [f'v=spf1 include:{i + 1}.chain.test -all']
    dns['acme.test'] = ['v=spf1 include:0.chain.test -all']
    assert (await senders.check_domain('acme.test', 'google'))['spf'] == 'warn'
    assert len([n for n in lookups if n.endswith('chain.test') or n == 'acme.test']) == 10


async def test_a_slow_check_is_unknown_and_the_rest_is_kept(dns, monkeypatch):
    async def slow_spf(domain, want):
        await asyncio.sleep(1)
        return 'pass'

    monkeypatch.setattr(senders, '_spf', slow_spf)
    monkeypatch.setattr(senders, '_CHECK_SECONDS', 0.05)
    dns['_dmarc.acme.test'] = ['v=DMARC1; p=reject']
    assert await senders.check_domain('acme.test', 'google') == {'spf': 'unknown', 'dkim': 'fail', 'dmarc': 'pass'}


@pytest.fixture
def client(db, dns, monkeypatch):  # noqa: F811 — the imported fixture
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(senders, 'get_conn', scoped_conn(db))
    uid = _user(db)
    sign_in(app, member(db, uid))
    yield TestClient(app), uid
    app.dependency_overrides.clear()


def test_list_and_pause_are_scoped_to_the_caller(client, db):  # noqa: F811
    http, uid = client
    other = _user(db)
    mine = _mailbox(db, uid, created_at=datetime.now(UTC) - timedelta(days=3), **ALWAYS_OPEN)
    _mailbox(db, other)
    db.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, step, channel, status, recipient,
               idempotency_key, sender_provider, sender_id, attempted_at, delivered_at)
           VALUES (%s, %s, 1, 'email', 'sent', 'x@y.test', 'k1', 'google', %s, now(), now())""",
        (_campaign(db, uid), uid, mine),
    )

    (sender,) = http.get('/v1/integrations/senders').json()['senders']
    assert sender['account_label'] == 'me@acme.test' and sender['paused'] is False
    assert sender['id'] == mine
    assert sender['sent_today'] == 1 and sender['daily_cap'] == 10 and sender['warming_up'] is True  # week one's ramp
    assert (sender['state'], sender['reason'], sender['resumes_at']) == ('ready', None, None)
    assert sender['domain'] == 'acme.test' and sender['dns']['spf'] == 'fail'
    lookups.clear()
    (quick,) = http.get('/v1/integrations/senders?dns=false').json()['senders']  # the campaign builder (#217)
    assert quick['dns'] is None and quick['daily_cap'] == 10 and quick['warming_up'] is True and not lookups

    assert http.patch(f'/v1/integrations/senders/{mine}', json={'paused': True}).json() == {'id': mine, 'paused': True}
    paused = db.execute('SELECT user_id, paused FROM user_oauth_connections').fetchall()
    assert {str(r['user_id']): r['paused'] for r in paused} == {uid: True, other: False}
    (held,) = http.get('/v1/integrations/senders?dns=false').json()['senders']
    assert (held['state'], held['reason']) == ('held', 'paused')
    assert http.patch(f'/v1/integrations/senders/{uuid4()}', json={'paused': True}).status_code == 404


def test_window_is_per_mailbox_and_validated(client, db):  # noqa: F811
    http, uid = client
    other = _user(db)
    mine = _mailbox(db, uid)
    theirs = _mailbox(db, other)
    (sender,) = http.get('/v1/integrations/senders?dns=false').json()['senders']
    assert (sender['send_window_start_hour'], sender['send_window_end_hour']) == (9, 18)
    assert sender['send_window_days'] == [0, 1, 2, 3, 4] and sender['send_timezone'] == 'Europe/Rome'

    body = {'send_window_start_hour': 8, 'send_window_end_hour': 20, 'send_window_days': [4, 0, 0, 5]}
    put = http.put(f'/v1/integrations/senders/{mine}/window', json=body)
    assert put.status_code == 200 and put.json()['send_window_days'] == [0, 4, 5]
    rows = db.execute(
        'SELECT user_id::text, send_window_start_hour, send_window_end_hour, send_window_days FROM user_oauth_connections'
    ).fetchall()
    assert {
        r['user_id']: (r['send_window_start_hour'], r['send_window_end_hour'], r['send_window_days']) for r in rows
    } == {
        uid: (8, 20, [0, 4, 5]),
        other: (9, 18, [0, 1, 2, 3, 4]),
    }
    for bad in (
        {**body, 'send_window_start_hour': 20},
        {**body, 'send_window_end_hour': 25},
        {**body, 'send_window_days': []},
        {**body, 'send_window_days': [7]},
    ):
        assert http.put(f'/v1/integrations/senders/{mine}/window', json=bad).status_code == 422
    assert http.put(f'/v1/integrations/senders/{theirs}/window', json=body).status_code == 404  # another team's
    assert http.put(f'/v1/integrations/senders/{uuid4()}/window', json=body).status_code == 404


def test_each_account_is_paused_and_given_hours_by_its_id(client, db):  # noqa: F811
    http, uid = client
    google = _mailbox(db, uid, **ALWAYS_OPEN)
    microsoft = _mailbox(db, uid, provider='microsoft', account_label='me@acme.test', **ALWAYS_OPEN)
    unlinked = _mailbox(db, uid, provider='unipile')
    db.execute('DELETE FROM project_senders WHERE sender_id = %s', (unlinked,))
    assert http.patch(f'/v1/integrations/senders/{microsoft}', json={'paused': True}).status_code == 200
    assert http.patch(f'/v1/integrations/senders/{unlinked}', json={'paused': True}).status_code == 404
    states = {s['id']: s['reason'] for s in http.get('/v1/integrations/senders?dns=false').json()['senders']}
    assert states == {google: None, microsoft: 'paused'}
    body = {'send_window_start_hour': 7, 'send_window_end_hour': 12, 'send_window_days': [2]}
    assert http.put(f'/v1/integrations/senders/{google}/window', json=body).status_code == 200
    rows = db.execute('SELECT id::text, paused, send_window_start_hour AS start FROM user_oauth_connections').fetchall()
    assert {r['id']: (r['paused'], r['start']) for r in rows} == {
        google: (False, 7),
        microsoft: (True, 0),
        unlinked: (False, 9),
    }


def test_the_list_keeps_its_order_when_an_account_is_paused(client, request):
    http, uid = client
    conn = request.getfixturevalue('db')
    ids = [_mailbox(conn, uid, account_label=f'{name}@acme.test', **ALWAYS_OPEN) for name in ('c', 'a', 'b')]
    ids.append(_mailbox(conn, uid, account_label='a@acme.test', age_days=60, **ALWAYS_OPEN))

    def listed() -> list[str]:
        return [s['id'] for s in http.get('/v1/integrations/senders?dns=false').json()['senders']]

    before = listed()
    assert before == [ids[3], ids[1], ids[2], ids[0]]
    assert http.patch(f'/v1/integrations/senders/{ids[3]}', json={'paused': True}).status_code == 200
    assert listed() == before


def test_the_list_says_why_an_account_cannot_send(client, db):  # noqa: F811
    http, uid = client
    broken = _mailbox(db, uid, status='error')
    rested = _mailbox(db, uid, provider='microsoft', last_used_at=datetime.now(UTC) + timedelta(hours=1))
    by_id = {s['id']: s for s in http.get('/v1/integrations/senders?dns=false').json()['senders']}
    assert (by_id[broken]['state'], by_id[broken]['reason']) == ('held', 'needs_reconnect')
    assert (by_id[rested]['state'], by_id[rested]['reason']) == ('held', 'throttled')
    assert datetime.fromisoformat(by_id[rested]['resumes_at']) > datetime.now(UTC) + timedelta(minutes=68)


def test_the_email_channel_switch_holds_every_listed_account(client, db, monkeypatch):  # noqa: F811
    http, uid = client
    ready = _mailbox(db, uid, **ALWAYS_OPEN)
    other = _mailbox(db, uid, provider='microsoft', **ALWAYS_OPEN)
    monkeypatch.setattr(sender_state, 'is_disabled', lambda key: key == 'channel_disabled:email')
    listed = http.get('/v1/integrations/senders?dns=false').json()['senders']
    assert {s['id']: (s['state'], s['reason']) for s in listed} == {
        ready: ('held', 'channel_disabled'),
        other: ('held', 'channel_disabled'),
    }


def test_the_kill_switches_are_read_before_the_list_holds_a_connection(client, db, monkeypatch):  # noqa: F811
    http, uid = client
    _mailbox(db, uid, **ALWAYS_OPEN)
    held, real = [], senders.get_conn

    @contextmanager
    def tracked():
        held.append(True)
        try:
            with real() as conn:
                yield conn
        finally:
            held.pop()

    seen = []
    monkeypatch.setattr(senders, 'get_conn', tracked)
    monkeypatch.setattr(sender_state, 'is_disabled', lambda key: seen.append(bool(held)) or False)
    http.get('/v1/integrations/senders?dns=false')
    assert seen and not any(seen)


def test_migration_gives_existing_mailboxes_the_global_window(client, db):  # noqa: F811
    _, uid = client
    _mailbox(db, uid)
    db.execute(
        'ALTER TABLE user_oauth_connections DROP COLUMN send_window_start_hour, DROP COLUMN send_window_end_hour,'
        ' DROP COLUMN send_window_days'
    )
    db.execute((MIGRATIONS / '20261008174101_mailbox_send_window.sql').read_text())
    row = db.execute(
        'SELECT send_window_start_hour, send_window_end_hour, send_window_days FROM user_oauth_connections'
    ).fetchone()
    assert (row['send_window_start_hour'], row['send_window_end_hour'], row['send_window_days']) == (
        9,
        18,
        [0, 1, 2, 3, 4],
    )
