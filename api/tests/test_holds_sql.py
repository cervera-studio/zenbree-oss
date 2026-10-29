"""The hold (#931): ``outreach_hold()`` read through ``app.outreach.holds``. Needs TEST_DATABASE_URL."""

import psycopg
import pytest

from app.audience.follow import alias_map
from app.integrations.instantly_store import HELD
from app.outreach import holds
from tests.conftest import migrated_database, project
from tests.test_tools_sql import _user

ANN = ('email', 'ann@acme.test'), ('email', 'a.smith@acme.test'), ('linkedin_url', 'linkedin.com/in/ann')
OUTCOMES = ('pending', 'publishing', 'accepted', 'skipped', 'failed', 'uncertain')


@pytest.fixture
def db():
    with migrated_database() as (c, _):
        yield c


def org_of(c, pid) -> str:
    return str(c.execute('SELECT organization_id FROM projects WHERE id = %s', (pid,)).fetchone()['organization_id'])


def person(c, pid, *aliases: tuple[str, str]) -> str:
    org = org_of(c, pid)
    identity = c.execute(
        "INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (%s, %s, 'person') RETURNING id",
        (org, pid),
    ).fetchone()['id']
    for kind, value in aliases:
        c.execute(
            'INSERT INTO audience_aliases (organization_id, project_id, identity_id, entity, kind, value, source)'
            " VALUES (%s, %s, %s, 'person', %s, %s, 'test')",
            (org, pid, identity, kind, value),
        )
    return str(identity)


def lead(c, pid, email, outcome='accepted') -> str:
    org = org_of(c, pid)
    connection = c.execute(
        "INSERT INTO instantly_connections (organization_id, project_id, name, workspace_id) VALUES (%s, %s, 'x', 'ws')"
        ' ON CONFLICT (project_id) DO UPDATE SET name = EXCLUDED.name RETURNING id',
        (org, pid),
    ).fetchone()['id']
    enrollment = c.execute(
        'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, workspace_id, campaign_id,'
        " status) VALUES (%s, %s, %s, 'ws', gen_random_uuid()::text, 'completed') RETURNING id",
        (org, pid, connection),
    ).fetchone()['id']
    return str(
        c.execute(
            'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email,'
            " outcome) VALUES (%s, %s, %s, 'ws', 'camp', %s, %s) RETURNING id",
            (org, pid, enrollment, email, outcome),
        ).fetchone()['id']
    )


def stop(c, pid, email, status='pending') -> None:
    lead_id = lead(c, pid, email, 'skipped')
    c.execute(
        'INSERT INTO outreach_stops (organization_id, project_id, connection_id, lead_id, email, reason, cause,'
        ' status, last_error_code, confirmed_at, confirmed_by)'
        ' SELECT organization_id, project_id, (SELECT id FROM instantly_connections WHERE project_id = l.project_id),'
        " id, email, 'human_reply', 'inbound:test', %(status)s,"
        " CASE WHEN %(status)s = 'failed' THEN 'INSTANTLY.UNREACHABLE' END,"
        " CASE WHEN %(status)s = 'confirmed' THEN now() END, CASE WHEN %(status)s = 'confirmed' THEN 'provider' END"
        ' FROM instantly_leads l WHERE id = %(lead)s',
        {'status': status, 'lead': lead_id},
    )


def second_project(c, uid) -> str:
    first = project(c, uid)
    return str(
        c.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Other client') RETURNING id",
            (org_of(c, first), uid),
        ).fetchone()['id']
    )


def test_each_reason_is_a_hold_reason_and_the_first_one_wins(db):
    pid = project(db, _user(db))
    answers = []
    lead(db, pid, 'ann@acme.test')
    answers.append(holds.hold(db, pid, 'email', 'ann@acme.test', None))
    for status in ('confirmed', 'pending', 'failed'):
        stop(db, pid, 'ann@acme.test', status)
        answers.append(holds.hold(db, pid, 'email', 'ann@acme.test', None))
    assert answers[::-1] == list(holds.HOLD_REASONS)


@pytest.mark.parametrize('outcome', OUTCOMES)
def test_instantly_holds_the_email_for_exactly_its_held_outcomes(db, outcome):
    pid = project(db, _user(db))
    lead(db, pid, 'ann@acme.test', outcome)
    expected = 'held_by_instantly' if outcome in HELD else None
    assert holds.hold(db, pid, 'email', ' Ann@Acme.test ', None) == expected


def test_instantly_holds_email_only_and_a_stop_holds_every_channel(db):
    pid = project(db, _user(db))
    lead(db, pid, 'ann@acme.test')
    assert [holds.hold(db, pid, ch, 'ann@acme.test', None) for ch in ('linkedin_connect', 'hubspot_task', None)] == [
        None,
        None,
        None,
    ]
    stop(db, pid, 'ann@acme.test')
    assert {holds.hold(db, pid, ch, 'ann@acme.test', None) for ch in ('email', 'linkedin_message', None)} == {
        'stop_pending'
    }


def test_an_alias_holds_through_the_email_and_the_linkedin_url(db):
    pid = project(db, _user(db))
    person(db, pid, *ANN)
    lead(db, pid, 'a.smith@acme.test')
    assert holds.hold(db, pid, 'email', 'ANN@acme.test', None) == 'held_by_instantly'
    assert holds.hold(db, pid, 'email', '', 'https://www.linkedin.com/in/ann/?trk=x') == 'held_by_instantly'
    assert holds.hold(db, pid, 'email', 'someone@else.test', 'https://linkedin.com/in/bob') is None


def test_another_projects_alias_lead_or_stop_never_holds(db):
    uid = _user(db)
    pid, other = project(db, uid), second_project(db, uid)
    lead(db, other, 'ann@acme.test')
    stop(db, other, 'bob@acme.test')
    person(db, other, *ANN)
    lead(db, pid, 'a.smith@acme.test')
    assert holds.hold(db, pid, 'email', 'ann@acme.test', None) is None
    assert holds.hold(db, pid, 'email', 'bob@acme.test', None) is None
    assert holds.hold(db, pid, 'email', '', 'linkedin.com/in/ann') is None
    assert holds.hold(db, other, 'email', 'ann@acme.test', None) == 'held_by_instantly'


def test_a_query_filters_on_the_same_rule(db):
    pid = project(db, _user(db))
    person(db, pid, *ANN)
    lead(db, pid, 'a.smith@acme.test')
    free = holds.not_held('p.project_id', "'email'", 'p.email', "''")
    rows = db.execute(
        f'SELECT p.email FROM (VALUES (%s::uuid, %s), (%s::uuid, %s)) p(project_id, email) WHERE {free}',
        (pid, 'ann@acme.test', pid, 'bea@acme.test'),
    ).fetchall()
    assert [r['email'] for r in rows] == ['bea@acme.test']


@pytest.mark.parametrize(
    ('email', 'linkedin'),
    [
        ('ann@acme.test', None),
        (' A.Smith@ACME.test ', ''),
        (None, 'https://www.linkedin.com/in/ann/'),
        ('cat@acme.test', 'linkedin.com/in/ann'),
        ('info@acme.test', None),
        ('nobody@acme.test', 'linkedin.com/in/nobody'),
        ('', None),
    ],
)
def test_the_people_emails_are_the_email_aliases_enrollment_follows(db, email, linkedin):
    pid = project(db, _user(db))
    person(db, pid, *ANN)
    person(db, pid, ('email', 'cat@acme.test'), ('email', 'cathy@acme.test'))
    sql = {
        r['email']
        for r in db.execute('SELECT email FROM outreach_person_emails(%s, %s, %s)', (pid, email, linkedin)).fetchall()
    }
    followed = alias_map(db, pid, [('email', email), ('linkedin_url', linkedin)])
    itself = {(email or '').strip().lower()} - {''}
    assert sql == itself | {v for k, v in followed if k == 'email'}


def _refused(c, sql, arg) -> bool:
    try:
        with c.transaction():
            c.execute(sql, (arg,))
    except psycopg.errors.IntegrityError:
        return True
    return False


def test_deleting_the_lead_or_the_connection_of_a_stop_is_refused(db):
    pid = project(db, _user(db))
    stop(db, pid, 'ann@acme.test')
    held = db.execute('SELECT lead_id, connection_id FROM outreach_stops').fetchone()
    assert _refused(db, 'DELETE FROM instantly_leads WHERE id = %s', held['lead_id'])
    assert _refused(db, 'DELETE FROM instantly_connections WHERE id = %s', held['connection_id'])
    assert holds.stopped(db, pid, 'ann@acme.test', None)


@pytest.mark.parametrize('owner', ['projects', 'organization'])
def test_deleting_the_project_or_the_organization_takes_its_stops(db, owner):
    pid = project(db, _user(db))
    stop(db, pid, 'ann@acme.test')
    key = pid if owner == 'projects' else org_of(db, pid)
    db.execute(f'DELETE FROM {owner} WHERE id = %s', (key,))
    assert db.execute('SELECT count(*) AS n FROM outreach_stops').fetchone()['n'] == 0
