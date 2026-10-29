"""Every tenant row names its organization (PR 2 of the organization-scoping run).

Two things are proven here that the catalog test (test_tenant_catalog_sql.py) cannot see:
the migrations on data that breaks their rules, and fill_organization_id_from_parent() on a live schema.
"""

import os
import shutil
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.core.migrate import MIGRATIONS, migrate
from app.lists import lookup_claims
from tests.conftest import COPY_PREFIX, project
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db

pytestmark = needs_db
FIRST = '20261013175601_tenant_rows_roots.sql'
ROWS_MIGRATIONS = sorted(p for p in MIGRATIONS.glob('*.sql') if p.name >= FIRST and 'tenant_rows' in p.name)


def _org(c, name) -> str:
    return str(
        c.execute(
            'INSERT INTO organization (name, slug, "createdAt") VALUES (%s, %s, now()) RETURNING id',
            (name, f'{name}-{uuid.uuid4().hex[:6]}'),
        ).fetchone()['id']
    )


def _project(c, org, uid) -> str:
    return str(
        c.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'p') RETURNING id", (org, uid)
        ).fetchone()['id']
    )


def _insert(c, table, **cols) -> str:
    sql = f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({", ".join(["%s"] * len(cols))}) RETURNING id'
    return str(c.execute(sql, list(cols.values())).fetchone()['id'])


# ---------------------------------------------------------------------------------------------- the migrations on data


@pytest.fixture(scope='module')
def before(tmp_path_factory):
    """A database migrated up to the file before this change, built once and copied for each test."""
    url = os.environ['TEST_DATABASE_URL']
    older = tmp_path_factory.mktemp('older')
    for p in sorted(MIGRATIONS.glob('*.sql')):
        if p.name < FIRST:
            shutil.copy(p, older / p.name)
    name = f'{COPY_PREFIX}before_rows_{uuid.uuid4().hex[:10]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name}')
    with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True) as c:
        migrate(c, older)
    yield name
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


@pytest.fixture
def old(before):
    """A copy of that database that a test fills with data as the old schema allowed it."""
    url = os.environ['TEST_DATABASE_URL']
    name = f'{COPY_PREFIX}rows_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name} TEMPLATE {before}')
    try:
        with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True, row_factory=dict_row) as c:
            yield c
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


def _migrate(c, files=ROWS_MIGRATIONS) -> None:
    for path in files:
        c.execute(path.read_text())


def _org_of(c, table, id_col, id_) -> str | None:
    row = c.execute(f'SELECT organization_id::text AS o FROM {table} WHERE {id_col} = %s', (id_,)).fetchone()
    return row['o'] if row else None


def test_the_migrations_resolve_rows_that_disagree_with_their_parent(old):
    """A row keeps its organization and its project moves to one of that organization (never the other way round). A
    link that may be NULL into another organization is cut. A ledger row and a job keep their organization too."""
    ada = _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pa, pb = _project(old, a, ada), _project(old, b, ada)
    chat_a = _insert(old, 'chat_sessions', organization_id=a, project_id=pa, user_id=ada)
    chat_b = _insert(old, 'chat_sessions', organization_id=b, project_id=pb, user_id=ada)

    # A list in B whose project is in A keeps B with its rows and its chat; its project becomes the one of B.
    moved = _insert(
        old, 'lists', organization_id=b, project_id=pa, user_id=ada, name='m', entity='person', chat_session_id=chat_b
    )
    # A list in A that names a chat of B loses the link.
    cut = _insert(
        old, 'lists', organization_id=a, project_id=pa, user_id=ada, name='c', entity='person', chat_session_id=chat_b
    )
    # A chat, a campaign and an import in A whose project is in B; and a chat in an organization with no project.
    chat_wrong = _insert(old, 'chat_sessions', organization_id=a, project_id=pb, user_id=ada)
    campaign_wrong = _insert(old, 'outreach_campaigns', organization_id=a, project_id=pb, owner_id=ada, name='w')
    import_wrong = _insert(
        old,
        'csv_imports',
        organization_id=a,
        project_id=pb,
        user_id=ada,
        entity='person',
        name='i',
        chat_session_id=chat_b,
    )
    bare = _org(old, 'bare')
    chat_bare = _insert(old, 'chat_sessions', organization_id=bare, project_id=pa, user_id=ada)
    kept = _insert(
        old, 'lists', organization_id=a, project_id=pa, user_id=ada, name='k', entity='person', chat_session_id=chat_a
    )
    moved_row = _insert(old, 'list_rows', list_id=moved)
    kept_row = _insert(old, 'list_rows', list_id=kept)
    old.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, 'k', 'K', 'static', 0)", (moved,)
    )
    charge = _insert(old, 'row_charges', row_id=kept_row, keys=['email'], credits=1, state='held')

    # A job of B pointing at a list of A keeps B and loses the list. A ledger row of B with a project of A likewise.
    job = _insert(old, 'jobs', organization_id=b, user_id=ada, status='succeeded', list_id=kept)
    retry = _insert(old, 'jobs', organization_id=a, user_id=ada, status='succeeded', retry_of=job)
    ledger = _insert(
        old,
        'credit_transactions',
        organization_id=b,
        user_id=ada,
        type='deduction',
        amount=1,
        balance_after=0,
        project_id=pa,
    )
    old.execute('UPDATE row_charges SET job_id = %s WHERE id = %s', (job, charge))

    watch = _insert(
        old, 'watchlists', list_id=kept, name='w', job_titles=['x'], cadence='daily', next_run_at='2026-10-14'
    )
    ok_signal = _insert(
        old,
        'signals',
        watchlist_id=watch,
        row_id=kept_row,
        type='job_posting',
        key='k1',
        score=1,
        occurred_at='2026-10-13',
    )
    old.execute(
        'INSERT INTO watchlist_accounts (watchlist_id, row_id, checked_at) VALUES (%s, %s, now())', (watch, kept_row)
    )

    # A campaign of A, a target of it, and a send that names a sender of B.
    campaign = _insert(old, 'outreach_campaigns', organization_id=a, project_id=pa, owner_id=ada, name='c')
    target = _insert(old, 'outreach_campaign_targets', campaign_id=campaign, owner_id=ada, email='t@x.test')
    sender_b = _insert(
        old, 'user_oauth_connections', organization_id=b, user_id=ada, provider='google', refresh_token_encrypted='x'
    )
    send = _insert(
        old,
        'outreach_campaign_sends',
        campaign_id=campaign,
        owner_id=ada,
        target_id=target,
        step=1,
        channel='email',
        recipient='x@y.test',
        idempotency_key='k',
        sender_id=sender_b,
    )
    # The key (target_id, campaign_id, owner_id) is MATCH SIMPLE: a send with no owner skips it and can name a target
    # of another organization.
    campaign_b = _insert(old, 'outreach_campaigns', organization_id=b, project_id=pb, owner_id=ada, name='cb')
    target_b = _insert(old, 'outreach_campaign_targets', campaign_id=campaign_b, owner_id=ada, email='tb@x.test')
    send_cut = _insert(
        old,
        'outreach_campaign_sends',
        campaign_id=campaign,
        owner_id=None,
        target_id=target_b,
        step=1,
        channel='email',
        recipient='z@y.test',
        idempotency_key='k2',
    )
    # An inbound message of B that names a campaign and a target of A.
    inbound_b = _insert(
        old,
        'outreach_inbound_messages',
        project_id=pb,
        owner_id=ada,
        campaign_id=campaign,
        target_id=target,
        channel='email',
        kind='reply',
        provider_message_id='m2',
    )
    inbound = _insert(
        old,
        'outreach_inbound_messages',
        project_id=pa,
        owner_id=ada,
        campaign_id=campaign,
        target_id=target,
        channel='email',
        kind='reply',
        provider_message_id='m',
    )
    _insert(
        old,
        'outreach_inbox_replies',
        inbound_id=inbound,
        owner_id=ada,
        channel='email',
        sender_provider='google',
        recipient='x@y.test',
        body='b',
        sender_id=sender_b,
    )
    old.execute("INSERT INTO outreach_reminders (message_id, remind_on) VALUES (%s, '2026-10-20')", (inbound,))
    old.execute('INSERT INTO business_profiles (project_id) VALUES (%s)', (pa,))
    _insert(old, 'exclusions', project_id=pb, kind='domain', value='x.test')
    msg = _insert(old, 'chat_messages', session_id=chat_b, role='user')

    _migrate(old)

    def col(table, column, id_):
        row = old.execute(f'SELECT {column} AS v FROM {table} WHERE id = %s', (id_,)).fetchone()['v']
        return None if row is None else str(row)

    assert _org_of(old, 'lists', 'id', moved) == b
    assert col('lists', 'project_id', moved) == pb
    assert col('lists', 'chat_session_id', moved) == chat_b
    assert _org_of(old, 'list_rows', 'id', moved_row) == b
    assert (
        old.execute('SELECT organization_id::text AS o FROM list_columns WHERE list_id = %s', (moved,)).fetchone()['o']
        == b
    )
    assert col('lists', 'chat_session_id', cut) is None
    assert col('lists', 'chat_session_id', kept) == chat_a
    for table, id_, want in [
        ('chat_sessions', chat_wrong, pa),
        ('outreach_campaigns', campaign_wrong, pa),
        ('csv_imports', import_wrong, pa),
    ]:
        assert _org_of(old, table, 'id', id_) == a, table
        assert col(table, 'project_id', id_) == want, table
    assert col('csv_imports', 'chat_session_id', import_wrong) is None
    assert _org_of(old, 'chat_sessions', 'id', chat_bare) == bare
    assert (
        col('chat_sessions', 'project_id', chat_bare)
        == old.execute('SELECT id::text AS i FROM projects WHERE organization_id = %s', (bare,)).fetchone()['i']
    )
    assert _org_of(old, 'row_charges', 'id', charge) == a
    assert old.execute('SELECT job_id FROM row_charges WHERE id = %s', (charge,)).fetchone()['job_id'] is None

    assert _org_of(old, 'jobs', 'id', job) == b
    assert old.execute('SELECT list_id FROM jobs WHERE id = %s', (job,)).fetchone()['list_id'] is None
    assert old.execute('SELECT retry_of FROM jobs WHERE id = %s', (retry,)).fetchone()['retry_of'] is None
    assert _org_of(old, 'credit_transactions', 'id', ledger) == b
    assert (
        old.execute('SELECT project_id FROM credit_transactions WHERE id = %s', (ledger,)).fetchone()['project_id']
        is None
    )

    assert [str(r['id']) for r in old.execute('SELECT id FROM signals').fetchall()] == [ok_signal]
    assert _org_of(old, 'watchlists', 'id', watch) == a

    assert _org_of(old, 'outreach_campaign_targets', 'id', target) == a
    assert _org_of(old, 'outreach_campaign_sends', 'id', send) == a
    assert (
        old.execute('SELECT sender_id FROM outreach_campaign_sends WHERE id = %s', (send,)).fetchone()['sender_id']
        is None
    )
    assert col('outreach_campaign_sends', 'target_id', send) == target
    assert _org_of(old, 'outreach_campaign_sends', 'id', send_cut) == a
    assert col('outreach_campaign_sends', 'target_id', send_cut) is None
    assert col('outreach_inbound_messages', 'campaign_id', inbound_b) is None
    assert col('outreach_inbound_messages', 'target_id', inbound_b) is None
    assert _org_of(old, 'outreach_inbound_messages', 'id', inbound) == a
    assert old.execute('SELECT sender_id FROM outreach_inbox_replies').fetchone()['sender_id'] is None
    assert col('outreach_inbound_messages', 'campaign_id', inbound) == campaign
    assert old.execute('SELECT organization_id::text AS o FROM outreach_reminders').fetchone()['o'] == a
    assert old.execute('SELECT organization_id::text AS o FROM business_profiles').fetchone()['o'] == a
    assert old.execute('SELECT organization_id::text AS o FROM exclusions').fetchone()['o'] == b
    assert _org_of(old, 'chat_messages', 'id', msg) == b


def _project_of(c, table, id_) -> str:
    return str(c.execute(f'SELECT project_id FROM {table} WHERE id = %s', (id_,)).fetchone()['project_id'])


def test_a_chat_its_list_and_its_campaign_in_the_wrong_project_land_in_one_project_of_their_organization(old):
    """Catches: lists before chats, campaigns before lists."""
    ada = _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pa, pb = _project(old, a, ada), _project(old, b, ada)
    pa_later = _project(old, a, ada)
    # The chat's creator is Ada, who has two projects in A; the oldest is chosen, and the rest follow the chat.
    chat = _insert(old, 'chat_sessions', organization_id=a, project_id=pb, user_id=ada)
    lst = _insert(
        old, 'lists', organization_id=a, project_id=pb, user_id=ada, name='l', entity='person', chat_session_id=chat
    )
    camp = _insert(old, 'outreach_campaigns', organization_id=a, project_id=pb, owner_id=ada, name='c', list_id=lst)
    _migrate(old)
    assert _project_of(old, 'chat_sessions', chat) == pa
    assert _project_of(old, 'lists', lst) == pa
    assert _project_of(old, 'outreach_campaigns', camp) == pa
    assert pa_later != pa


def test_a_list_and_a_campaign_follow_their_parent_not_their_creator(old):
    """Catches: no chat coalesce (the list falls to its creator's project) and no list coalesce (likewise)."""
    ada, bob = _user(old), _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pa_ada, pa_bob, pb = _project(old, a, ada), _project(old, a, bob), _project(old, b, ada)
    # Ada's project is the older one, so the creator rule alone would pick it. The chat of Bob is wrong too and goes to
    # Bob's project, so a list and a campaign that follow their parents go there as well.
    chat = _insert(old, 'chat_sessions', organization_id=a, project_id=pb, user_id=bob)
    lst = _insert(
        old, 'lists', organization_id=a, project_id=pb, user_id=ada, name='l', entity='person', chat_session_id=chat
    )
    camp = _insert(old, 'outreach_campaigns', organization_id=a, project_id=pb, owner_id=ada, name='c', list_id=lst)
    _migrate(old)
    assert _project_of(old, 'chat_sessions', chat) == pa_bob
    assert _project_of(old, 'lists', lst) == pa_bob
    assert _project_of(old, 'outreach_campaigns', camp) == pa_bob
    assert pa_ada != pa_bob


def test_a_list_with_no_chat_takes_its_creators_project_and_a_campaign_follows_its_list(old):
    """Catches: no creator term, since the organization's oldest project is not the creator's."""
    ada, bob = _user(old), _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pa_ada, pa_bob, pb = _project(old, a, ada), _project(old, a, bob), _project(old, b, ada)
    lst = _insert(old, 'lists', organization_id=a, project_id=pb, user_id=bob, name='l', entity='person')
    camp = _insert(old, 'outreach_campaigns', organization_id=a, project_id=pb, owner_id=ada, name='c', list_id=lst)
    alone = _insert(old, 'outreach_campaigns', organization_id=a, project_id=pb, owner_id=bob, name='d')
    other = _insert(old, 'csv_imports', organization_id=a, project_id=pb, user_id=bob, entity='person', name='i')
    _migrate(old)
    assert _project_of(old, 'lists', lst) == pa_bob
    assert _project_of(old, 'outreach_campaigns', camp) == pa_bob
    assert _project_of(old, 'outreach_campaigns', alone) == pa_bob
    assert _project_of(old, 'csv_imports', other) == pa_bob
    assert pa_ada != pa_bob


def test_a_project_with_no_creator_is_not_preferred_to_the_creators_oldest(old):
    """Catches: ORDER BY (user_id = creator) DESC without IS TRUE, which sorts a NULL creator first."""
    ada = _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pa, pb = _project(old, a, ada), _project(old, b, ada)
    _project(old, a, ada)
    _project(old, a, None)
    chat = _insert(old, 'chat_sessions', organization_id=a, project_id=pb, user_id=ada)
    _migrate(old)
    assert _project_of(old, 'chat_sessions', chat) == pa


def test_an_organization_with_no_project_gets_one_that_is_onboarded(old):
    """The created project holds existing work, so it has a completed profile and shows no onboarding."""
    ada = _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pb = _project(old, b, ada)
    _insert(old, 'chat_sessions', organization_id=a, project_id=pb, user_id=ada)
    _migrate(old)
    created = old.execute('SELECT id FROM projects WHERE organization_id = %s', (a,)).fetchall()
    assert len(created) == 1
    profile = old.execute(
        'SELECT completed_at, welcome_closed_at FROM business_profiles WHERE project_id = %s', (created[0]['id'],)
    ).fetchone()
    assert profile['completed_at'] is not None
    assert profile['welcome_closed_at'] is not None
    assert old.execute('SELECT count(*) AS n FROM business_profiles').fetchone() == {'n': 1}


def test_a_pair_that_links_two_organizations_stops_the_migration_and_nothing_is_deleted(old):
    ada = _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pa, pb = _project(old, a, ada), _project(old, b, ada)
    list_a = _insert(old, 'lists', organization_id=a, project_id=pa, user_id=ada, name='a', entity='person')
    row_a = _insert(old, 'list_rows', list_id=list_a)
    row_b = _insert(
        old,
        'list_rows',
        list_id=_insert(old, 'lists', organization_id=b, project_id=pb, user_id=ada, name='b', entity='person'),
    )
    watch = _insert(
        old, 'watchlists', list_id=list_a, name='w', job_titles=['x'], cadence='daily', next_run_at='2026-10-14'
    )
    for row in (row_a, row_b):
        _insert(
            old,
            'signals',
            watchlist_id=watch,
            row_id=row,
            type='job_posting',
            key=f'k{row}',
            score=1,
            occurred_at='2026-10-13',
        )

    old.execute(
        'INSERT INTO watchlist_accounts (watchlist_id, row_id, checked_at) VALUES (%s, %s, now())', (watch, row_b)
    )

    with pytest.raises(psycopg.errors.RaiseException) as raised:
        _migrate(old)
    assert 'signals.organization_id: 1 row' in str(raised.value)
    assert 'watchlist_accounts.organization_id: 1 row' in str(raised.value)
    assert old.execute('SELECT count(*) AS n FROM signals').fetchone() == {'n': 2}
    assert old.execute('SELECT count(*) AS n FROM watchlist_accounts').fetchone() == {'n': 1}


def _claim(c, job, row, call='t1'):
    return c.execute(
        "INSERT INTO lookup_claims (job_id, tool_call_id, row_id, key) VALUES (%s, %s, %s, 'email')", (job, call, row)
    )


def test_a_lookup_claim_takes_the_organization_of_its_job(old):
    ada = _user(old)
    a = _org(old, 'a')
    pa = _project(old, a, ada)
    lst = _insert(old, 'lists', organization_id=a, project_id=pa, user_id=ada, name='a', entity='person')
    row = _insert(old, 'list_rows', list_id=lst)
    job = _insert(old, 'jobs', organization_id=a, user_id=ada, status='running', list_id=lst)
    _claim(old, job, row)

    _migrate(old)

    assert old.execute('SELECT organization_id::text AS o FROM lookup_claims').fetchone()['o'] == a


def test_a_lookup_claim_of_a_job_and_a_row_in_two_organizations_stops_the_migration_and_nothing_is_deleted(old):
    ada = _user(old)
    a, b = _org(old, 'a'), _org(old, 'b')
    pb = _project(old, b, ada)
    list_b = _insert(old, 'lists', organization_id=b, project_id=pb, user_id=ada, name='b', entity='person')
    row_b = _insert(old, 'list_rows', list_id=list_b)
    job_a = _insert(old, 'jobs', organization_id=a, user_id=ada, status='running')
    _claim(old, job_a, row_b)

    with pytest.raises(psycopg.errors.RaiseException) as raised:
        _migrate(old)
    assert 'lookup_claims.organization_id: 1 row' in str(raised.value)
    assert old.execute('SELECT count(*) AS n FROM lookup_claims').fetchone() == {'n': 1}


def test_the_migrations_run_on_an_empty_schema_and_every_key_holds(old):
    """Nothing to fix: the keys and the NOT NULL columns must still apply."""
    _migrate(old)
    assert old.execute(
        "SELECT count(*) AS n FROM pg_constraint WHERE contype = 'f' AND convalidated IS FALSE"
    ).fetchone() == {'n': 0}


# ----------------------------------------------------------------------------------------------- the trigger and keys


def _chain(db):
    """Ada in her own organization with a list, a row and a chat; and Bo in another one."""
    ada, bo = _user(db), _user(db)
    org = str(db.execute('SELECT default_organization(%s) AS o', (ada,)).fetchone()['o'])
    other = str(db.execute('SELECT default_organization(%s) AS o', (bo,)).fetchone()['o'])
    pid = project(db, ada)
    lid = _insert(db, 'lists', organization_id=org, project_id=pid, user_id=ada, name='l', entity='person')
    return ada, bo, org, other, pid, lid


def test_a_child_inserted_without_an_organization_takes_the_one_of_its_parent(db):
    ada, _, org, _, pid, lid = _chain(db)
    row = _insert(db, 'list_rows', list_id=lid)
    charge = _insert(db, 'row_charges', row_id=row, keys=['email'], credits=1, state='held')
    chat = _insert(db, 'chat_sessions', organization_id=org, project_id=pid, user_id=ada)
    msg = _insert(db, 'chat_messages', session_id=chat, role='user')
    campaign = _insert(db, 'outreach_campaigns', organization_id=org, project_id=pid, owner_id=ada, name='c')
    target = _insert(db, 'outreach_campaign_targets', campaign_id=campaign, owner_id=ada, email='t@x.test')
    inbound = _insert(
        db,
        'outreach_inbound_messages',
        project_id=pid,
        owner_id=ada,
        channel='email',
        kind='reply',
        provider_message_id='m',
    )
    watch = _insert(
        db, 'watchlists', list_id=lid, name='w', job_titles=['x'], cadence='daily', next_run_at='2026-10-14'
    )
    signal = _insert(
        db, 'signals', watchlist_id=watch, row_id=row, type='job_posting', key='k', score=1, occurred_at='2026-10-13'
    )
    for table, id_ in [
        ('list_rows', row),
        ('row_charges', charge),
        ('chat_messages', msg),
        ('outreach_campaign_targets', target),
        ('outreach_inbound_messages', inbound),
        ('watchlists', watch),
        ('signals', signal),
    ]:
        assert _org_of(db, table, 'id', id_) == org, table


def test_a_lookup_claim_inserted_by_the_app_takes_the_organization_of_its_job(db):
    ada, _, org, other, _, lid = _chain(db)
    row = _insert(db, 'list_rows', list_id=lid)
    job = _insert(db, 'jobs', organization_id=org, user_id=ada, status='running', list_id=lid)
    assert lookup_claims.claim(db, job, 'call', lid, 'email', [row]) == 1
    assert _org_of(db, 'lookup_claims', 'job_id', job) == org
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO lookup_claims (job_id, tool_call_id, row_id, key, organization_id) VALUES (%s, 'x', %s, 'phone', %s)",
            (job, row, other),
        )


def test_a_parent_that_is_not_there_is_a_foreign_key_violation(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert(db, 'list_rows', list_id=str(uuid.uuid4()))


def test_a_child_cannot_name_another_organization_than_its_parent(db):
    _, _, _, other, _, lid = _chain(db)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert(db, 'list_rows', list_id=lid, organization_id=other)


def test_changing_the_parent_column_moves_the_row_to_the_new_parents_organization(db):
    _, bo, _, other, _, lid = _chain(db)
    bo_list = _insert(
        db, 'lists', organization_id=other, project_id=project(db, bo), user_id=bo, name='b', entity='person'
    )
    col = db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, 'k', 'K', 'static', 0)"
        ' RETURNING list_id',
        (lid,),
    ).fetchone()
    db.execute('UPDATE list_columns SET list_id = %s WHERE list_id = %s', (bo_list, col['list_id']))
    assert db.execute('SELECT organization_id::text AS o FROM list_columns').fetchone()['o'] == other


def test_a_set_null_key_nulls_its_own_column_and_keeps_the_organization(db):
    ada, _, org, _, pid, lid = _chain(db)
    campaign = _insert(
        db, 'outreach_campaigns', organization_id=org, project_id=pid, owner_id=ada, name='c', list_id=lid
    )
    chat = _insert(db, 'chat_sessions', organization_id=org, project_id=pid, user_id=ada)
    db.execute('UPDATE lists SET chat_session_id = %s WHERE id = %s', (chat, lid))

    db.execute('DELETE FROM lists WHERE id = %s', (lid,))
    db.execute('DELETE FROM chat_sessions WHERE id = %s', (chat,))

    row = db.execute(
        'SELECT list_id, organization_id::text AS o FROM outreach_campaigns WHERE id = %s', (campaign,)
    ).fetchone()
    assert row == {'list_id': None, 'o': org}


def test_deleting_a_campaign_reaches_its_targets_and_sends_whose_owner_is_gone(db):
    """The owner key skips a row whose owner is NULL, so the key on the organization alone must cascade."""
    ada, _, org, _, pid, _ = _chain(db)
    campaign = _insert(db, 'outreach_campaigns', organization_id=org, project_id=pid, owner_id=ada, name='c')
    target = _insert(db, 'outreach_campaign_targets', campaign_id=campaign, owner_id=ada, email='t@x.test')
    send = _insert(
        db,
        'outreach_campaign_sends',
        campaign_id=campaign,
        owner_id=ada,
        target_id=target,
        step=1,
        channel='email',
        recipient='x@y.test',
        idempotency_key='k',
    )
    db.execute('UPDATE outreach_campaign_targets SET owner_id = NULL')
    db.execute('UPDATE outreach_campaign_sends SET owner_id = NULL')

    db.execute('DELETE FROM outreach_campaigns WHERE id = %s', (campaign,))

    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone() == {'n': 0}
    assert _org_of(db, 'outreach_campaign_sends', 'id', send) is None


def test_deleting_the_owner_keeps_the_children_of_the_campaign(db):
    """ON UPDATE CASCADE of the owner key nulls owner_id on the children, as before the organization joined it."""
    ada, _, org, _, pid, _ = _chain(db)
    campaign = _insert(db, 'outreach_campaigns', organization_id=org, project_id=pid, owner_id=ada, name='c')
    target = _insert(db, 'outreach_campaign_targets', campaign_id=campaign, owner_id=ada, email='t@x.test')

    db.execute('UPDATE outreach_campaigns SET owner_id = NULL WHERE id = %s', (campaign,))

    row = db.execute(
        'SELECT owner_id, organization_id::text AS o FROM outreach_campaign_targets WHERE id = %s', (target,)
    ).fetchone()
    assert row == {'owner_id': None, 'o': org}
