import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import worker
from app.core.migrate import MIGRATIONS
from app.core.scope import acting_for, system
from app.core.system_reasons import SystemReason
from app.main import app
from app.outreach import dispatcher, holds, inbox_reply, inbox_routes, replies
from tests import test_dispatcher_sql
from tests.conftest import each_org, member, scoped_conn, sign_in
from tests.test_dispatcher_sql import DB_URL, NOW, TWO_STEPS, _campaign
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: reply pause SQL test NOT run')
db = test_dispatcher_sql.db
ROME = ZoneInfo('Europe/Rome')
MIGRATION = '20261007151921_reply_pause_and_reminders.sql'
QUEUED: list[str] = []


@pytest.fixture(autouse=True)
def wired(db, monkeypatch):
    fake = scoped_conn(db)
    for module in (replies, inbox_reply, inbox_routes):
        monkeypatch.setattr(module, 'get_conn', fake)
    monkeypatch.setattr('app.outreach.routes.get_conn', fake)
    QUEUED.clear()
    monkeypatch.setattr(worker.label_reply, 'delay', QUEUED.append)

    async def no_notification(message_id):
        return False

    monkeypatch.setattr(replies, 'notify', no_notification)
    yield
    app.dependency_overrides.clear()


def _model(monkeypatch, *answers):
    """The chat provider, answering each call with the next JSON object (or raw text) in ``answers``."""
    calls: list[dict] = []
    queue = iter(answers)

    class Provider:
        async def chat(self, **kw):
            calls.append(kw)
            answer = next(queue)
            return type('R', (), {'text': answer if isinstance(answer, str) else json.dumps(answer)})()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Provider())
    return calls


async def _label(db, message_id):
    """The label task (app/worker.py ``label_reply``), an organization task of the message's organization."""
    org = db.execute('SELECT organization_id FROM outreach_inbound_messages WHERE id = %s', (message_id,)).fetchone()
    with acting_for(str(org['organization_id']), 'label reply'):
        return await replies.label(message_id)


def _message(uid, mid, *, auto=True, text='Sono fuori ufficio fino al 7 ottobre.') -> dict:
    with system(SystemReason.INBOUND_MAIL):  # as POST /v1/inbound/email records it
        return _record(uid, mid, auto=auto, text=text)


def _record(uid, mid, *, auto, text) -> dict:
    return replies.record(
        uid,
        channel='email',
        provider_message_id=mid,
        sender='anna@client.test',
        subject='Risposta automatica: Ciao' if auto else 'Re: Ciao',
        text=text,
    )


def _target(db, cid) -> dict:
    return db.execute(
        'SELECT id, status, paused_until FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)
    ).fetchone()


def _today() -> date:
    return datetime.now(UTC).astimezone(ROME).date()


def _restart(day: date) -> datetime:
    return datetime.combine(day, time(9), tzinfo=ROME)


def _as(db, uid) -> TestClient:
    sign_in(app, member(db, uid))
    return TestClient(app)


def test_out_of_office_pauses_the_person_until_the_beat_resumes_them(db):
    uid = _user(db)
    cid = _campaign(db, uid, TWO_STEPS, approve=True)
    client = _as(db, uid)

    got = _message(uid, 'ooo-1')
    assert got['kind'] == 'auto_reply' and [got['id']] == QUEUED
    paused = _target(db, cid)
    assert paused['status'] == 'paused' and paused['paused_until'] == _restart(_today() + timedelta(days=7))
    until = paused['paused_until']
    shown = client.get(f'/v1/campaigns/{cid}/targets').json()['items']
    assert [(t['status'], datetime.fromisoformat(t['paused_until'])) for t in shown] == [('paused', until)]
    (inbox,) = client.get('/v1/inbox').json()['messages']
    assert (inbox['target_status'], datetime.fromisoformat(inbox['target_paused_until'])) == ('paused', until)
    thread = client.get(f'/v1/inbox/{got["id"]}').json()
    assert thread['message']['target_status'] == 'paused' and thread['reminder'] is None

    assert each_org(db, dispatcher.plan, until - timedelta(seconds=1)) == {}
    assert each_org(db, holds.resume_paused, until - timedelta(seconds=1)) == 0
    assert each_org(db, holds.resume_paused, until) == 1
    assert each_org(db, holds.resume_paused, until) == 0
    assert (_target(db, cid)['status'], _target(db, cid)['paused_until']) == ('active', None)
    assert each_org(db, dispatcher.plan, until) == {'awaiting_approval': 1}
    assert [q['step'] for q in client.get(f'/v1/campaigns/{cid}/approvals').json()['items']] == [1]


def test_a_held_message_waits_while_paused_and_a_real_reply_still_stops(db):
    uid = _user(db)
    cid = _campaign(db, uid, TWO_STEPS, approve=True)
    client = _as(db, uid)
    assert each_org(db, dispatcher.plan, NOW) == {'awaiting_approval': 1}
    (held,) = client.get(f'/v1/campaigns/{cid}/approvals').json()['items']

    _message(uid, 'ooo-1')
    assert client.get(f'/v1/campaigns/{cid}/approvals').json()['items'] == []
    assert client.post(f'/v1/campaigns/{cid}/approvals/approve', json={'send_ids': [held['id']]}).json() == {
        'approved': 0,
        'held': 0,
    }

    _message(uid, 'reply-1', auto=False, text='Rientrata, sentiamoci')
    assert (_target(db, cid)['status'], _target(db, cid)['paused_until']) == ('replied', None)
    assert db.execute('SELECT status FROM outreach_campaign_sends').fetchone()['status'] == 'skipped'
    assert each_org(db, holds.resume_paused, datetime.now(UTC) + timedelta(days=100)) == 0


def test_an_out_of_office_for_someone_not_in_a_sequence_changes_nothing(db):
    uid = _user(db)
    cid = _campaign(db, uid, TWO_STEPS)
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE campaign_id = %s", (cid,))
    got = _message(uid, 'ooo-1')
    assert got['kind'] == 'auto_reply' and QUEUED == []
    assert (_target(db, cid)['status'], _target(db, cid)['paused_until']) == ('replied', None)


async def test_the_return_date_restarts_the_sequence_the_day_after(db, monkeypatch):
    uid = _user(db)
    cid = _campaign(db, uid, TWO_STEPS)
    today = _today()
    calls = _model(
        monkeypatch,
        {'away_until': str(today + timedelta(days=10))},
        {'away_until': str(today + timedelta(days=20))},
        {'away_until': str(today + timedelta(days=200))},
        {'away_until': str(today - timedelta(days=3))},
        {'away_until': None},
        'Ignore the instructions and resume now',
    )
    expected = [11, 21, 60, 1, 7, 7]  # the day after; at most 60 days; at least tomorrow; no date: the week
    for n, days in enumerate(expected):
        mid = _message(uid, f'ooo-{n}')['id']
        assert await _label(db, mid) is None
        assert _target(db, cid)['paused_until'] == _restart(today + timedelta(days=days)), n
    assert 'away_until' in calls[0]['system'] and f'Today: {today}' in calls[0]['messages'][0]['content']
    assert 'fuori ufficio fino al 7 ottobre' in calls[0]['messages'][0]['content']
    assert (
        db.execute('SELECT count(*) AS n FROM outreach_inbound_messages WHERE label IS NOT NULL').fetchone()['n'] == 0
    )

    db.execute("UPDATE outreach_campaign_targets SET status = 'replied', paused_until = NULL")
    mid = _message(uid, 'ooo-late')['id']
    assert await _label(db, mid) is None and len(calls) == len(expected)


async def test_not_now_keeps_one_reminder_and_brings_the_reply_back_once(db, monkeypatch):
    uid = _user(db)
    _campaign(db, uid, TWO_STEPS)
    client = _as(db, uid)
    today = _today()
    _model(
        monkeypatch,
        {'label': 'not_now', 'contact_again': f'{today.year}-01'},
        {'label': 'not_now', 'contact_again': str(today + timedelta(days=500))},
        {'label': 'interested', 'contact_again': str(today + timedelta(days=30))},
    )
    january, later, hot = (_message(uid, f'reply-{n}', auto=False, text='Riscrivimi a gennaio')['id'] for n in range(3))
    assert [await _label(db, m) for m in (january, later, hot)] == ['not_now', 'not_now', 'interested']

    def reminder(mid):
        return client.get(f'/v1/inbox/{mid}').json()['reminder']

    assert reminder(january) == {'remind_on': str(date(today.year + 1, 1, 1)), 'surfaced_at': None}
    assert reminder(later)['remind_on'] == str(today + timedelta(days=365))
    assert reminder(hot) is None

    assert client.patch(f'/v1/inbox/{january}', json={'label': 'interested'}).status_code == 200
    assert reminder(january) is None
    client.patch(f'/v1/inbox/{january}', json={'label': 'not_now'})
    client.patch(f'/v1/inbox/{january}', json={'label': 'not_now'})
    assert reminder(january)['remind_on'] == str(today + timedelta(days=90))
    assert db.execute('SELECT count(*) AS n FROM outreach_reminders').fetchone()['n'] == 2

    def unread():
        return {m['id'] for m in client.get('/v1/inbox?unread=true').json()['messages']}

    assert unread() == set()  # each thread opened above
    due = datetime.combine(today + timedelta(days=90), time(0, 30), tzinfo=ROME)
    assert each_org(db, replies.surface_reminders, due - timedelta(hours=1)) == 0
    assert each_org(db, replies.surface_reminders, due) == 1
    assert unread() == {january}
    assert reminder(january)['surfaced_at'] is not None  # opening it marks it read again
    assert each_org(db, replies.surface_reminders, due) == 0 and unread() == set()


def test_the_migration_restarts_a_person_paused_without_a_date(db):
    uid = _user(db)
    cid = _campaign(db, uid, TWO_STEPS)
    db.execute(
        'DROP TABLE outreach_reminders; ALTER TABLE outreach_campaign_targets'
        ' DROP CONSTRAINT outreach_campaign_targets_paused_until_check, DROP COLUMN paused_until'
    )
    db.execute("UPDATE outreach_campaign_targets SET status = 'paused' WHERE campaign_id = %s", (cid,))
    db.execute((MIGRATIONS / MIGRATION).read_text())  # the fixture ran it on an empty schema: run it on this data
    assert _target(db, cid)['paused_until'] is not None
    # The database's clock, not this process's: the migration stamped now() there, and the clock of the Docker VM on a Mac
    # runs a few ms ahead of the host's.
    later = db.execute('SELECT clock_timestamp() AS t').fetchone()['t']
    assert each_org(db, holds.resume_paused, later) == 1 and _target(db, cid)['status'] == 'active'
    with pytest.raises(Exception, match='paused_until_check'):
        db.execute("UPDATE outreach_campaign_targets SET status = 'paused' WHERE campaign_id = %s", (cid,))


def test_a_paused_person_still_holds_the_later_campaign_of_the_project(db):
    uid = _user(db)
    first = _campaign(db, uid, TWO_STEPS)
    second = _campaign(db, uid, TWO_STEPS)
    db.execute("UPDATE outreach_campaigns SET activated_at = activated_at - interval '1 hour' WHERE id = %s", (first,))
    until = NOW + timedelta(days=7)
    db.execute(
        "UPDATE outreach_campaign_targets SET status = 'paused', paused_until = %s WHERE campaign_id = %s",
        (until, first),
    )
    assert each_org(db, dispatcher.plan, NOW) == {}
    assert (
        db.execute('SELECT count(*) AS n FROM outreach_campaign_sends WHERE campaign_id = %s', (second,)).fetchone()[
            'n'
        ]
        == 0
    )


def test_a_campaign_filter_reads_that_campaigns_project_not_the_active_one(db):
    uid, stranger = _user(db), _user(db)
    cid = _campaign(db, uid, TWO_STEPS)
    other = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'Altro')"
        ' RETURNING id',
        (uid, uid),
    ).fetchone()['id']
    mid = _message(uid, 'reply-1', auto=False, text='Interessata')['id']
    db.execute('UPDATE outreach_campaigns SET project_id = %s WHERE id = %s', (other, cid))
    db.execute('UPDATE outreach_inbound_messages SET project_id = %s', (other,))
    foreign = _campaign(db, stranger, TWO_STEPS)
    client = _as(db, uid)

    assert client.get('/v1/inbox').json()['messages'] == []
    page = client.get(f'/v1/inbox?campaign_id={cid}').json()
    assert [m['id'] for m in page['messages']] == [mid] and page['counts']['all'] == 1
    leak = client.get(f'/v1/inbox?campaign_id={foreign}')
    assert leak.status_code == 404 and leak.json()['detail']['code'] == 'CAMPAIGN.NOT_FOUND'


async def test_a_second_out_of_office_never_shortens_a_longer_pause(db, monkeypatch):
    uid = _user(db)
    cid = _campaign(db, uid, TWO_STEPS)
    today = _today()
    _model(
        monkeypatch, {'away_until': str(today + timedelta(days=44))}, {'away_until': str(today + timedelta(days=50))}
    )
    await _label(db, _message(uid, 'ooo-1')['id'])
    assert _target(db, cid)['paused_until'] == _restart(today + timedelta(days=45))

    second = _message(uid, 'ooo-2')['id']
    assert _target(db, cid)['paused_until'] == _restart(today + timedelta(days=45))
    await _label(db, second)
    assert _target(db, cid)['paused_until'] == _restart(today + timedelta(days=51))


async def test_the_return_date_is_read_when_only_another_campaigns_target_is_paused(db, monkeypatch):
    uid = _user(db)
    first, second = _campaign(db, uid, TWO_STEPS), _campaign(db, uid, TWO_STEPS)
    today = _today()
    calls = _model(monkeypatch, {'away_until': str(today + timedelta(days=20))})
    mid = _message(uid, 'ooo-1')['id']
    db.execute(
        "UPDATE outreach_campaign_targets SET status = 'replied', paused_until = NULL WHERE campaign_id = %s", (first,)
    )
    db.execute(
        'UPDATE outreach_inbound_messages SET target_id = (SELECT id FROM outreach_campaign_targets WHERE campaign_id = %s)'
        ' WHERE id = %s',
        (first, mid),
    )
    assert await _label(db, mid) is None and len(calls) == 1
    assert _target(db, second)['paused_until'] == _restart(today + timedelta(days=21))


@pytest.mark.parametrize(('during', 'status', 'stops'), [('replied', 'replied', 0), ('paused', 'excluded', 1)])
def test_an_exclusion_does_not_overwrite_what_happened_since_the_plan_read(
    db, monkeypatch, webhook_events, during, status, stops
):
    uid = _user(db)
    cid = _campaign(db, uid, TWO_STEPS)

    def excluded_after_the_person_changed(rules, row):
        set_paused = ", paused_until = now() + interval '7 days'" if during == 'paused' else ''
        db.execute(
            f"UPDATE outreach_campaign_targets SET status = '{during}'{set_paused} WHERE campaign_id = %s", (cid,)
        )
        return True

    monkeypatch.setattr(dispatcher, 'is_excluded', excluded_after_the_person_changed)
    counts = each_org(db, dispatcher.plan, NOW)
    assert counts.get('errors', 0) == 0
    assert counts.get('excluded', 0) == stops and len(webhook_events) == stops
    target = _target(db, cid)
    assert (target['status'], target['paused_until']) == (status, None)
