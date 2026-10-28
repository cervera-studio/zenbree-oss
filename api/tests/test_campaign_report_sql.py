"""The campaign report (#942) through its route, against real SQL under row-level security. Needs TEST_DATABASE_URL."""

import contextlib
import itertools
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.integrations.instantly_sync.vocabulary import ReplyNature, reply_class
from app.main import app
from app.outreach.inbound import ReplyClass
from tests.conftest import member, migrated_database, pool_on, project, scoped_conn, sign_in
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: campaign report SQL test NOT run')

MAY = {'from': '2026-05-01', 'to': '2026-05-31', 'tz': 'UTC'}
APRIL = {'from': '2026-04-01', 'to': '2026-04-30', 'tz': 'UTC'}


def at(day: int, month: int = 5) -> datetime:
    return datetime(2026, month, day, 12, tzinfo=UTC)


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in ('app.reports.routes', 'app.projects.routes'):
                monkeypatch.setattr(f'{module}.get_conn', scoped_conn(c))
            yield c
        finally:
            app.dependency_overrides.clear()


def report(db, uid, params=MAY, project_id=None) -> dict:
    sign_in(app, member(db, uid))
    headers = {'X-Project-Id': project_id} if project_id else {}
    r = TestClient(app).get('/v1/reports/campaigns', params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def by_name(body: dict) -> dict:
    return {c['name']: c for c in body['campaigns']}


def campaign(db, uid, name='C', *, activated=True, list_id=None, project_id=None, steps=()) -> str:
    return str(
        db.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, list_id, activated_at,'
            ' steps) VALUES (default_organization(%s), %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s)'
            ' RETURNING id',
            (uid, project_id or project(db, uid), uid, name, 'active' if activated else 'draft', list_id, activated,
             json.dumps(list(steps))),
        ).fetchone()['id']
    )  # fmt: skip


def target(db, cid, uid, email, *, source='manual', signal_id=None, status='active', variant=None) -> str:
    return str(
        db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, source, signal_id, variant)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
            (cid, uid, email, status, source, signal_id, variant),
        ).fetchone()['id']
    )


def send(db, cid, uid, tid, step, when, channel='email', recipient='x@p.test') -> None:
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,'
        " idempotency_key, delivered_at) VALUES (%s, %s, %s, %s, %s, 'sent', %s, %s, %s)",
        (cid, uid, tid, step, channel, recipient, uuid.uuid4().hex, when),
    )


def reply(
    db, cid, uid, tid, when, *, kind='reply', label=None, sender='x@p.test', channel='email', member=None
) -> None:
    pid = db.execute('SELECT project_id FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()['project_id']
    db.execute(
        'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id, from_address,'
        ' campaign_id, target_id, label, received_at, sender_provider_id)'
        ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (uid, pid, channel, kind, uuid.uuid4().hex, sender, cid, tid, label, when, member),
    )


def test_a_person_counts_once_on_the_date_of_their_first_event(db):
    uid = _user(db)
    cid = campaign(db, uid)
    ann, bob = target(db, cid, uid, 'ann@p.test'), target(db, cid, uid, 'bob@p.test')
    send(db, cid, uid, ann, 1, at(2))
    send(db, cid, uid, ann, 2, at(5))
    reply(db, cid, uid, ann, at(6), label='interested')
    reply(db, cid, uid, ann, at(7), label='meeting_request')
    send(db, cid, uid, bob, 1, at(20, 4))  # contacted in April, replies in April and in May
    send(db, cid, uid, bob, 2, at(3))
    reply(db, cid, uid, bob, at(25, 4))
    reply(db, cid, uid, bob, at(8), label='interested')

    counts = report(db, uid)['campaigns'][0]['counts']
    assert counts['sent'] == {'email': 3, 'linkedin_connect': 0, 'linkedin_message': 0}  # sends, not people
    assert (counts['contacted'], counts['human_replies'], counts['positive_replies']) == (1, 1, 2)
    april = report(db, uid, APRIL)['campaigns'][0]['counts']
    assert (april['contacted'], april['human_replies'], april['positive_replies']) == (1, 1, 0)
    both = report(db, uid, {'from': '2026-04-01', 'to': '2026-05-31', 'tz': 'UTC'})['campaigns'][0]['counts']
    for measure in ('contacted', 'human_replies', 'positive_replies'):
        assert both[measure] == counts[measure] + april[measure]  # two windows split a range, never double it


AB_STEPS = (
    {'step': 1, 'channel': 'email', 'subject': 'Idea', 'body': 'Hi', 'send_after_days': 0, 'b': {'subject': 'Hey', 'body': 'Yo'}},
    {'step': 2, 'channel': 'linkedin_connect', 'subject': '', 'body': 'Hi', 'send_after_days': 2},
    {'step': 3, 'channel': 'linkedin_message', 'subject': '', 'body': 'Hi', 'send_after_days': 2},
)  # fmt: skip


def split(row: dict) -> dict:
    return {v.pop('group'): v for v in row['variants']}


def test_a_test_campaign_splits_each_count_by_copy_variant_and_the_groups_add_up(db):
    uid = _user(db)
    cid = campaign(db, uid, steps=AB_STEPS)
    ann, bob = target(db, cid, uid, 'ann@p.test', variant='a'), target(db, cid, uid, 'bob@p.test', variant='b')
    target(db, cid, uid, 'amy@p.test', variant='a')  # enrolled, not contacted yet
    oli = target(db, cid, uid, 'oli@p.test')  # joined while the allocation was paused
    gone = target(db, cid, uid, 'gone@p.test', variant='a')
    send(db, cid, uid, ann, 1, at(2))
    send(db, cid, uid, ann, 2, at(4), channel='linkedin_connect')
    send(db, cid, uid, bob, 1, at(2))
    send(db, cid, uid, bob, 3, at(6), channel='linkedin_message')
    send(db, cid, uid, oli, 1, at(3))
    send(db, cid, uid, gone, 1, at(3), recipient='gone@p.test')
    reply(db, cid, uid, ann, at(5), label='interested')
    reply(db, cid, uid, bob, at(7))
    reply(db, cid, uid, None, at(8), sender='boss@p.test')  # a colleague who matched nobody
    db.execute('DELETE FROM outreach_campaign_targets WHERE id = %s', (gone,))

    row = report(db, uid)['campaigns'][0]
    groups = split(row)
    counts = row['counts']
    for measure in ('enrolled', 'contacted', 'human_replies', 'positive_replies'):
        assert sum(v[measure] for v in groups.values()) == counts[measure]
    for channel in ('email', 'linkedin_connect', 'linkedin_message'):
        assert sum(v['sent'][channel] for v in groups.values()) == counts['sent'][channel]
    assert {g: (v['enrolled'], v['contacted'], v['human_replies'], v['positive_replies']) for g, v in groups.items()} == {
        'a': (2, 1, 1, 1),
        'b': (1, 1, 1, 0),
        'outside': (1, 1, 0, 0),
        'unknown': (0, 1, 1, 0),
    }  # fmt: skip
    assert {g: tuple(v['sent'].values()) for g, v in groups.items()} == {
        'a': (1, 1, 0),
        'b': (1, 0, 1),
        'outside': (1, 0, 0),
        'unknown': (1, 0, 0),
    }
    assert all(v['bounced'] is None for v in groups.values())
    assert (counts['enrolled'], counts['contacted'], counts['sent']['email']) == (4, 4, 4)


class _Racing:
    """A connection that enrolls a person and delivers a send after each statement. The report's later statements in
    the transaction see that write, as at READ COMMITTED they would see one another session committed in between."""

    def __init__(self, conn, between):
        self._conn, self._between = conn, between

    def execute(self, *args, **kwargs):
        cursor = self._conn.execute(*args, **kwargs)
        self._between(self._conn)
        return cursor

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_the_groups_add_up_to_the_row_while_people_join_and_sends_land(db, monkeypatch):
    uid = _user(db)
    cid = campaign(db, uid, steps=AB_STEPS)
    send(db, cid, uid, target(db, cid, uid, 'ann@p.test', variant='a'), 1, at(2))
    scoped = scoped_conn(db)

    def land(conn) -> None:
        send(conn, cid, uid, target(conn, cid, uid, f'{uuid.uuid4().hex}@p.test', variant='a'), 1, at(4))

    @contextlib.contextmanager
    def racing(timeout=None):
        with scoped(timeout) as conn:
            yield _Racing(conn, land)

    monkeypatch.setattr('app.reports.routes.get_conn', racing)
    row = report(db, uid)['campaigns'][0]
    groups = split(row)
    assert sum(v['enrolled'] for v in groups.values()) == row['counts']['enrolled']
    assert sum(v['sent']['email'] for v in groups.values()) == row['counts']['sent']['email']


def test_a_variant_counts_a_person_once_on_their_first_event_and_adjacent_windows_add_up(db):
    uid = _user(db)
    cid = campaign(db, uid, steps=AB_STEPS)
    bob = target(db, cid, uid, 'bob@p.test', variant='b')
    send(db, cid, uid, bob, 1, at(20, 4))
    send(db, cid, uid, bob, 2, at(3), channel='linkedin_connect')
    reply(db, cid, uid, bob, at(25, 4))
    reply(db, cid, uid, bob, at(8), label='interested')
    reply(db, cid, uid, bob, at(9), label='meeting_request')

    may, april = (split(report(db, uid, params)['campaigns'][0])['b'] for params in (MAY, APRIL))
    assert (may['contacted'], may['human_replies'], may['positive_replies']) == (0, 0, 1)
    assert (april['contacted'], april['human_replies'], april['positive_replies']) == (1, 1, 0)
    both = split(report(db, uid, {'from': '2026-04-01', 'to': '2026-05-31', 'tz': 'UTC'})['campaigns'][0])['b']
    for measure in ('contacted', 'human_replies', 'positive_replies'):
        assert both[measure] == may[measure] + april[measure]
    assert both['sent'] == {k: may['sent'][k] + april['sent'][k] for k in may['sent']}
    assert both['enrolled'] == may['enrolled'] == april['enrolled'] == 1  # all-time, not windowed


def test_a_campaign_with_no_test_has_no_variants(db):
    uid = _user(db)
    cid = campaign(db, uid, steps=[{k: v for k, v in AB_STEPS[0].items() if k != 'b'}])
    send(db, cid, uid, target(db, cid, uid, 'ann@p.test'), 1, at(2))
    row = report(db, uid)['campaigns'][0]
    assert row['counts']['contacted'] == 1 and row['variants'] == []


def test_a_campaign_whose_steps_store_no_copy_b_has_no_variants(db):
    uid = _user(db)
    campaign(db, uid, steps=[{**AB_STEPS[0], 'b': None}])
    assert report(db, uid)['campaigns'][0]['variants'] == []


def test_a_positive_reply_counts_to_the_campaign_that_received_it(db):
    uid = _user(db)
    first, second = campaign(db, uid, 'First'), campaign(db, uid, 'Second')
    for cid in (first, second):
        send(db, cid, uid, target(db, cid, uid, 'ann@p.test'), 1, at(2))
    reply(db, first, uid, db.execute('SELECT id FROM outreach_campaign_targets WHERE campaign_id = %s', (first,))
          .fetchone()['id'], at(3), label='interested')  # fmt: skip

    rows = by_name(report(db, uid))
    assert [rows[n]['counts']['positive_replies'] for n in ('First', 'Second')] == [1, 0]
    assert [rows[n]['counts']['contacted'] for n in ('First', 'Second')] == [1, 1]
    assert [c['name'] for c in report(db, uid)['campaigns']] == ['Second', 'First']  # newest first


def test_an_autoreply_or_an_opt_out_is_not_a_human_reply(db):
    uid = _user(db)
    cid = campaign(db, uid)
    tid = target(db, cid, uid, 'ann@p.test')
    reply(db, cid, uid, tid, at(2), kind='auto_reply')
    reply(db, cid, uid, tid, at(3), kind='unsubscribe')
    assert report(db, uid)['campaigns'][0]['counts']['human_replies'] == 0
    reply(db, cid, uid, tid, at(4))
    assert report(db, uid)['campaigns'][0]['counts']['human_replies'] == 1


def test_lineage_groups_signals_by_watchlist_and_puts_a_deleted_person_under_unknown(db):
    uid = _user(db)
    pid = project(db, uid)
    list_id = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (default_organization(%s), %s,'
        " %s, 'Leads', 'person') RETURNING id::text",
        (uid, pid, uid),
    ).fetchone()['id']
    hiring, funding = (
        db.execute(
            'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, next_run_at)'
            " VALUES (%s, %s, %s, '{CEO}', 'weekly', now()) RETURNING id::text",
            (uid, list_id, name),
        ).fetchone()['id']
        for name in ('Hiring', 'Funding')
    )

    def signal(watchlist) -> str:
        row = db.execute(
            'INSERT INTO list_rows (list_id, cells) VALUES (%s, %s) RETURNING id', (list_id, json.dumps({}))
        ).fetchone()['id']
        return str(
            db.execute(
                'INSERT INTO signals (watchlist_id, row_id, type, key, score, occurred_at)'
                " VALUES (%s, %s, 'job_posting', %s, 1, now()) RETURNING id",
                (watchlist, row, uuid.uuid4().hex),
            ).fetchone()['id']
        )

    cid = campaign(db, uid, list_id=list_id)
    people = {
        'a': target(db, cid, uid, 'a@p.test', source='signal', signal_id=signal(hiring)),
        'b': target(db, cid, uid, 'b@p.test', source='signal', signal_id=signal(hiring)),
        'c': target(db, cid, uid, 'c@p.test', source='signal', signal_id=signal(funding)),
        'd': target(db, cid, uid, 'd@p.test', source='csv'),
        'gone': target(db, cid, uid, 'gone@p.test', source='csv'),
    }
    for name, tid in people.items():
        send(db, cid, uid, tid, 1, at(2), recipient=f'{name}@p.test')
    reply(db, cid, uid, people['gone'], at(3), label='interested', sender='Gone@P.test')
    db.execute('DELETE FROM outreach_campaign_targets WHERE id = %s', (people['gone'],))

    row = report(db, uid)['campaigns'][0]
    leads = {'id': list_id, 'name': 'Leads'}
    assert row['list'] == leads
    lineage = {(r['source'], r['signal'] and r['signal']['watchlist_name']): r for r in row['lineage']}
    assert {k: v['contacted'] for k, v in lineage.items()} == {
        ('signal', 'Hiring'): 2,
        ('signal', 'Funding'): 1,
        ('csv', None): 1,
        (None, None): 1,
    }
    assert lineage['signal', 'Hiring']['signal'] == {
        'watchlist_id': hiring,
        'watchlist_name': 'Hiring',
        'type': 'job_posting',
    }
    assert lineage[None, None]['positive_replies'] == 1 and lineage[None, None]['list'] == leads
    for measure in ('contacted', 'human_replies', 'positive_replies'):
        assert sum(r[measure] for r in row['lineage']) == row['counts'][measure]


def test_a_reply_with_no_person_counts_once_by_its_address(db):
    uid = _user(db)
    cid = campaign(db, uid)
    reply(db, cid, uid, None, at(25, 4), label='interested', sender='Col@P.test')  # a colleague, or a deleted person
    reply(db, cid, uid, None, at(8), label='interested', sender=' col@p.test')
    may = report(db, uid)['campaigns'][0]['counts']
    assert (may['human_replies'], may['positive_replies']) == (0, 0)
    april = report(db, uid, APRIL)['campaigns'][0]['counts']
    assert (april['human_replies'], april['positive_replies']) == (1, 1)


def test_a_send_whose_person_was_deleted_counts_once_by_its_address(db):
    uid = _user(db)
    cid = campaign(db, uid)
    send(db, cid, uid, None, 1, at(20, 4), recipient='Gone@P.test')
    send(db, cid, uid, None, 2, at(3), recipient='gone@p.test ')
    may = report(db, uid)['campaigns'][0]['counts']
    assert (may['contacted'], may['sent']['email']) == (0, 1)
    assert report(db, uid, APRIL)['campaigns'][0]['counts']['contacted'] == 1


def test_a_crm_task_neither_contacts_a_person_nor_counts_as_a_send(db):
    uid = _user(db)
    task_only, task_first = campaign(db, uid, 'Task only'), campaign(db, uid, 'Task first')
    send(db, task_only, uid, target(db, task_only, uid, 'ann@p.test'), 1, at(2), channel='hubspot_task')
    bob = target(db, task_first, uid, 'bob@p.test')
    send(db, task_first, uid, bob, 1, at(20, 4), channel='hubspot_task')
    send(db, task_first, uid, bob, 2, at(3))  # bob's first message is in May: the April task did not contact him
    rows = {name: row['counts'] for name, row in by_name(report(db, uid)).items()}
    assert (rows['Task only']['contacted'], rows['Task first']['contacted']) == (0, 1)
    assert [sum(rows[n]['sent'].values()) for n in ('Task only', 'Task first')] == [0, 1]


def test_unmatched_linkedin_replies_with_no_address_are_each_their_own_person(db):
    uid = _user(db)
    cid = campaign(db, uid)
    reply(db, cid, uid, None, at(20, 4), sender='', channel='linkedin')
    for day in (2, 3):
        reply(db, cid, uid, None, at(day), sender='', channel='linkedin')
    assert report(db, uid)['campaigns'][0]['counts']['human_replies'] == 2


def test_a_linkedin_reply_with_no_address_counts_once_by_its_member_id(db):
    uid = _user(db)
    cid = campaign(db, uid)
    reply(db, cid, uid, None, at(20, 4), sender='', channel='linkedin', member='ACoA-early')
    for day in (2, 3):
        reply(db, cid, uid, None, at(day), sender='', channel='linkedin', member='ACoA-early')
        reply(db, cid, uid, None, at(day), sender='', channel='linkedin', member='ACoA-new')
    assert report(db, uid)['campaigns'][0]['counts']['human_replies'] == 1


def test_drafts_are_not_listed_and_outcomes_not_read_are_unknown(db):
    uid = _user(db)
    campaign(db, uid, 'Draft', activated=False)
    cid = campaign(db, uid, 'Live')
    target(db, cid, uid, 'a@p.test')
    target(db, cid, uid, 'b@p.test', status='excluded')
    body = report(db, uid)
    assert [c['name'] for c in body['campaigns']] == ['Live']
    counts = body['campaigns'][0]['counts']
    assert (counts['enrolled'], counts['bounced'], counts['meetings'], counts['deals']) == (1, None, None, None)
    assert body['window'] == {'start': '2026-05-01', 'end': '2026-05-31', 'time_zone': 'UTC'}
    assert body['instantly'] is None


def test_another_organization_or_another_project_sees_nothing(db):
    uid, other = _user(db), _user(db)
    cid = campaign(db, uid)
    send(db, cid, uid, target(db, cid, uid, 'a@p.test'), 1, at(2))
    assert report(db, uid)['campaigns'][0]['counts']['contacted'] == 1
    assert report(db, other)['campaigns'] == []
    sign_in(app, member(db, uid))
    second = TestClient(app).post('/v1/projects', json={'name': 'Other'}).json()['project']['id']
    assert report(db, uid, project_id=second)['campaigns'] == []


def test_the_window_is_the_users_local_days(db):
    uid = _user(db)
    cid = campaign(db, uid)
    send(db, cid, uid, target(db, cid, uid, 'a@p.test'), 1, datetime(2026, 4, 30, 22, 30, tzinfo=UTC))
    assert report(db, uid, {**MAY, 'tz': 'Europe/Rome'})['campaigns'][0]['counts']['contacted'] == 1  # 1 May 00:30
    assert report(db, uid)['campaigns'][0]['counts']['contacted'] == 0
    sign_in(app, member(db, uid))
    for params in ({**MAY, 'tz': 'Nowhere/City'}, {**MAY, 'to': '2026-04-01'}, {**MAY, 'from': 'May 1st'}):
        r = TestClient(app).get('/v1/reports/campaigns', params=params)
        assert (r.status_code, r.json()['detail']['code']) == (422, 'REQUEST.INVALID_INPUT')


# Instantly (#930 facts): one connection per project, people keyed by address within (workspace, campaign).

WS = 'ws-1'


class Instantly:
    def __init__(self, db, uid, project_id=None):
        self.db, self.uid = db, uid
        self.org = member(db, uid).org_id
        self.project = project_id or project(db, uid)
        self.connection = db.execute(
            "INSERT INTO instantly_connections (organization_id, project_id, name, workspace_id) VALUES (%s, %s, 'I', %s)"
            ' RETURNING id',
            (self.org, self.project, WS),
        ).fetchone()['id']
        db.execute(
            'INSERT INTO instantly_sync_state (connection_id, organization_id, project_id, workspace_id)'
            ' VALUES (%s, %s, %s, %s)',
            (self.connection, self.org, self.project, WS),
        )
        self.enrollments: dict[str, str] = {}

    def lead(self, campaign_id, email, *, name=None, list_id=None, row_id=None, outcome='accepted') -> str:
        if campaign_id not in self.enrollments:
            self.enrollments[campaign_id] = self.db.execute(
                'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, list_id, workspace_id,'
                " campaign_id, campaign_name, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed') RETURNING id",
                (self.org, self.project, self.connection, list_id, WS, campaign_id, name or campaign_id),
            ).fetchone()['id']
        return str(
            self.db.execute(
                'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, row_id, workspace_id,'
                ' campaign_id, email, outcome) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id',
                (self.org, self.project, self.enrollments[campaign_id], row_id, WS, campaign_id, email, outcome),
            ).fetchone()['id']
        )

    def fact(self, lead, kind, when, *, nature=None, ours=None, label=None, step=None, confirmed=True) -> None:
        row = self.db.execute('SELECT campaign_id, email FROM instantly_leads WHERE id = %s', (lead,)).fetchone()
        self.db.execute(
            'INSERT INTO instantly_events (organization_id, project_id, connection_id, lead_id, workspace_id,'
            ' campaign_id, email, kind, reply_nature, our_class, label, step, fact_key, provider_ref, occurred_at,'
            " time_basis) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'event')",
            (self.org, self.project, self.connection, lead, WS, row['campaign_id'], row['email'], kind, nature, ours,
             label, step, uuid.uuid4().hex, uuid.uuid4().hex if confirmed else None, when),
        )  # fmt: skip

    def refuse_emails(self) -> None:
        self.db.execute(
            "UPDATE instantly_sync_state SET emails_refused_at = now() + interval '1 second' WHERE connection_id = %s",
            (self.connection,),
        )


def test_instantly_copies_of_one_send_and_one_reply_count_once(db):
    uid = _user(db)
    i = Instantly(db, uid)
    ann = i.lead('camp-1', 'ann@p.test', name='Spring')
    i.fact(ann, 'sent', at(2), step=1)
    i.fact(ann, 'sent', at(2) + timedelta(minutes=3), step=1, confirmed=False)  # a webhook retried with a new time
    i.fact(ann, 'sent', at(9), step=2)
    i.fact(ann, 'reply', at(10), nature='human')
    i.fact(ann, 'reply', at(10) + timedelta(minutes=2), nature='human', confirmed=False)
    i.fact(ann, 'interest', at(11), label='meeting_booked')
    i.fact(ann, 'bounced', at(12))

    [row] = report(db, uid)['campaigns']
    assert (row['sender'], row['id'], row['name'], row['status'], row['list'], row['variants']) == (
        'instantly',
        'camp-1',
        'Spring',
        None,
        None,
        [],
    )
    counts = row['counts']
    assert counts['sent'] == {'email': 2, 'linkedin_connect': 0, 'linkedin_message': 0}
    assert (counts['enrolled'], counts['contacted'], counts['human_replies']) == (1, 1, 1)
    assert (counts['positive_replies'], counts['bounced'], counts['meetings'], counts['deals']) == (1, 1, None, None)
    coverage = report(db, uid)['instantly']
    assert (coverage['mode'], coverage['complete_until']) == ('polled', None)  # no full pass yet: not complete


def test_instantly_lineage_is_the_list_each_lead_came_from(db):
    uid = _user(db)
    i = Instantly(db, uid)
    pid = i.project
    lists = [
        db.execute(
            'INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, %s,'
            " 'person') RETURNING id::text",
            (i.org, pid, uid, name),
        ).fetchone()['id']
        for name in ('Rows', 'Enrolled')
    ]
    row = db.execute("INSERT INTO list_rows (list_id, cells) VALUES (%s, '{}') RETURNING id", (lists[0],)).fetchone()[
        'id'
    ]
    a = i.lead('camp-1', 'a@p.test', list_id=lists[1], row_id=row)
    b = i.lead('camp-1', 'b@p.test')
    for lead in (a, b):
        i.fact(lead, 'sent', at(2))
    by_list = {r['list'] and r['list']['name']: r['contacted'] for r in report(db, uid)['campaigns'][0]['lineage']}
    assert by_list == {'Rows': 1, 'Enrolled': 1}
    assert all(r['source'] is None and r['signal'] is None for r in report(db, uid)['campaigns'][0]['lineage'])


def test_an_instantly_reply_is_human_by_the_safer_vote_and_unvoted_waits_unless_emails_are_refused(db):
    uid = _user(db)
    i = Instantly(db, uid)
    cases = list(itertools.product([n.value for n in ReplyNature], [None, 'auto', 'opt_out']))
    for nature, ours in cases:
        i.fact(i.lead(f'{nature}-{ours}', 'ann@p.test'), 'reply', at(2), nature=nature, ours=ours)

    for refused in (False, True):
        if refused:
            i.refuse_emails()
        got = {c['id']: c['counts']['human_replies'] for c in report(db, uid)['campaigns']}
        for nature, ours in cases:
            voted = reply_class(ReplyNature(nature), ReplyClass(ours) if ours else None)
            human = voted in (ReplyClass.HUMAN, ReplyClass.OPT_OUT) or (voted is None and refused)
            assert got[f'{nature}-{ours}'] == int(human), (nature, ours, refused)
    assert sum(got.values()) == 6  # the cases are not all one answer


def test_an_instantly_interest_is_positive_only_for_a_positive_label(db):
    uid = _user(db)
    i = Instantly(db, uid)
    for n, label in enumerate(('interested', 'meeting_booked', 'meeting_completed', 'won', 'out_of_office', 'lost')):
        i.fact(i.lead('camp-1', f'p{n}@p.test'), 'interest', at(2), label=label)
    i.lead('camp-1', 'gone@p.test', outcome='failed')
    counts = report(db, uid)['campaigns'][0]['counts']
    assert (counts['positive_replies'], counts['enrolled']) == (4, 6)


def test_instantly_facts_of_another_project_or_organization_are_not_counted(db):
    uid, other = _user(db), _user(db)
    theirs = Instantly(db, other)
    theirs.fact(theirs.lead('camp-1', 'a@p.test'), 'sent', at(2))
    assert report(db, uid)['campaigns'] == [] and report(db, uid)['instantly'] is None

    ours = Instantly(db, uid)
    ours.fact(ours.lead('camp-1', 'b@p.test'), 'sent', at(2))
    sign_in(app, member(db, uid))
    second = TestClient(app).post('/v1/projects', json={'name': 'Other'}).json()['project']['id']
    shared = Instantly(db, uid, second)  # one workspace, one campaign, two clients
    shared.fact(shared.lead('camp-1', 'c@p.test'), 'sent', at(2))
    shared.fact(shared.lead('camp-1', 'd@p.test'), 'reply', at(3), nature='human')
    for project_id, contacted, replied in ((None, 1, 0), (second, 1, 1)):
        counts = report(db, uid, project_id=project_id)['campaigns'][0]['counts']
        assert (counts['contacted'], counts['human_replies'], counts['sent']['email']) == (contacted, replied, 1)


def test_a_report_counts_people_by_their_first_instantly_fact(db):
    uid = _user(db)
    i = Instantly(db, uid)
    ann = i.lead('camp-1', 'ann@p.test')
    i.fact(ann, 'sent', at(20, 4))
    i.fact(ann, 'sent', at(2))
    i.fact(ann, 'reply', at(3), nature='human')
    may = report(db, uid)['campaigns'][0]['counts']
    assert (may['contacted'], may['human_replies']) == (0, 1)
    spring = report(db, uid, {'from': '2026-04-01', 'to': '2026-05-31', 'tz': 'UTC'})['campaigns'][0]['counts']
    assert (spring['contacted'], spring['sent']['email']) == (1, 2)
