"""Shared setup of the automation SQL tests (#939): a user with credits, their lists and campaign, and the run's db."""

import contextlib
import importlib
import uuid

from psycopg.types.json import Jsonb

from tests.conftest import member, migrated_database, pool_on, project, scoped_conn


def database(monkeypatch, balance_events):
    """The body of the ``db`` fixture: ``db = pytest.fixture(database)`` in a test file (a fixture imported by name
    reads as a redefinition to the linter)."""
    with migrated_database() as (conn, conninfo), pool_on(conninfo):
        scoped = scoped_conn(conn)

        @contextlib.contextmanager
        def connection(timeout=None):
            with conn.transaction(), scoped(timeout) as c:
                yield c

        for name in (
            'service',
            'approval',
            'preview',
            'runner',
            'steps',
            'run',
            'hubspot',
            'held_writes',
            'deliveries',
            'signal_trigger',
            'inbound',
            'instantly_step',
            'instantly_handoff',
        ):
            module = importlib.import_module(f'app.automations.{name}')
            monkeypatch.setattr(module, 'get_conn', connection)
        yield conn


def user(db, *, credits=200) -> str:
    uid = str(uuid.uuid4())
    db.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt")'
        " VALUES (%s, 'T', %s, false, now(), now())",
        (uid, f'{uid}@example.test'),
    )
    db.execute(
        'INSERT INTO user_credits (organization_id, user_id, credits_remaining)'
        ' VALUES (default_organization(%s), %s, %s)',
        (uid, uid, credits),
    )
    return uid


def make_list(db, uid, entity, rows=()) -> tuple[str, list[str]]:
    """A list of the user's default project with ``rows`` (cells). ``(list id, row ids)``."""
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity)'
        ' VALUES (default_organization(%s), %s, %s, %s, %s) RETURNING id',
        (uid, project(db, uid), uid, entity.title(), entity),
    ).fetchone()['id']
    ids = [
        str(
            db.execute(
                'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id',
                (lid, Jsonb(cells), f'{lid}-{i}'),
            ).fetchone()['id']
        )
        for i, cells in enumerate(rows)
    ]
    return str(lid), ids


def make_campaign(db, uid, *, status='draft') -> str:
    return str(
        db.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
            ' VALUES (default_organization(%s), %s, %s, %s, %s) RETURNING id',
            (uid, project(db, uid), uid, 'Outreach', status),
        ).fetchone()['id']
    )


def client(db, monkeypatch, uid, *, org=None, project_id=None):
    """An API client with an ``sbk_`` key of ``uid`` in ``org`` (their own by default), on ``project_id``."""
    from fastapi.testclient import TestClient

    from app.auth import middleware
    from app.automations import service
    from app.main import app

    monkeypatch.setattr(middleware, 'get_conn', service.get_conn)
    monkeypatch.setattr('app.projects.routes.get_conn', service.get_conn)
    key = f'sbk_{uid}'
    db.execute(
        "INSERT INTO user_api_keys (organization_id, user_id, label, key_hash) VALUES (%s, %s, 'A', %s)",
        (org or member(db, uid).org_id, uid, middleware.hash_api_key(key)),
    )
    headers = {'Authorization': f'Bearer {key}'} | ({'X-Project-Id': project_id} if project_id else {})
    return TestClient(app, headers=headers)


def teammate(db, org) -> str:
    uid = user(db)
    db.execute(
        'INSERT INTO member (id, "organizationId", "userId", role, "createdAt")'
        " VALUES (gen_random_uuid(), %s, %s, 'member', now())",
        (org, uid),
    )
    return uid


def connect_hubspot(db, uid, *, write_back=True):
    db.execute(
        'INSERT INTO user_oauth_connections (organization_id, user_id, provider, status, granted_scopes, crm_write_back,'
        ' crm_write_back_since, refresh_token_encrypted) VALUES (default_organization(%s), %s, %s, %s, %s, %s, now(), %s)',
        (
            uid,
            uid,
            'hubspot',
            'connected',
            ['crm.objects.contacts.write', 'crm.objects.companies.write'],
            write_back,
            'x',
        ),
    )
