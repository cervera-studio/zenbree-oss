"""jobs.organization_id: the migration places every job by the first of four sources that names an organization."""

import os
import shutil
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import COPY_PREFIX
from tests.test_agent import _user, needs_db
from tests.test_agent import db as current_db  # noqa: F401 - pytest fixture, used by name

pytestmark = needs_db
MIGRATION = '20261013175258_tenant_paths.sql'


def _org(db, name) -> str:
    return str(
        db.execute(
            'INSERT INTO organization (name, slug, "createdAt") VALUES (%s, %s, now()) RETURNING id', (name, name)
        ).fetchone()['id']
    )


def _project(db, org, uid) -> str:
    """A project of org: a chat or a list names the organization of its project (composite key)."""
    return str(
        db.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'p') RETURNING id", (org, uid)
        ).fetchone()['id']
    )


def _chat(db, org, uid) -> str:
    return str(
        db.execute(
            'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (%s, %s, %s) RETURNING id',
            (org, _project(db, org, uid), uid),
        ).fetchone()['id']
    )


def _list(db, org, uid) -> str:
    return str(
        db.execute(
            "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, 'l', 'person')"
            ' RETURNING id',
            (org, _project(db, org, uid), uid),
        ).fetchone()['id']
    )


def _job(db, uid, status='succeeded', **cols) -> str:
    row = {'user_id': uid, 'status': status, **cols}
    return str(
        db.execute(
            f'INSERT INTO jobs ({", ".join(row)}) VALUES ({", ".join(["%s"] * len(row))}) RETURNING id',
            list(row.values()),
        ).fetchone()['id']
    )


def _ledger(db, uid, org, job, type_='deduction', status='committed') -> None:
    db.execute(
        'INSERT INTO credit_transactions (user_id, organization_id, type, amount, balance_after, status,'
        " reference_type, reference_id) VALUES (%s, %s, %s, 1, 0, %s, 'job', %s)",
        (uid, org, type_, status, job),
    )


@pytest.fixture(scope='module')
def before(tmp_path_factory):
    """A database migrated up to the file before this change, so the jobs are written on the schema that was real."""
    url = os.environ['TEST_DATABASE_URL']
    older = tmp_path_factory.mktemp('older')
    for p in sorted(MIGRATIONS.glob('*.sql')):
        if p.name < MIGRATION:
            shutil.copy(p, older / p.name)
    name = f'{COPY_PREFIX}before_paths_{uuid.uuid4().hex[:10]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name}')
    with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True) as c:
        migrate(c, older)
    yield name
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


@pytest.fixture
def db(before):
    """A copy of that database: jobs have no organization_id yet, and only this migration runs on it."""
    url = os.environ['TEST_DATABASE_URL']
    name = f'{COPY_PREFIX}paths_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name} TEMPLATE {before}')
    try:
        with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True, row_factory=dict_row) as c:
            yield c
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


def _migrate(db) -> None:
    db.execute((MIGRATIONS / MIGRATION).read_text())


def _rows(db) -> dict[str, str]:
    return {
        r['id']: r['organization_id'] for r in db.execute('SELECT id::text, organization_id::text FROM jobs').fetchall()
    }


def test_the_migration_places_each_job_by_the_first_source_that_names_an_organization(db):
    ada, bo, orphan = _user(db), _user(db), _user(db)
    ledger_org, chat_org, list_org, charge_org = (_org(db, n) for n in ('ledger', 'chat', 'list', 'charge'))
    ada_default = str(db.execute('SELECT default_organization(%s) AS o', (ada,)).fetchone()['o'])
    chat, other_list = _chat(db, chat_org, ada), _list(db, list_org, ada)
    row = db.execute(
        'INSERT INTO list_rows (list_id) VALUES (%s) RETURNING id', (_list(db, charge_org, ada),)
    ).fetchone()['id']

    by_ledger = _job(db, ada, chat_session_id=chat, list_id=other_list)  # the ledger wins over the chat and the list
    _ledger(db, ada, ledger_org, by_ledger)
    by_chat = _job(db, ada, chat_session_id=chat, list_id=other_list)  # the chat wins over the list
    by_list = _job(db, ada, list_id=other_list)
    retried = _job(db, ada, chat_session_id=chat)
    by_retry = _job(db, ada, retry_of=retried)
    by_retry_of_retry = _job(db, ada, retry_of=by_retry)
    # A retry follows its parent whatever placed the parent: here a ledger row, then a list, then a charge.
    retry_of_ledger = _job(db, ada, retry_of=by_ledger)
    retry_of_list = _job(db, ada, retry_of=by_list)
    by_charge = _job(db, bo)
    db.execute(
        "INSERT INTO row_charges (job_id, row_id, keys, credits, state) VALUES (%s, %s, '{email}', 1, 'held')",
        (by_charge, row),
    )
    retry_of_charge = _job(db, bo, retry_of=by_charge)
    # Ada's default organization names no source: it must not place a job.
    no_source = _job(db, ada)
    gone_live = _job(db, orphan, 'running')
    waiting = _job(db, orphan, 'needs_input')
    # The only trace of a failed live job: its status when the row goes.
    db.execute(
        'CREATE TABLE zt_deleted (id uuid, status text);'
        ' CREATE FUNCTION zt_keep() RETURNS trigger LANGUAGE plpgsql AS'
        ' $$ BEGIN INSERT INTO zt_deleted VALUES (OLD.id, OLD.status); RETURN OLD; END $$;'
        ' CREATE TRIGGER zt_keep BEFORE DELETE ON jobs FOR EACH ROW EXECUTE FUNCTION zt_keep()'
    )

    _migrate(db)

    assert _rows(db) == {
        by_ledger: ledger_org,
        by_chat: chat_org,
        by_list: list_org,
        retried: chat_org,
        by_retry: chat_org,
        by_retry_of_retry: chat_org,
        retry_of_ledger: ledger_org,
        retry_of_list: list_org,
        by_charge: charge_org,
        retry_of_charge: charge_org,
    }
    assert ada_default not in _rows(db).values()
    assert {r['id']: r['status'] for r in db.execute('SELECT id::text, status FROM zt_deleted').fetchall()} == {
        no_source: 'succeeded',
        gone_live: 'failed',
        waiting: 'failed',
    }


def test_a_job_with_a_charge_survives_with_its_record(db):
    """No ledger row, no chat, no list and no starter: only the charge places it."""
    ada = _user(db)
    org = _org(db, 'charged')
    row = db.execute('INSERT INTO list_rows (list_id) VALUES (%s) RETURNING id', (_list(db, org, ada),)).fetchone()[
        'id'
    ]
    jid = _job(db, None)
    db.execute(
        "INSERT INTO row_charges (job_id, row_id, keys, credits, state) VALUES (%s, %s, '{email}', 1, 'held')",
        (jid, row),
    )
    _migrate(db)
    assert db.execute('SELECT job_id::text FROM row_charges').fetchone()['job_id'] == jid
    assert _rows(db) == {jid: org}


def test_a_job_with_no_source_is_deleted(db):
    ada = _user(db)
    _job(db, ada)
    _job(db, None)
    _migrate(db)
    assert _rows(db) == {}


def test_a_job_in_two_organizations_of_the_ledger_stops_the_migration(db):
    ada = _user(db)
    jid = _job(db, ada)
    _ledger(db, ada, _org(db, 'one'), jid)
    _ledger(db, ada, _org(db, 'two'), jid)
    with pytest.raises(Exception, match=r'1 job.*more than one organization'):
        _migrate(db)


def test_the_ledger_of_a_job_that_is_gone_does_not_stop_the_migration(db):
    ada = _user(db)
    gone = '00000000-0000-4000-8000-000000000001'
    _ledger(db, ada, _org(db, 'one'), gone)
    _ledger(db, ada, _org(db, 'two'), gone)
    _migrate(db)
    assert _rows(db) == {}


def test_an_open_hold_of_a_job_with_no_user_keeps_the_spend_of_the_job(db):
    """The ledger row places the job, so the reaper still finds it: it keeps the 30 credits spent, not 0."""
    ada = _user(db)
    org = str(db.execute('SELECT default_organization(%s) AS o', (ada,)).fetchone()['o'])
    jid = _job(db, ada, 'running', credits_spent=30)
    db.execute("SELECT reserve_credits(%s, %s, 40, 'job', %s)", (org, ada, jid))
    db.execute('UPDATE jobs SET user_id = NULL')  # the account is gone, the chat and the list with it
    db.execute("UPDATE credit_transactions SET created_at = created_at - interval '30 minutes'")
    _migrate(db)
    assert _rows(db) == {jid: org}

    db.execute('SELECT * FROM reap_stale_reservations_ids(20)')

    assert db.execute('SELECT credits_remaining FROM user_credits WHERE organization_id = %s', (org,)).fetchone() == {
        'credits_remaining': 20
    }


def test_a_job_inserted_by_the_previous_image_takes_the_organization_of_its_chat_or_list(db):
    ada = _user(db)
    chat_org, list_org = _org(db, 'chat'), _org(db, 'list')
    chat, lst, same_org_list = _chat(db, chat_org, ada), _list(db, list_org, ada), _list(db, chat_org, ada)
    _migrate(db)
    # An insert without the column, as the image before this migration writes it. A job whose chat and list are in two
    # organizations is not made: the composite keys of the next migration refuse it.
    by_chat = _job(db, ada, chat_session_id=chat)
    by_chat_and_list = _job(db, ada, chat_session_id=chat, list_id=same_org_list)
    by_list = _job(db, ada, list_id=lst)
    assert _rows(db) == {by_chat: chat_org, by_chat_and_list: chat_org, by_list: list_org}
    with pytest.raises(Exception, match='organization_id'):
        _job(db, ada)


def test_a_job_needs_an_organization(current_db):  # noqa: F811 - the fixture imported above
    ada = _user(current_db)
    with pytest.raises(Exception, match='organization_id'):
        current_db.execute('INSERT INTO jobs (user_id) VALUES (%s)', (ada,))
