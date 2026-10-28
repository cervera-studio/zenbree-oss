"""Notifications (#173) against real SQL: urgent cap, digest, low credits, search done, the panel API.

Needs TEST_DATABASE_URL; copies a throwaway database from the migrated api/db template. Resend faked.
"""

import json
import os
import uuid
from contextlib import aclosing
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agent.language import save_locale
from app.core.config import settings
from app.core.migrate import MIGRATIONS
from app.main import app
from app.notifications import emails, replay, routes
from app.notifications import service as n
from app.notifications.low_credits import low_credits
from app.notifications.quotes import QUOTE_CHARS
from tests.conftest import (
    act_as,
    act_in_own_org,
    italian_user,
    member,
    migrated_database,
    pool_on,
    project,
    scoped_conn,
    sign_in,
)
from tests.test_tools_sql import _user as _new_user

DB_URL = os.environ.get('TEST_DATABASE_URL')
DAILY = {'frequency': 'daily', 'hour': 9}
OFF = {'frequency': 'off', 'hour': 9}
MONDAY_9 = datetime(2026, 7, 6, 7, 0, tzinfo=UTC)  # 9:00 in Rome in summer
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: notifications SQL test NOT run')


def _user(c) -> str:
    return italian_user(c, _new_user)


@pytest.fixture
def db(monkeypatch, http):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (routes, n):
                monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
            monkeypatch.setattr(settings, 'resend_api_key', 're_test')
            monkeypatch.setattr(settings, 'email_from', 'Zenbree <noreply@zenbree.test>')
            http.handler = lambda r: httpx.Response(200, json={'id': 'mail'})
            yield c
        finally:
            app.dependency_overrides.clear()


def _notify(c, *args, **kwargs) -> str:
    """notify + deliver after the (autocommit) write, as every caller does."""
    kwargs.setdefault('org_id', _org(c, args[0]))  # every notification names its organization
    notified = n.notify(c, *args, **kwargs)
    n.deliver(notified)
    return notified.id


def _changed(c, *uids) -> int:
    """The low-credit rule of on_balance_changed for each user's own organization (#306: its admin), delivered
    after the (autocommit) write."""
    due = [notified for uid in uids for notified in low_credits(c, _org(c, uid))]
    for notified in due:
        n.deliver(notified)
    return len(due)


def _org(c, uid) -> str:
    return str(c.execute('SELECT default_organization(%s) AS id', (uid,)).fetchone()['id'])


def _note(c, nid: str) -> dict:
    return c.execute('SELECT * FROM notifications WHERE id = %s', (nid,)).fetchone()


def _mails(http) -> list[dict]:
    return [json.loads(r.content) for r in http.calls]


def test_baseline_upgrades_the_kind_check_of_an_existing_table(db):
    db.execute('ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check')
    db.execute(  # the #173 table: the CHECK CREATE TABLE gave it, by its default name
        "ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check CHECK (kind IN ('reply', 'search_done'))"
    )
    db.execute((MIGRATIONS / '20261005000000_baseline.sql').read_text())  # what adopting prod runs (#268)
    uid = _user(db)
    org = _org(db, uid)
    db.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href) VALUES (%s, %s, 'agent_question', '/')",
        (uid, org),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO notifications (user_id, organization_id, kind, href) VALUES (%s, %s, 'nope', '/')", (uid, org)
        )


def test_urgent_reply_emails_at_most_once_per_30_minutes_then_the_digest(db, http):
    uid = _user(db)
    data = {'name': 'Anna Rossi', 'label': 'interested', 'channel': 'email'}
    first = _notify(db, uid, 'reply_urgent', href='/inbox?m=1', data=data)
    second = _notify(db, uid, 'reply_urgent', href='/inbox?m=2', data=data)
    assert _note(db, first)['emailed_at'] and not _note(db, first)['email_pending']
    assert not _note(db, second)['emailed_at'] and _note(db, second)['email_pending']
    assert len(http.calls) == 1
    db.execute(
        "UPDATE notification_settings SET last_urgent_email_at = now() - interval '31 minutes' WHERE user_id = %s",
        (uid,),
    )
    assert _note(db, _notify(db, uid, 'reply_urgent', href='/inbox?m=3', data=data))['emailed_at']
    other = _user(db)  # the cap is per user
    assert _note(db, _notify(db, other, 'reply_urgent', href='/inbox?m=4', data=data))['emailed_at']


EMAIL_ROWS = [k for k, kind in n.KINDS.items() if kind.pref and 'email' in n.ROW_DEFAULTS[kind.pref]]


def _row(db, uid, kind: str, **choice) -> None:
    events = {r: dict(v) for r, v in n.ROW_DEFAULTS.items()}
    events[n.KINDS[kind].pref].update(choice)
    n.save_prefs(db, uid, events, digest=DAILY, dnd=False)


@pytest.mark.parametrize('kind', list(n.KINDS))
def test_every_kind_sends_its_email_the_way_the_registry_says(db, http, kind):
    k = n.KINDS[kind]
    uid = _user(db)
    if k.pref:
        _row(db, uid, kind, email=True)
    got = n.notify(db, uid, kind, href='/x', data={}, org_id=_org(db, uid))
    pending = _note(db, got.id)['email_pending']
    assert (got.mail is not None, pending) == {
        'urgent': (True, False),
        'now': (True, False),
        'always': (True, False),
        'digest': (False, True),
        'never': (False, False),
    }[k.email]


@pytest.mark.parametrize('kind', EMAIL_ROWS)
def test_an_email_toggle_that_is_off_sends_nothing_and_queues_nothing(db, http, kind):
    uid = _user(db)
    _row(db, uid, kind, email=False)
    got = n.notify(db, uid, kind, href='/x', data={}, org_id=_org(db, uid))
    if n.KINDS[kind].email == 'always':
        assert got.mail is not None  # the row has no email toggle: a saved false is ignored
    else:
        assert got.mail is None and not _note(db, got.id)['email_pending']


@pytest.mark.parametrize('kind', [k for k, v in n.KINDS.items() if v.pref])
def test_the_browser_flag_follows_the_rows_browser_choice(db, kind):
    uid = _user(db)
    _row(db, uid, kind, browser=True)
    assert n.notify(db, uid, kind, href='/x', data={}, org_id=_org(db, uid)).item['browser'] is n.KINDS[kind].browser
    _row(db, uid, kind, browser=False)
    assert n.notify(db, uid, kind, href='/x', data={}, org_id=_org(db, uid)).item['browser'] is False
    _row(db, uid, kind, browser=True)
    n.save_prefs(db, uid, {r: dict(v) for r, v in n.ROW_DEFAULTS.items()}, digest=DAILY, dnd=True)
    assert n.notify(db, uid, kind, href='/x', data={}, org_id=_org(db, uid)).item['browser'] is False  # do not disturb


@pytest.mark.parametrize('kind', [k for k, v in n.KINDS.items() if not v.browser])
def test_a_kind_without_a_browser_never_alerts_even_with_everything_on(db, kind):
    uid = _user(db)
    n.save_prefs(db, uid, {r: dict.fromkeys(v, True) for r, v in n.ROW_DEFAULTS.items()}, digest=DAILY, dnd=False)
    assert n.notify(db, uid, kind, href='/x', data={}, org_id=_org(db, uid)).item['browser'] is False


def test_dnd_and_settings_route_emails_but_never_the_payment_one(db, http):
    uid = _user(db)
    events = {k: dict.fromkeys(v, False) for k, v in n.ROW_DEFAULTS.items()}
    n.save_prefs(db, uid, events, digest=DAILY, dnd=True)
    assert not _note(db, _notify(db, uid, 'search_done', href='/', data={}))['email_pending']
    paid = _note(db, _notify(db, uid, 'payment_failed', href='/billing'))
    assert paid['emailed_at'] and len(http.calls) == 1  # cannot be turned off, not even by do-not-disturb
    n.save_prefs(db, uid, n.ROW_DEFAULTS, digest=DAILY, dnd=True)
    held = _note(db, _notify(db, uid, 'reply_urgent', href='/inbox?m=1', data={'label': 'interested'}))
    assert held['email_pending'] and len(http.calls) == 1
    n.save_prefs(db, uid, n.ROW_DEFAULTS, digest=OFF, dnd=False)
    reply = _note(db, _notify(db, uid, 'reply', href='/inbox?m=2', data={'label': 'other'}))
    assert not reply['email_pending'] and not reply['emailed_at']  # no digest: the panel only


def test_email_channel_is_inert_without_resend(db, http, monkeypatch):
    monkeypatch.setattr(settings, 'resend_api_key', '')
    uid = _user(db)
    note = _note(db, _notify(db, uid, 'payment_failed', href='/billing'))
    assert note and not note['email_pending'] and not http.calls
    assert n.take_digests(db, MONDAY_9) == []


def test_digest_batches_counts_per_user_on_working_days_at_nine(db, http):
    a, b = _user(db), _user(db)
    for _ in range(2):
        _notify(db, a, 'reply', href='/inbox?m=1', data={'name': 'Anna Rossi', 'label': 'other'})
    _notify(db, b, 'reply', href='/inbox?m=2', data={'name': 'Bruno', 'label': 'not_now'})
    assert not http.calls
    batches = sorted(n.take_digests(db, MONDAY_9), key=lambda d: sum(d.queued.values()))
    assert [d.queued for d in batches] == [{'reply': 1}, {'reply': 2}]
    assert n.take_digests(db, MONDAY_9) == []  # taken once
    assert n.send_digest(batches[1])
    (mail,) = _mails(http)
    assert '2 altre risposte' in mail['text'] and 'Anna' not in mail['text']
    assert mail['subject'] == '2 risposte · lunedì 6 lug'


def test_digest_schedule_daily_on_working_days_weekly_on_monday_never_when_off_at_the_users_hour():
    # 9:00 in Rome is 07:00 UTC in summer, 08:00 UTC in winter.
    monday, friday, saturday = (datetime(2026, 7, d, 7, 0, tzinfo=UTC) for d in (6, 10, 11))
    assert n.digest_due('daily', 9, monday) and n.digest_due('daily', 9, friday)
    assert not n.digest_due('daily', 9, saturday)
    assert not n.digest_due('daily', 9, monday.replace(hour=8))  # 10:00 local
    assert n.digest_due('daily', 9, datetime(2026, 1, 9, 8, 0, tzinfo=UTC))  # Friday, winter
    assert n.digest_due('weekly', 9, monday) and not n.digest_due('weekly', 9, friday)
    assert n.digest_due('daily', 18, monday.replace(hour=16)) and not n.digest_due('daily', 18, monday)
    assert not n.digest_due('off', 9, monday)


def _pending(c, uid, kind='reply', **data) -> str:
    return _notify(c, uid, kind, href='/inbox?m=1', data=data or {'label': 'other'})


def test_take_digests_takes_only_users_due_at_that_hour_and_leaves_the_rest_queued(db):
    daily, weekly, evening, off = (_user(db) for _ in range(4))
    n.save_prefs(db, weekly, n.ROW_DEFAULTS, digest={'frequency': 'weekly', 'hour': 9}, dnd=False)
    n.save_prefs(db, evening, n.ROW_DEFAULTS, digest={'frequency': 'daily', 'hour': 18}, dnd=False)
    for uid in (daily, weekly, evening, off):
        _pending(db, uid)
    n.save_prefs(db, off, n.ROW_DEFAULTS, digest=OFF, dnd=False)  # turned off after it queued
    friday_9 = datetime(2026, 7, 10, 7, 0, tzinfo=UTC)
    assert [d.email for d in n.take_digests(db, friday_9)] == [f'{daily}@x.test']
    assert [d.email for d in n.take_digests(db, MONDAY_9)] == [f'{weekly}@x.test']  # weekly waited for Monday
    assert [d.email for d in n.take_digests(db, MONDAY_9.replace(hour=16))] == [f'{evening}@x.test']
    assert db.execute('SELECT count(*) AS c FROM notifications WHERE email_pending').fetchone()['c'] == 0


def test_turning_the_digest_off_drops_what_queued_and_off_users_queue_nothing(db, http):
    uid = _user(db)
    queued = _pending(db, uid)
    assert _note(db, queued)['email_pending']
    n.save_prefs(db, uid, n.ROW_DEFAULTS, digest=OFF, dnd=False)
    assert not _note(db, queued)['email_pending']  # not sent weeks later, when it is stale
    assert not _note(db, _pending(db, uid))['email_pending']
    assert n.take_digests(db, MONDAY_9) == []


def test_digest_adds_live_approvals_and_each_administered_balance_and_skips_when_nothing_is_left(db, http):
    uid = _user(db)
    _credits(db, uid, 942)
    team_admin = _user(db)
    team = _org(db, team_admin)
    db.execute('UPDATE organization SET name = %s WHERE id = %s', ('Agenzia Bo', team))
    _credits(db, team_admin, 55)
    _join(db, uid, team, role='admin')
    _held(db, uid, 12)
    _pending(db, uid, 'approval_needed', count=3, campaign='Fiere 2026')
    _pending(db, uid, name='Anna', label='other')
    (digest,) = n.take_digests(db, MONDAY_9)
    assert digest.approvals == 12  # live, not the 3 the item said
    own = db.execute('SELECT name FROM organization WHERE id = %s', (_org(db, uid),)).fetchone()['name']
    assert sorted(digest.balances) == sorted([('Agenzia Bo', 55), (own, 942)])
    assert n.send_digest(digest)
    (mail,) = _mails(http)
    assert mail['subject'] == '12 messaggi da approvare e 1 risposta · lunedì 6 lug'
    for line in ('12 messaggi da approvare', '1 altra risposta alle tue campagne', 'Crediti di «Agenzia Bo»: 55'):
        assert line in mail['text']
    assert 'Anna' not in mail['text'] and 'Fiere' not in mail['text']
    # the approvals were handled before the digest left: only an approval_needed item queued, nothing to say
    other = _user(db)
    _pending(db, other, 'approval_needed', count=3, campaign='Fiere 2026')
    assert n.take_digests(db, MONDAY_9) == []
    assert db.execute('SELECT count(*) AS c FROM notifications WHERE email_pending').fetchone()['c'] == 0


def test_the_digest_leaves_out_approvals_when_the_user_turned_that_email_off(db):
    uid = _user(db)
    _held(db, uid, 4)
    _pending(db, uid, name='Anna', label='other')
    events = {r: dict(v) for r, v in n.ROW_DEFAULTS.items()}
    events['approval_needed']['email'] = False
    n.save_prefs(db, uid, events, digest=DAILY, dnd=False)
    (digest,) = n.take_digests(db, MONDAY_9)
    assert digest.approvals == 0
    assert 'approvare' not in emails.digest_subject(digest) and 'approvare' not in emails.digest(digest).text


def test_the_digest_counts_only_approvals_of_organizations_the_user_still_belongs_to(db):
    ada, bo = _user(db), _user(db)
    team = _org(db, ada)
    own_campaign = _held(db, bo, 2)
    _join(db, bo, team)
    left = _held(db, bo, 5, project_id=project(db, ada))
    assert own_campaign != left
    _pending(db, bo, name='Anna', label='other')
    (digest,) = n.take_digests(db, MONDAY_9)
    assert digest.approvals == 7
    db.execute('UPDATE notifications SET email_pending = true')
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, team))
    (digest,) = n.take_digests(db, MONDAY_9)
    assert digest.approvals == 2


class _OtherRunTakesTheQueue:
    def __init__(self, real):
        self.real = real

    def execute(self, sql, params=None):
        if str(sql).lstrip().startswith('UPDATE notifications'):
            self.real.execute('UPDATE notifications SET email_pending = false')
        return self.real.execute(sql, params)


def test_a_digest_whose_queue_another_run_took_is_not_sent_with_only_live_lines(db):
    uid = _user(db)
    _held(db, uid, 3)
    _pending(db, uid, name='Anna', label='other')
    assert n.take_digests(_OtherRunTakesTheQueue(db), MONDAY_9) == []


def _credits(c, uid, balance):
    c.execute(
        """INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (default_organization(%s), %s, %s)
           ON CONFLICT (organization_id) DO UPDATE SET credits_remaining = EXCLUDED.credits_remaining""",
        (uid, uid, balance),
    )


def test_low_credits_once_per_drop_and_never_with_auto_topup(db, http):
    uid, auto = _user(db), _user(db)
    _credits(db, uid, 50)
    assert _changed(db, uid, auto) == 0  # never above the line (signup credits): nothing dropped
    _credits(db, uid, 250)
    _credits(db, auto, 250)
    db.execute(
        'INSERT INTO auto_topups (organization_id, user_id, enabled, pack_id, threshold)'
        " VALUES (default_organization(%s), %s, true, 'starter', 100)",
        (auto, auto),
    )
    assert _changed(db, uid, auto) == 0  # armed
    _credits(db, uid, 150)
    _credits(db, auto, 150)
    assert _changed(db, uid, auto) == 1
    assert _changed(db, uid, auto) == 0  # once per drop, however many changes follow
    (mail,) = _mails(http)
    assert '150 crediti' in mail['text']
    _credits(db, uid, 120)
    assert _changed(db, uid, auto) == 0
    _credits(db, uid, 300)
    _changed(db, uid, auto)  # re-armed
    _credits(db, uid, 10)
    assert _changed(db, uid, auto) == 1
    kinds = db.execute('SELECT user_id::text, kind FROM notifications').fetchall()
    assert kinds == [{'user_id': uid, 'kind': 'low_credits'}] * 2  # the auto top-up user: nothing


def test_search_done_only_when_long_or_zenbree_closed_and_email_off_by_default(db, http, event_redis):
    now = datetime.now(UTC)
    assert n.search_done_due(timedelta(minutes=6), True)
    assert not n.search_done_due(timedelta(minutes=2), True)  # watched: the chat is enough
    assert n.search_done_due(timedelta(minutes=2), False)
    uid = _user(db)
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id, title)'
        " VALUES (default_organization(%s), %s, %s, 'T') RETURNING id::text",
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    found = ('search_done', {'refunded': 0})
    assert n.notify_turn(db, uid, sid, now - timedelta(minutes=1), found)  # no bell stream open: Zenbree closed
    n.mark_open(uid)  # what an open bell stream keeps alive
    assert event_redis.ttl(n.open_key(uid)) == n.OPEN_TTL_SECONDS
    assert n.notify_turn(db, uid, sid, now - timedelta(minutes=1), found) is None
    assert n.notify_turn(db, uid, sid, now - timedelta(minutes=6), None) is None
    done = n.notify_turn(db, uid, sid, now - timedelta(minutes=6), found)
    assert done and _note(db, done.id)['href'] == f'/?s={sid}' and done.mail is None and not http.calls
    n.save_prefs(db, uid, {**n.ROW_DEFAULTS, 'search_done': {'email': True, 'browser': False}}, digest=DAILY, dnd=False)
    n.deliver(n.notify_turn(db, uid, sid, now - timedelta(minutes=6), found))
    (mail,) = _mails(http)
    assert sid in mail['text']


def test_turn_outcome_picks_the_stopped_stage_then_the_question_then_rows():
    ok = {'tool': 'find_emails', 'units': 100, 'done': 64}
    stopped = {**ok, 'error': {'code': 'provider'}}
    assert n.turn_outcome('succeeded', [ok, stopped], None, True, 14) == (
        'search_failed',
        {'done': 64, 'units': 100},
    )
    assert n.turn_outcome('failed', [{**ok, 'error': {'code': 'failed'}}], None, False, 0)[0] == 'search_failed'
    no_credits = {**ok, 'error': {'code': 'no_credits'}}  # the chat's credit card says it, not the bell
    assert n.turn_outcome('succeeded', [no_credits], None, True, 0) == (
        'search_done',
        {'refunded': 0, 'spent': 0, 'undelivered': 0},
    )
    funnel = {'question': '', 'options': [], 'funnel': {'steps': []}}
    assert n.turn_outcome('needs_input', [], funnel, False, 0) == ('agent_question', {'reason': 'funnel'})
    plan = {'question': '', 'options': [], 'plan': {}}
    assert n.turn_outcome('needs_input', [], plan, False, 0) == ('agent_question', {'reason': 'plan'})
    assert n.turn_outcome('succeeded', [], None, True, 14, 298, 3) == (
        'search_done',
        {'refunded': 14, 'spent': 298, 'undelivered': 3},
    )
    assert n.turn_outcome('succeeded', [], None, False, 0) is None


def test_turn_items_carry_the_chat_title_and_list_sizes_and_never_email(db, http):
    uid = _user(db)
    sid = db.execute(
        "INSERT INTO chat_sessions (organization_id, project_id, user_id, title) VALUES (default_organization(%s), %s, %s, 'CEO a Milano') RETURNING id::text",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    lists = {}
    for entity, rows in (('person', 3), ('company', 2)):
        lid = lists[entity] = db.execute(
            'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity) VALUES (default_organization(%s), %s, %s, %s, %s, %s) RETURNING id::text',
            (uid, project(db, uid), uid, sid, f'L {entity}', entity),
        ).fetchone()['id']
        for i in range(rows):
            db.execute(
                "INSERT INTO list_rows (list_id, dedupe_key, cells) VALUES (%s, %s, '{}')", (lid, f'{entity}{i}')
            )
    old = datetime.now(UTC) - timedelta(minutes=6)
    _credits(db, uid, 500)
    done = n.notify_turn(db, uid, sid, old, ('search_done', {'refunded': 4, 'spent': 30, 'undelivered': 1}))
    assert done and done.item['data'] == {
        'refunded': 4,
        'spent': 30,
        'undelivered': 1,
        'chat': 'CEO a Milano',
        'people': 3,
        'companies': 2,
        'list': 'L person',
        'list_id': lists['person'],
        'people_list_id': lists['person'],
        'balance': 500,
    }
    n.save_prefs(db, uid, {k: dict.fromkeys(v, True) for k, v in n.ROW_DEFAULTS.items()}, digest=DAILY, dnd=False)
    ask = n.notify_turn(db, uid, sid, old, ('agent_question', {'reason': None}))  # never an email
    assert ask and ask.mail is None and ask.item['browser'] and ask.item['data']['chat'] == 'CEO a Milano'
    assert not _note(db, ask.id)['email_pending']
    assert n.notify_turn(db, uid, str(uuid.uuid4()), old, ('agent_question', {'reason': None})) is None  # no chat


def test_a_stopped_search_is_an_urgent_email_sharing_the_reply_cap(db, http):
    uid = _user(db)
    sid = db.execute(
        "INSERT INTO chat_sessions (organization_id, project_id, user_id, title) VALUES (default_organization(%s), %s, %s, 'CEO a Milano') RETURNING id::text",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    old = datetime.now(UTC) - timedelta(minutes=6)
    stopped = ('search_failed', {'done': 64, 'units': 100})
    first = n.notify_turn(db, uid, sid, old, stopped)
    assert first and first.item['browser']  # on by default, like the other urgent kind
    n.deliver(first)
    (mail,) = _mails(http)
    assert mail['subject'] == 'Ricerca ferma su Zenbree' and f'next=%2F%3Fs%3D{sid}' in mail['text']
    assert 'CEO a Milano' in mail['text'] and '64 di 100' in mail['text'] and '/settings/notifications' in mail['html']
    # the 30-minute cap is shared with urgent replies: the next one waits for the digest
    reply = _notify(db, uid, 'reply_urgent', href='/inbox?m=1', data={'name': 'Anna', 'label': 'interested'})
    assert _note(db, reply)['email_pending'] and len(http.calls) == 1
    again = n.notify_turn(db, uid, sid, old, stopped)
    assert again.mail is None and _note(db, again.id)['email_pending']
    (digest,) = n.take_digests(db, MONDAY_9)
    assert digest.queued == {'reply_urgent': 1, 'search_failed': 1}
    n.save_prefs(
        db, uid, {**n.ROW_DEFAULTS, 'search_failed': {'email': False, 'browser': False}}, digest=DAILY, dnd=False
    )
    off = n.notify_turn(db, uid, sid, old, stopped)
    assert off.mail is None and not off.item['browser'] and not _note(db, off.id)['email_pending']


def _held(c, uid: str, held: int, activated_ago=timedelta(hours=1), *, project_id=None) -> str:
    """``project_id``: a project of another organization than the owner's own (a team they joined or left)."""
    project_id = project_id or project(c, uid)
    cid = c.execute(
        "INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, activated_at) VALUES ((SELECT organization_id FROM projects WHERE id = %s), %s, %s, 'Fiere 2026', 'active', %s)"
        ' RETURNING id::text',
        (project_id, project_id, uid, datetime.now(UTC) - activated_ago),
    ).fetchone()['id']
    for i in range(held):
        tid = c.execute(
            "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, %s, %s, 'active')"
            ' RETURNING id',
            (cid, uid, f'p{i}@x.test'),
        ).fetchone()['id']
        c.execute(
            'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient,'
            ' idempotency_key)'
            " VALUES (%s, %s, %s, 1, 'email', 'p@x.test', %s)",
            (cid, uid, tid, f'{cid}:{tid}:1'),
        )
    return cid


def test_approvals_once_while_unread_scoped_and_not_while_watching_a_fresh_campaign(db, event_redis):
    uid, other = _user(db), _user(db)
    cid = _held(db, uid, 2)
    assert n.notify_approvals(db, other, cid) is None  # not the owner's campaign
    first = n.notify_approvals(db, uid, cid)
    assert first and first.item['href'] == n.switch_href(first.org_id, f'/campaigns/{cid}')
    assert first.item['data'] == {'count': 2, 'campaign': 'Fiere 2026'} and first.mail is None
    assert n.notify_approvals(db, uid, cid) is None  # the unread item already says it
    db.execute('UPDATE notifications SET read_at = now() WHERE id = %s', (first.id,))
    assert n.notify_approvals(db, uid, cid)  # a later beat holding more, once read
    assert n.notify_approvals(db, uid, _held(db, uid, 0)) is None  # nothing waiting
    n.mark_open(uid)
    assert n.notify_approvals(db, uid, _held(db, uid, 1, activated_ago=timedelta(minutes=1))) is None  # watching


def test_panel_api_is_scoped_and_marks_read(db):
    uid, other = _user(db), _user(db)
    mine = [_notify(db, uid, 'low_credits', href='/billing', data={'credits': i}) for i in range(3)]
    _notify(db, other, 'low_credits', href='/billing', data={'credits': 1})
    client = TestClient(app)
    sign_in(app, member(db, uid))
    res = client.get('/v1/notifications').json()
    assert [i['id'] for i in res['items']] == mine[::-1] and res['unread'] == 3
    assert all(i['browser'] for i in res['items'])  # low credits: browser on by default
    assert res['cursor'] == n.stream_cursor(uid) != '0-0'  # where the bell's stream picks up
    assert client.post('/v1/notifications/read', json={'ids': [mine[0]]}).json() == {'updated': 1}
    assert client.get('/v1/notifications').json()['unread'] == 2
    assert client.post('/v1/notifications/read', json={}).json() == {'updated': 2}
    sign_in(app, member(db, other))
    assert client.get('/v1/notifications').json()['unread'] == 1  # not touched by uid's "mark all"
    assert client.post('/v1/notifications/read', json={'ids': mine}).json() == {'updated': 0}


def test_a_notification_opens_behind_the_switch_of_its_organization(db):
    uid = _user(db)
    org = member(db, uid).org_id
    chat = f'/?s={uuid.uuid4()}'
    note = _notify(db, uid, 'search_done', href=chat, data={'chat': 'C'})
    sign_in(app, member(db, uid))
    [item] = TestClient(app).get('/v1/notifications').json()['items']
    assert item['id'] == note
    assert item['href'] == f'/switch?org={org}&next=%2F%3Fs%3D{chat[4:]}'
    assert n.switch_href(org, '/billing/switch?org=x') == '/billing/switch?org=x'  # billing's own switch stays
    assert db.execute('SELECT href FROM notifications WHERE id = %s', (note,)).fetchone()['href'] == chat


def test_the_email_links_open_the_organization_first(db):
    uid = _user(db)
    org = member(db, uid).org_id
    data = {'name': 'Ada', 'label': 'interested', 'channel': 'email', 'campaign': 'C'}
    got = n.notify(db, uid, 'reply_urgent', href='/inbox?m=1', data=data, org_id=org)
    assert got.mail and f'/switch?org={org}&next=%2Finbox%3Fm%3D1' in got.mail[1].text
    done = n._render('search_done', '/switch', {'list_id': 'L1', 'campaign': 'L1'}, 'it', org)
    assert f'/switch?org={org}&next=%2Flists%2FL1' in done.text


def test_settings_api_defaults_save_and_reject_mandatory(db):
    uid = _user(db)
    client = TestClient(app)
    sign_in(app, member(db, uid))
    got = client.get('/v1/notifications/settings').json()
    assert got == {'events': n.ROW_DEFAULTS, 'digest': DAILY, 'dnd': False}
    weekly = {'frequency': 'weekly', 'hour': 17}
    events = {**n.ROW_DEFAULTS, 'search_done': {'email': True, 'browser': True}}
    saved = client.put('/v1/notifications/settings', json={'events': events, 'digest': weekly, 'dnd': True}).json()
    assert saved == {'events': events, 'digest': weekly, 'dnd': True}
    assert client.get('/v1/notifications/settings').json() == saved
    old_tab = {k: v for k, v in events.items() if k not in ('search_failed', 'linkedin_accepted')}
    events['search_failed'] = {'email': False, 'browser': True}
    assert client.put('/v1/notifications/settings', json={'events': events, 'digest': weekly, 'dnd': True}).is_success
    kept = client.put('/v1/notifications/settings', json={'events': old_tab, 'digest': weekly, 'dnd': True})
    assert kept.status_code == 200 and kept.json()['events'] == events  # its choice is kept, not reset
    bad_events = {**events, 'payment_failed': {'browser': False}}
    bodies = [{'events': bad_events, 'digest': weekly, 'dnd': False}] + [
        {'events': events, 'digest': digest, 'dnd': False}
        for digest in ({'frequency': 'monthly', 'hour': 9}, {'frequency': 'daily', 'hour': 24}, True)
    ]
    assert [client.put('/v1/notifications/settings', json=b).status_code for b in bodies] == [422] * 4


def test_always_rows_take_only_a_browser_choice_and_ignore_a_stale_email_key(db):
    uid = _user(db)
    client = TestClient(app)
    sign_in(app, member(db, uid))
    stale = {'low_credits': {'email': False, 'browser': False}, 'bounces_high': {'email': False, 'browser': True}}
    got = client.put('/v1/notifications/settings', json={'events': stale, 'digest': DAILY, 'dnd': False}).json()
    assert got['events']['low_credits'] == {'browser': False} and got['events']['bounces_high'] == {'browser': True}
    db.execute(
        'UPDATE notification_settings SET events = events || \'{"low_credits": {"email": false, "browser": false}}\''
    )
    assert n.prefs(db, uid)['events']['low_credits'] == {'browser': False}  # a saved email key is dropped on read


def test_low_credit_send_failure_or_timeout_does_not_resend(db, http):
    uid, slow = _user(db), _user(db)
    for u in (uid, slow):
        _credits(db, u, 250)
    _changed(db, uid, slow)
    for u in (uid, slow):
        _credits(db, u, 100)

    def flaky(request):
        if len(http.calls) == 1:
            raise httpx.ReadTimeout('slow', request=request)
        return httpx.Response(500)

    http.handler = flaky
    assert _changed(db, uid, slow) == 2  # both sends fail, neither raises
    assert _changed(db, uid, slow) == 0  # the disarm was committed first: the next change does not resend
    assert db.execute('SELECT count(*) AS c FROM notifications WHERE emailed_at IS NULL').fetchone()['c'] == 2


def test_rate_limited_send_is_retried(db, http, monkeypatch):
    monkeypatch.setattr(n.time, 'sleep', lambda s: None)
    answers = iter([httpx.Response(429), httpx.Response(200, json={'id': 'mail'})])
    http.handler = lambda r: next(answers)
    uid = _user(db)
    _pending(db, uid)
    (digest,) = n.take_digests(db, MONDAY_9)
    assert n.send_digest(digest) and len(http.calls) == 2


def _stream(r, uid: str) -> list[tuple[str, dict]]:
    return [(f[b'event'].decode(), json.loads(f[b'data'])) for _, f in r.xrange(n.stream_key(uid))]


def test_open_tabs_get_new_items_after_the_commit_only(db, event_redis):
    uid, other = _user(db), _user(db)
    notified = n.notify(db, uid, 'low_credits', href='/billing', data={'credits': 120}, org_id=_org(db, uid))
    assert _stream(event_redis, uid) == []  # notify runs in the caller's transaction: nothing pushed yet
    n.deliver(notified)
    ((event, data),) = _stream(event_redis, uid)
    assert event == 'notification' and data['unread'] == 1
    client = TestClient(app)
    sign_in(app, member(db, uid))
    loaded = client.get('/v1/notifications').json()['items'][0]
    pushed = data['item']
    assert datetime.fromisoformat(pushed.pop('created_at')) == datetime.fromisoformat(loaded.pop('created_at'))
    assert pushed == loaded  # the same item the panel loads
    assert _stream(event_redis, other) == []


def test_a_redis_failure_never_stops_the_email(db, http, monkeypatch):
    uid = _user(db)

    def down():
        raise ConnectionError('redis down')

    monkeypatch.setattr(n.events, 'redis_sync', down)
    _notify(db, uid, 'payment_failed', href='/billing')
    assert len(http.calls) == 1
    assert n.stream_cursor(uid) is None and not n.tab_open(uid)
    client = TestClient(app)
    sign_in(app, member(db, uid))
    assert client.post('/v1/notifications/read', json={}).json() == {'updated': 1}  # publish fails, the read stands


def test_mark_read_reaches_the_other_tabs(db, event_redis):
    uid = _user(db)
    ids = [_notify(db, uid, 'low_credits', href='/billing', data={'credits': i}) for i in range(2)]
    client = TestClient(app)
    sign_in(app, member(db, uid))
    client.post('/v1/notifications/read', json={'ids': [ids[0]]})
    client.post('/v1/notifications/read', json={'ids': [ids[0]]})  # nothing changed: nothing pushed
    client.post('/v1/notifications/read', json={})
    assert _stream(event_redis, uid)[2:] == [
        ('read', {'ids': [ids[0]], 'unread': 1}),
        ('read', {'ids': None, 'unread': 0}),
    ]


def test_low_credits_tell_every_admin_of_the_organization_and_no_member(db, http):
    """#306: the wallet is the organization's, so its admins (who can buy credits) hear about it."""
    admin, other_admin, member = _user(db), _user(db), _user(db)
    org = _org(db, admin)
    for uid, role in ((other_admin, 'admin'), (member, 'member')):
        db.execute('DELETE FROM member WHERE "userId" = %s', (uid,))
        db.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (org, uid, role),
        )
    _credits(db, admin, 250)
    assert low_credits(db, org) == []  # armed
    _credits(db, admin, 150)
    for notified in low_credits(db, org):
        n.deliver(notified)
    assert low_credits(db, org) == []
    rows = db.execute("SELECT user_id::text FROM notifications WHERE kind = 'low_credits'").fetchall()
    assert sorted(r['user_id'] for r in rows) == sorted([admin, other_admin])


def _join(c, uid, org, role='member') -> None:
    c.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, uid, role),
    )


def test_a_removed_member_stops_seeing_that_organizations_notifications(db):
    """#323: an item about an organization disappears from the panel, the unread count and the digest once its
    user leaves that organization; their own organization's items and items about none stay."""
    ada, bo = _user(db), _user(db)
    team, own = _org(db, ada), _org(db, bo)
    _join(db, bo, team)
    teams = _notify(db, bo, 'search_done', href='/', data={}, org_id=team)
    owns = _notify(db, bo, 'search_done', href='/', data={}, org_id=own)
    old = _notify(db, bo, 'search_done', href='/', data={})  # from before #323: no organization
    db.execute('UPDATE notifications SET email_pending = true')
    client = TestClient(app)
    sign_in(app, member(db, bo))
    assert {i['id'] for i in client.get('/v1/notifications').json()['items']} == {teams, owns, old}

    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, team))
    res = client.get('/v1/notifications').json()
    assert {i['id'] for i in res['items']} == {owns, old} and res['unread'] == 2
    assert n.unread_count(db, bo) == 2
    assert [d.queued for d in n.take_digests(db, MONDAY_9)] == [{'search_done': 2}]


def test_a_campaign_owner_outside_its_organization_gets_nothing_until_they_join(db, http, event_redis):
    """#412: a campaign keeps its owner after they leave its organization. What it raises then is stored for nobody:
    no row, no push to an open tab, no email. A member of that organization gets both kinds."""
    ada, bo = _user(db), _user(db)
    team = _org(db, ada)
    cid = _held(db, bo, 2, project_id=project(db, ada))
    data = {'name': 'Anna Rossi', 'label': 'interested', 'channel': 'email', 'campaign': 'Fiere 2026'}

    def raised():
        return [
            n.notify(db, bo, 'reply_urgent', href='/inbox?m=1', data=data, org_id=team),
            n.notify_approvals(db, bo, cid),
        ]

    gone = raised()
    for notified in gone:
        n.deliver(notified)
    assert gone == [None, None]
    assert db.execute('SELECT count(*) AS n FROM notifications').fetchone()['n'] == 0
    assert _stream(event_redis, bo) == [] and http.calls == []

    _join(db, bo, team)
    reply, approvals = raised()
    assert reply and reply.mail and approvals and approvals.item['data'] == {'count': 2, 'campaign': 'Fiere 2026'}


def test_low_credits_are_armed_per_organization_and_name_it(db, http):
    """#323: an admin of two organizations hears about each one's drop, and the notice says which and opens its
    billing; one organization's notice never disarms the other's."""
    ada, bo = _user(db), _user(db)
    own, team = _org(db, ada), _org(db, bo)
    db.execute('UPDATE organization SET name = %s WHERE id = %s', ('Agenzia Bo', team))
    _join(db, ada, team, role='admin')
    for uid in (ada, bo):
        _credits(db, uid, 250)
    assert low_credits(db, own) == [] and low_credits(db, team) == []  # both armed
    _credits(db, bo, 150)
    told = low_credits(db, team)
    assert sorted(x.user_id for x in told) == sorted([ada, bo])
    mine = next(x for x in told if x.user_id == ada)
    assert mine.item['href'] == f'/billing/switch?org={team}'
    assert mine.item['data'] == {'credits': 150, 'organization': 'Agenzia Bo'}
    assert mine.mail is not None and 'Agenzia Bo' in mine.mail[1].subject
    _credits(db, ada, 150)
    assert [x.user_id for x in low_credits(db, own)] == [ada]  # Ada's own wallet: still armed
    assert low_credits(db, team) == []  # once per drop


def test_low_credits_carry_the_next_plan_grant_unless_it_is_cancelled(db, http):
    """#351: the email's renewal box reads the organization's live subscription; one set to cancel grants nothing."""
    uid = _user(db)
    org = _org(db, uid)
    renews = datetime(2030, 11, 1, tzinfo=UTC)

    def sub(sid, cancel_at):
        db.execute(
            'INSERT INTO subscriptions (stripe_subscription_id, user_id, organization_id, plan_id, status,'
            " current_period_end, cancel_at, event_created) VALUES (%s, %s, %s, 'starter', 'active', %s, %s, 1)",
            (sid, uid, org, renews, cancel_at),
        )

    def drop() -> dict:
        _credits(db, uid, 250)
        assert low_credits(db, org) == []
        _credits(db, uid, 180)
        (told,) = low_credits(db, org)
        return told.item['data']

    sub('sub_cancelled', renews)  # #538: flexible billing mode sets only cancel_at
    cancelled = drop()  # still the live plan, but no next grant
    assert cancelled['plan'] == 'starter' and 'renews_at' not in cancelled
    sub('sub_live', None)
    data = drop()
    assert data['plan'] == 'starter' and datetime.fromisoformat(data['renews_at']) == renews
    assert 'Piano Starter: 500 crediti il 1 nov' in n._render('low_credits', '/billing', data, 'it').text


def _inbound(c, uid, body: str) -> str:
    return str(
        c.execute(
            """INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id, body)
               VALUES (%s, %s, 'email', 'reply', %s, %s) RETURNING id""",
            (uid, project(c, uid), uuid.uuid4().hex, body),
        ).fetchone()['id']
    )


def test_reply_quote_is_computed_for_the_owner_only_and_never_stored(db, event_redis):
    uid, other = _user(db), _user(db)
    text = 'Interessante,\n\n  ci sentiamo   giovedì? ' + 'x' * 200
    mid = _inbound(db, uid, text)
    nid = _notify(db, uid, 'reply', href=f'/inbox?m={mid}', data={'message_id': mid, 'name': 'Anna'})
    stored = db.execute('SELECT data FROM notifications WHERE id = %s', (nid,)).fetchone()['data']
    assert 'Interessante' not in json.dumps(stored)  # never in data

    client = TestClient(app)
    sign_in(app, member(db, uid))
    (item,) = client.get('/v1/notifications').json()['items']
    assert item['quote'].startswith('Interessante, ci sentiamo giovedì? xxx') and item['quote'].endswith('…')
    assert len(item['quote']) == QUOTE_CHARS + 1
    ((_, frame),) = _stream(event_redis, uid)
    assert frame['item']['quote'] == item['quote']  # the live frame carries the same quote

    # a notification pointing at someone else's message gets none
    foreign = _notify(db, other, 'reply', href=f'/inbox?m={mid}', data={'message_id': mid})
    sign_in(app, member(db, other))
    (theirs,) = client.get('/v1/notifications').json()['items']
    assert theirs['id'] == foreign and 'quote' not in theirs

    db.execute('DELETE FROM outreach_inbound_messages WHERE id = %s', (mid,))
    sign_in(app, member(db, uid))
    (gone,) = client.get('/v1/notifications').json()['items']
    assert 'quote' not in gone


def test_reply_quote_is_only_the_contacts_own_words(db):
    uid = _user(db)
    text = (
        'Ok, giovedì alle 15 va bene.\n\n'
        'Il giorno mar 7 ott 2026 alle ore 10:02 Ale Poli <ale@zenbree.test> ha scritto:\n> Ciao Giulia, ti scrivo'
    )
    mid = _inbound(db, uid, text)
    _notify(db, uid, 'reply', href=f'/inbox?m={mid}', data={'message_id': mid})
    client = TestClient(app)
    sign_in(app, member(db, uid))
    (item,) = client.get('/v1/notifications').json()['items']
    assert item['quote'] == 'Ok, giovedì alle 15 va bene.'


def test_a_notification_without_a_message_has_no_quote(db):
    uid = _user(db)
    _notify(db, uid, 'reply', href='/inbox', data={'name': 'Anna'})
    _notify(db, uid, 'low_credits', href='/billing', data={'credits': 1})
    client = TestClient(app)
    sign_in(app, member(db, uid))
    assert all('quote' not in i for i in client.get('/v1/notifications').json()['items'])


def _locale(c, uid: str) -> str:
    return c.execute('SELECT locale FROM "user" WHERE id = %s', (uid,)).fetchone()['locale']


def test_a_new_user_gets_the_english_email_and_an_italian_one_the_italian(db, http):
    english, italian = _new_user(db), _user(db)
    _notify(db, english, 'payment_failed', href='/billing', data={})
    _notify(db, italian, 'payment_failed', href='/billing', data={})
    en_mail, it_mail = _mails(http)
    assert en_mail['subject'] == 'Automatic top-up failed' and 'Check the payment' in en_mail['text']
    assert it_mail['subject'] == 'Ricarica automatica non riuscita' and 'Controlla il pagamento' in it_mail['text']


def test_save_locale_writes_only_a_change(db):
    uid = _user(db)

    def row_version() -> str:
        return db.execute('SELECT xmin::text AS v FROM "user" WHERE id = %s', (uid,)).fetchone()['v']

    before = row_version()
    save_locale(db, uid, 'it')
    assert row_version() == before
    save_locale(db, uid, 'en')
    assert _locale(db, uid) == 'en' and row_version() != before


def test_the_digest_carries_the_users_language(db, http):
    english, italian = _user(db), _user(db)
    save_locale(db, english, 'en')
    for uid in (english, italian):
        _pending(db, uid, name='Anna', label='other')
    by_email = {d.email: d for d in n.take_digests(db, MONDAY_9)}
    assert by_email[f'{english}@x.test'].locale == 'en' and by_email[f'{italian}@x.test'].locale == 'it'
    assert n.send_digest(by_email[f'{english}@x.test'])
    (mail,) = _mails(http)
    assert mail['subject'] == '1 reply · Mon Jul 6' and '1 other reply to your campaigns' in mail['text']


async def test_opening_the_bell_stream_never_changes_the_saved_language(db):
    uid = _user(db)
    save_locale(db, uid, 'en')
    sign_in(app, member(db, uid))
    # The old `locale` parameter is ignored now (#795): a page load is not a choice.
    act_in_own_org(db, uid)
    await routes.stream(user=member(db, uid), last_event_id='0-0')
    assert _locale(db, uid) == 'en'
    await routes.stream(user=member(db, uid), last_event_id='0-0')
    assert _locale(db, uid) == 'en'


@pytest.mark.parametrize('how', ['removed', 'banned'])
def test_a_digest_covers_only_the_organizations_where_its_user_is_a_live_member(db, how):
    """The digest beat runs as System(DIGESTS), where no policy hides an organization: it names live members itself.
    Ada, removed from the team, gets a digest of her own organization only; banned, she gets none, and both items stay
    queued for when the ban ends. Nothing is sent: the test reads the digests and never calls send_digest."""
    from app.core.scope import system
    from app.core.system_reasons import SystemReason

    ada, bo = _user(db), _user(db)
    team = _org(db, bo)
    _credits(db, ada, 942)
    _credits(db, bo, 55)
    _join(db, ada, team, role='admin')
    own_note = _pending(db, ada)
    team_note = _notify(db, ada, 'reply', href='/inbox?m=2', data={'label': 'other'}, org_id=team)
    if how == 'removed':
        db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (ada, team))
    else:
        db.execute('UPDATE "user" SET banned = true WHERE id = %s', (ada,))

    with system(SystemReason.DIGESTS), n.get_conn() as conn:  # the task's scope and checkout
        digests = n.take_digests(conn, MONDAY_9)

    if how == 'removed':
        (digest,) = digests
        own = db.execute('SELECT name FROM organization WHERE id = %s', (_org(db, ada),)).fetchone()['name']
        assert (digest.queued, digest.balances) == ({'reply': 1}, [(own, 942)])
        assert (_note(db, own_note)['email_pending'], _note(db, team_note)['email_pending']) == (False, True)
    else:
        assert digests == []
        assert _note(db, own_note)['email_pending'] and _note(db, team_note)['email_pending']


async def test_replay_rereads_each_item_so_a_left_organization_and_an_erased_reply_leave_no_trace(
    db, event_redis, monkeypatch
):
    """#975: the Redis frame is a copy made while the user was a member; a replay from 0-0 shows what the panel shows."""
    monkeypatch.setattr(replay, 'get_conn', scoped_conn(db))
    ada, bo = _user(db), _user(db)
    team, own = _org(db, ada), _org(db, bo)
    _join(db, bo, team)
    secret = _inbound(db, ada, 'DUMMY_TEAM_PRIVATE_REPLY')
    mine = _inbound(db, bo, 'DUMMY_OWN_REPLY')
    act_as(team, bo)
    gone = _notify(db, bo, 'reply', href='/inbox', data={'message_id': secret}, org_id=team)
    act_as(own, bo)
    kept = _notify(db, bo, 'reply', href='/inbox', data={'message_id': mine}, org_id=own)
    db.execute('DELETE FROM outreach_inbound_messages WHERE id = %s', (mine,))
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, team))
    n.publish(bo, 'read', {'ids': None, 'unread': 0})  # the end of the replay

    resp = await routes.stream(user=member(db, bo), last_event_id='0-0')
    frames = []
    async with aclosing(resp.body_iterator) as it:
        async for chunk in it:
            frames.append(chunk)
            if 'event: read' in chunk:
                break
    assert [json.loads(f.split('data: ')[1])['item']['id'] for f in frames[:-1]] == [kept]
    assert 'quote' not in frames[0] and 'DUMMY' not in ''.join(frames) and gone not in ''.join(frames)
