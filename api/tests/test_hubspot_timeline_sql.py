"""The HubSpot card's timeline block and its two buttons (#932) over real SQL: each caller reads and changes their
own rows, and nobody else's. Needs TEST_DATABASE_URL."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.scope import acting_for
from app.crm.timeline import facts, ledger
from app.integrations import hubspot
from app.main import app
from tests.conftest import member, project, scoped_conn, sign_in
from tests.test_crm_timeline_sql import NOW, T0
from tests.test_crm_timeline_sql import db as db
from tests.test_crm_timeline_sql import dispatcher_db as dispatcher_db
from tests.test_crm_timeline_sql import hubspot as connect_hubspot
from tests.test_instantly_events_sql import pytestmark as pytestmark  # skipped without TEST_DATABASE_URL
from tests.test_tools_sql import _user


@pytest.fixture(autouse=True)
def _hubspot_reads_the_test_database(db, monkeypatch):
    monkeypatch.setattr(hubspot, 'get_conn', scoped_conn(db))
    yield
    app.dependency_overrides.clear()


def _fact(org, project_id, owner, n) -> facts.TimelineFact:
    return facts.TimelineFact(
        source=facts.Source.INSTANTLY,
        source_id=str(uuid.uuid4()),
        organization_id=org,
        project_id=project_id,
        owner_id=owner,
        kind=facts.Kind.OUTGOING_EMAIL,
        email=f'p{n}@acme.test',
        occurred_at=T0,
        dedupe_key=f'k:{uuid.uuid4()}',
        message_id=None,
        person=facts.Person(),
        context=facts.Context(provider='Instantly', campaign='c', project='p', list=None, step=1),
    )


def _rows(db, org, project_id, owner, n, *, state, code, at_least=1):
    """``n`` rows of ``owner`` in ``state`` (and ``code`` when the state needs one)."""
    with acting_for(org, 'test'), scoped_conn(db)() as conn:
        ledger.record(conn, [_fact(org, project_id, owner, i) for i in range(n)])
    db.execute(
        'UPDATE crm_timeline_events SET state = %s, code = %s, request = %s::jsonb, activity_id = %s,'
        ' claimed_at = now(), settled_at = now(), attempts = 5'
        ' WHERE owner_id IS NOT DISTINCT FROM %s AND organization_id = %s AND state = %s',
        (
            state,
            code,
            facts.freeze(facts.Kind.OUTGOING_EMAIL, NOW).as_json(),
            'x' if state == 'logged' else None,
            owner,
            org,
            'pending',
        ),
    )


class World:
    """Three people with a failed row each: the caller, a colleague in the same organization, a stranger."""

    def __init__(self, db):
        self.db = db
        self.me = _user(db)
        self.mine = member(db, self.me)
        self.project = project(db, self.me)
        self.colleague = _user(db)
        self.stranger = _user(db)
        for uid in (self.me, self.stranger):
            connect_hubspot(db, uid)
        self.other_org = member(db, self.stranger).org_id
        self.other_project = project(db, self.stranger)

    def seed(self):
        org = self.mine.org_id
        _rows(self.db, org, self.project, self.me, 2, state='failed', code=facts.Code.REFUSED.value)
        _rows(self.db, org, self.project, self.me, 3, state='logged', code=None)
        _rows(self.db, org, self.project, self.me, 1, state='uncertain', code=facts.Code.ANSWER_LOST.value)
        _rows(self.db, org, self.project, self.colleague, 4, state='failed', code=facts.Code.REFUSED.value)
        _rows(
            self.db, self.other_org, self.other_project, self.stranger, 5, state='failed', code=facts.Code.REFUSED.value
        )

    def call(self, method, path, *, project='active', **kw):
        """As the caller; ``project=None`` omits ``X-Project-Id``, so the organization's default project answers."""
        sign_in(app, self.mine)
        headers = {'X-Project-Id': self.project if project == 'active' else project} if project else {}
        return TestClient(app).request(method, f'/v1/integrations/hubspot{path}', headers=headers, **kw)

    def failed(self, owner) -> int:
        return self.db.execute(
            "SELECT count(*) AS n FROM crm_timeline_events WHERE owner_id = %s AND state = 'failed'", (owner,)
        ).fetchone()['n']


@pytest.fixture
def world(db):
    w = World(db)
    w.seed()
    return w


def test_the_status_counts_only_the_callers_rows(world):
    got = world.call('GET', '').json()['timeline']
    assert (got['logged'], got['pending']) == (3, 0)
    assert [(i['code'], i['state'], i['count'], i['action']) for i in got['issues']] == [
        ('CRM_TIMELINE.REFUSED', 'failed', 2, 'retry'),
        ('CRM_TIMELINE.ANSWER_LOST', 'uncertain', 1, 'resolve'),
    ]
    assert all(i['latest_at'] for i in got['issues'])


def test_a_user_with_no_connection_has_an_empty_block(db):
    uid = _user(db)
    sign_in(app, member(db, uid))
    got = TestClient(app).get('/v1/integrations/hubspot').json()
    assert got['timeline'] == {'logged': 0, 'pending': 0, 'issues': []}


def test_retry_changes_the_callers_failed_rows_of_that_code_and_no_one_elses(world):
    res = world.call('POST', '/timeline/retry', json={'code': 'CRM_TIMELINE.REFUSED'})
    assert (res.status_code, res.json()) == (200, {'changed': 2})
    assert (world.failed(world.me), world.failed(world.colleague), world.failed(world.stranger)) == (0, 4, 5)
    assert world.call('POST', '/timeline/retry', json={'code': 'CRM_TIMELINE.REFUSED'}).json() == {'changed': 0}
    assert world.call('GET', '').json()['timeline']['pending'] == 2


def test_an_ownerless_row_is_not_retryable_by_anyone(world):
    gone = _user(world.db)  # a deleted owner: the foreign key sets the column to NULL
    _rows(world.db, world.mine.org_id, world.project, gone, 1, state='failed', code=facts.Code.REFUSED.value)
    world.db.execute('UPDATE crm_timeline_events SET owner_id = NULL WHERE owner_id = %s', (gone,))
    assert world.call('POST', '/timeline/retry', json={'code': 'CRM_TIMELINE.REFUSED'}).json() == {'changed': 2}
    assert (
        world.db.execute(
            "SELECT count(*) AS n FROM crm_timeline_events WHERE owner_id IS NULL AND state = 'failed'"
        ).fetchone()['n']
        == 1
    )


@pytest.mark.parametrize(
    'code', ['CRM_TIMELINE.NO_OWNER', 'CRM_TIMELINE.ANSWER_LOST', 'CRM_TIMELINE.GRANT_REVOKED', 'NOPE', '']
)
def test_retry_takes_only_a_code_the_user_can_act_on(world, code):
    res = world.call('POST', '/timeline/retry', json={'code': code})
    assert (res.status_code, res.json()['detail']['code']) == (422, 'REQUEST.INVALID_INPUT')
    assert world.failed(world.me) == 2


def test_resolve_logs_again_or_takes_the_row_as_logged(world):
    again = world.call('POST', '/timeline/resolve', json={'code': 'CRM_TIMELINE.ANSWER_LOST', 'as': 'log_again'})
    assert (again.status_code, again.json()) == (200, {'changed': 1})
    assert world.call('GET', '').json()['timeline']['pending'] == 1
    world.db.execute(
        "UPDATE crm_timeline_events SET state = 'uncertain', code = %s WHERE state = 'pending' AND owner_id = %s",
        (facts.Code.ANSWER_LOST.value, world.me),
    )
    done = world.call('POST', '/timeline/resolve', json={'code': 'CRM_TIMELINE.ANSWER_LOST', 'as': 'logged'})
    assert done.json() == {'changed': 1}
    assert world.call('GET', '').json()['timeline']['logged'] == 4


def test_resolve_leaves_other_peoples_uncertain_rows(world):
    _rows(
        world.db,
        world.mine.org_id,
        world.project,
        world.colleague,
        2,
        state='uncertain',
        code=facts.Code.ANSWER_LOST.value,
    )
    world.call('POST', '/timeline/resolve', json={'code': 'CRM_TIMELINE.ANSWER_LOST', 'as': 'logged'})
    left = world.db.execute(
        "SELECT count(*) AS n FROM crm_timeline_events WHERE owner_id = %s AND state = 'uncertain'",
        (world.colleague,),
    ).fetchone()['n']
    assert left == 2


@pytest.mark.parametrize(
    'body',
    [
        {'code': 'CRM_TIMELINE.REFUSED', 'as': 'logged'},
        {'code': 'CRM_TIMELINE.ANSWER_LOST', 'as': 'forget'},
        {'code': 'CRM_TIMELINE.ANSWER_LOST'},
    ],
)
def test_resolve_refuses_a_code_or_an_answer_it_does_not_know(world, body):
    res = world.call('POST', '/timeline/resolve', json=body)
    assert (res.status_code, res.json()['detail']['code']) == (422, 'REQUEST.INVALID_INPUT')


def test_a_grant_in_error_still_shows_the_rows_that_wait_on_it(world):
    world.db.execute("UPDATE user_oauth_connections SET status = 'error' WHERE user_id = %s", (world.me,))
    _rows(world.db, world.mine.org_id, world.project, world.me, 2, state='pending', code=None)
    got = world.call('GET', '').json()
    assert got['connected'] is False
    waiting = [i for i in got['timeline']['issues'] if i['state'] == 'waiting']
    assert [(i['code'], i['count']) for i in waiting] == [('CRM_TIMELINE.GRANT_REVOKED', 2)]


def _ownerless(world, project_id, n):
    gone = _user(world.db)
    _rows(world.db, world.mine.org_id, project_id, gone, n, state='failed', code=facts.Code.REFUSED.value)
    world.db.execute(
        'UPDATE crm_timeline_events SET owner_id = NULL, state = %s, code = %s WHERE owner_id = %s',
        ('skipped', facts.Code.NO_OWNER.value, gone),
    )


def _no_owner(got) -> int:
    return sum(i['count'] for i in got['issues'] if i['code'] == 'CRM_TIMELINE.NO_OWNER')


def test_an_ownerless_row_shows_in_its_project_and_not_in_another_of_the_organization(world):
    other = world.db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'second') RETURNING id",
        (world.mine.org_id, world.me),
    ).fetchone()['id']
    _ownerless(world, world.project, 2)
    _ownerless(world, str(other), 3)
    assert _no_owner(world.call('GET', '').json()['timeline']) == 2
    assert _no_owner(world.call('GET', '', project=str(other)).json()['timeline']) == 3


def test_without_a_project_header_the_default_projects_ownerless_rows_show(world):
    _ownerless(world, world.project, 2)
    assert _no_owner(world.call('GET', '', project=None).json()['timeline']) == 2
