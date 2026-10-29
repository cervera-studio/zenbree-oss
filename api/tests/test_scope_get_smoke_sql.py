"""Every GET route, called as a member of organization A while A and B both hold data, under row-level security
(interrogate, Act on 7): none answers 500, and no body names an id of B. A route that read B's rows by a filter on
the user instead of the organization would show them here, or fail once the policy hides them. Each route whose path
parameter is filled from A's data answers 2xx, or is in ``NOT_2XX`` with its reason: a 404 there means the policy
hid a row of the caller's own organization.

The list of routes is generated from the app, so a new GET route is called with no edit here. The routes left out,
each with its reason, are in ``SKIPPED``.
"""

import re

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import main
from app.agent import routes as agent_routes
from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.core.event_stream import EventStream
from app.main import app
from app.outreach import unsubscribe
from tests.conftest import member, sign_in
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_org_isolation_sql import _org_a
from tests.test_scope_entry_points import _routes

pytestmark = needs_db

SKIPPED = {
    'GET routes that answer with an EventStream': 'a stream never ends under TestClient',
    '/v1/admin/*, for ids of B only': 'the back office runs as System and reads every organization on purpose:'
    ' called as an admin, and checked for no 500 only',
}

# The routes with a path parameter filled from A's data that answer other than 2xx on purpose. Each other one must
# answer 2xx: a 404 or a 403 there means row-level security hid a row of the caller's own organization.
NOT_2XX = {
    '/v1/integrations/mailboxes/{provider}/callback': '302: reads no row, hands code and state to the web app',
    '/v1/unsubscribe/{token}': '303: reads no row, sends the browser to the unsubscribe page',
    '/v1/avatars/{user_id}/{token}': '404: reads no row; the photo is in the object store, which has none here',
}

# The routes with a required query: without it they answer 422 and never read a row.
QUERIES = {'/v1/reports/campaigns': {'from': '2000-01-01', 'to': '2000-12-31', 'tz': 'UTC'}}


class _Redis:
    def ping(self) -> bool:
        return True


@pytest.fixture
def tc(db, monkeypatch, inline_worker, no_list_read_limit):
    monkeypatch.setattr(agent_routes, '_enqueue', lambda job_id: None)
    monkeypatch.setattr(main, '_redis', lambda: _Redis())
    monkeypatch.setattr(settings, 'outreach_reply_secret', 'smoke-secret')
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()


def _ids(db, tc, who) -> dict:
    ids = _org_a(tc, db, who)
    org = who.org_id
    one = lambda sql: (row := db.execute(sql, (org,)).fetchone()) and str(row['id'])  # noqa: E731
    ids |= {
        'import': one('SELECT id FROM csv_imports WHERE organization_id = %s'),
        'search': one('SELECT id FROM saved_searches WHERE organization_id = %s'),
        'row': one('SELECT id FROM list_rows WHERE organization_id = %s ORDER BY seq LIMIT 1'),
        'target_email': db.execute(
            'SELECT t.email FROM outreach_campaign_targets t WHERE t.campaign_id = %s', (ids['campaign'],)
        ).fetchone()['email'],
    }
    ids['watchlist'] = str(
        db.execute(
            'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, next_run_at)'
            " VALUES (%s, %s, 'W', '{CEO}', 'weekly', now()) RETURNING id",
            (who.id, ids['list']),
        ).fetchone()['id']
    )
    ids['webhook'] = str(
        db.execute(
            'INSERT INTO user_webhooks (user_id, organization_id, url, secret_encrypted, label)'
            " VALUES (%s, %s, 'https://203.0.113.10/hook', 'x', 'W') RETURNING id",
            (who.id, org),
        ).fetchone()['id']
    )
    project_id = db.execute('SELECT id FROM projects WHERE organization_id = %s', (org,)).fetchone()['id']
    connection = db.execute(
        "INSERT INTO instantly_connections (organization_id, project_id, name, workspace_id) VALUES (%s, %s, 'x', 'ws')"
        ' RETURNING id',
        (org, project_id),
    ).fetchone()['id']
    ids['instantly_enrollment'] = str(
        db.execute(
            'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, workspace_id, campaign_id,'
            " status) VALUES (%s, %s, %s, 'ws', 'camp', 'completed') RETURNING id",
            (org, project_id, connection),
        ).fetchone()['id']
    )
    ids['automation'] = str(
        db.execute(
            'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, credit_cap, definition)'
            " SELECT %s, project_id, %s, 'A', 'manual', 5, jsonb_build_object('list_id', %s::text,"
            " 'steps', jsonb_build_array(jsonb_build_object('type', 'find_emails')))"
            ' FROM lists WHERE id = %s RETURNING id',
            (org, who.id, ids['list'], ids['list']),
        ).fetchone()['id']
    )
    ids['automation_run'] = str(
        db.execute(
            'INSERT INTO automation_runs (organization_id, automation_id, version, definition, credit_cap, trigger_kind,'
            " scheduled_for) VALUES (%s, %s, 1, '{}', 5, 'manual', now()) RETURNING id",
            (org, ids['automation']),
        ).fetchone()['id']
    )
    ids['identity'] = str(
        db.execute(
            "INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (%s, %s, 'person')"
            ' RETURNING id',
            (org, project_id),
        ).fetchone()['id']
    )
    ids['segment'] = str(
        db.execute(
            'INSERT INTO segments (organization_id, project_id, name, entity, rule, published_at)'
            " VALUES (%s, %s, 'S', 'person', %s, now()) RETURNING id",
            (org, project_id, Jsonb({'type': 'group', 'op': 'and', 'children': [{'type': 'fit', 'op': 'is_filled'}]})),
        ).fetchone()['id']
    )
    return ids


def _fill(path: str, a, ids: dict) -> str:
    values = {
        'list_id': ids['list'],
        'session_id': ids['session'],
        'job_id': ids['job'],
        'import_id': ids['import'],
        'message_id': ids['message'],
        'campaign_id': ids['campaign'],
        'search_id': ids['search'],
        'row_id': ids['row'],
        'watchlist_id': ids['watchlist'],
        'webhook_id': ids['webhook'],
        'enrollment_id': ids['instantly_enrollment'],
        'automation_id': ids['automation'],
        'run_id': ids['automation_run'],
        'segment_id': ids['segment'],
        'identity_id': ids['identity'],
        'user_id': a.id,
        'org_id': a.org_id,
        'provider': 'google',
        'token': unsubscribe.mint(a.id, ids['target_email'], secret='smoke-secret'),
    }
    return re.sub(r'{(\w+)}', lambda m: str(values[m.group(1)]), path)


def _foreign(ids: dict, b) -> set[str]:
    found = {b.id, b.org_id}
    for value in ids.values():
        if isinstance(value, dict):
            found |= {str(v) for v in value.values() if isinstance(v, str) and len(v) == 36}
        elif isinstance(value, str) and len(value) == 36:
            found.add(value)
    return found


# The bell lists a notification of every organization the caller is a live member of (A's user is an admin of B),
# and its href names that organization: `/switch?org=<id>&next=...` (#743). Only the `org` value is exempt, so a B row
# id anywhere else, `next` included, still fails.
SWITCH_LINK = re.compile(r'(?<=/switch\?org=)[\w-]+')


def _without_switch_links(path: str, text: str) -> str:
    return SWITCH_LINK.sub('', text) if path == '/v1/notifications' else text


def test_no_get_route_fails_or_shows_another_organizations_rows(tc, db):
    a, b = member(db, _user(db)), member(db, _user(db))
    # A's user is also an admin of B and made B's rows there: a read filtered by the user instead of the
    # organization would return them (#701), so their ids are the ones to look for.
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'admin\', now())',
        (b.org_id, a.id),
    )
    b_ids = _ids(db, tc, CurrentUser(id=a.id, org_id=b.org_id))
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (b_ids['job'],))  # one live job per user
    a_ids = _ids(db, tc, a)
    foreign = _foreign(b_ids, b)
    admin = member(db, _user(db))
    db.execute('UPDATE "user" SET role = %s WHERE id = %s', ('admin', admin.id))

    gets = [r for r in _routes(app.router.routes) if 'GET' in r.methods and r.response_class is not EventStream]
    assert len(gets) > 50
    called, failures, own = [], [], 0
    for route in gets:
        url = _fill(route.path, a, a_ids)
        back_office = route.path.startswith('/v1/admin/')
        caller = admin if back_office else a
        sign_in(app, CurrentUser(id=caller.id, org_id=caller.org_id, role='admin' if back_office else None))
        params = {'ids': a.id} if route.path == '/v1/admin/users/overview' else QUERIES.get(route.path)
        r = tc.get(url, params=params)
        called.append(route.path)
        own += a_ids['list'] in r.text
        if r.status_code >= 500 and not (route.path == '/health' and r.status_code == 503):
            failures.append(f'{route.path}: {r.status_code} {r.text[:200]}')
        elif '{' in route.path and not 200 <= r.status_code < 300 and route.path not in NOT_2XX:
            failures.append(f'{route.path}: {r.status_code} for a row of its own organization {r.text[:200]}')
        elif not back_office and (
            leaked := sorted(i for i in foreign if i in _without_switch_links(route.path, r.text))
        ):
            failures.append(f'{route.path}: shows ids of organization B {leaked}')
    assert not failures, '\n'.join(failures)
    assert set(NOT_2XX) <= {r.path for r in gets}, 'NOT_2XX names a GET route that is gone: delete it'
    assert len(called) == len(gets)
    assert own >= 3  # the routes answered with A's rows, so an empty answer is not why B's ids are absent
