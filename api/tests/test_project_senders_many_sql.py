"""Several mailbox accounts of one provider in a client (#935): the migration on the links that exist, and the
links that connecting, linking and a member's removal leave. Needs TEST_DATABASE_URL."""

import os
import shutil
import uuid
from datetime import timedelta

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.auth.middleware import CurrentUser
from app.core.migrate import MIGRATIONS, migrate
from app.integrations import project_senders, token_crypto
from app.linkedin import api as linkedin_api
from app.outreach import sender_pool
from tests import test_dispatcher_sql, test_sender_pool_sql
from tests.conftest import COPY_PREFIX, call_as, conninfo_of, member, project, scoped_conn
from tests.test_dispatcher_sql import DB_URL, GMAIL_SEND, NOW, TWO_STEPS, _beat, _campaign, _mailbox
from tests.test_sender_pool_race_sql import Side, _waiting
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches
linking = test_sender_pool_sql.linking

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: project senders SQL test NOT run')

NAME = '20261018205118_project_senders_many.sql'
KEPT = ('project_senders', 'outreach_campaign_senders', 'outreach_campaign_targets', 'user_oauth_connections')


@pytest.fixture(scope='module')
def before(tmp_path_factory):
    url = os.environ['TEST_DATABASE_URL']
    older = tmp_path_factory.mktemp('older')
    for p in MIGRATIONS.glob('*.sql'):
        if p.name < NAME:
            shutil.copy(p, older / p.name)
    name = f'{COPY_PREFIX}before_many_{uuid.uuid4().hex[:10]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name}')
    with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True) as c:
        migrate(c, older)
    yield name
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


@pytest.fixture(scope='module')
def upto(tmp_path_factory):
    directory = tmp_path_factory.mktemp('upto')
    for p in MIGRATIONS.glob('*.sql'):
        if p.name <= NAME:
            shutil.copy(p, directory / p.name)
    return directory


@pytest.fixture
def old(before):
    url = os.environ['TEST_DATABASE_URL']
    name = f'{COPY_PREFIX}many_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name} TEMPLATE {before}')
    try:
        with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True, row_factory=dict_row) as c:
            yield c
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


def _insert(c, table, **cols) -> str:
    sql = f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({", ".join(["%s"] * len(cols))}) RETURNING id'
    return str(c.execute(sql, list(cols.values())).fetchone()['id'])


def _seed(c) -> dict[str, str]:
    """Two clients as the old schema keeps them: one account of each provider, a pool row and a pinned thread."""
    user = _user(c)
    org = str(c.execute('SELECT default_organization(%s) AS o', (user,)).fetchone()['o'])
    ids = {'org': org}
    for key, provider in (('google', 'google'), ('microsoft', 'microsoft'), ('linkedin', 'unipile'), ('g2', 'google')):
        ids[key] = _insert(
            c,
            'user_oauth_connections',
            organization_id=org,
            user_id=user,
            provider=provider,
            refresh_token_encrypted=f'token-{key}',
            provider_account_id=f'acct-{key}',
            account_label=f'{key}@x.test',
            status='connected',
        )
    ids['client'], ids['other'] = (
        _insert(c, 'projects', organization_id=org, user_id=user, name=n) for n in ('client', 'other')
    )
    for client, account in (('client', 'google'), ('client', 'microsoft'), ('client', 'linkedin'), ('other', 'g2')):
        c.execute(
            'INSERT INTO project_senders (project_id, sender_id, provider, organization_id)'
            ' SELECT %s, id, provider, organization_id FROM user_oauth_connections WHERE id = %s',
            (ids[client], ids[account]),
        )
    ids['campaign'] = _insert(
        c,
        'outreach_campaigns',
        organization_id=org,
        project_id=ids['client'],
        owner_id=user,
        name='c',
        sender_pool=['google'],
        status='active',
    )
    c.execute(
        'INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)'
        " VALUES (%s, %s, %s, 'google', %s)",
        (ids['campaign'], ids['google'], ids['client'], org),
    )
    _insert(
        c,
        'outreach_campaign_targets',
        campaign_id=ids['campaign'],
        owner_id=user,
        email='anna@lead.test',
        status='active',
        sender_provider='google',
        sender_id=ids['google'],
        sender_pinned_at=NOW,
    )
    return ids


def _rows(c, table) -> list[str]:
    return [r['j'] for r in c.execute(f'SELECT to_jsonb(x)::text AS j FROM {table} x ORDER BY 1').fetchall()]


def _link(c, ids, client, provider, key) -> None:
    sid = _insert(
        c,
        'user_oauth_connections',
        organization_id=ids['org'],
        provider=provider,
        refresh_token_encrypted='t',
        provider_account_id=f'acct-{key}',
        status='connected',
    )
    c.execute(
        'INSERT INTO project_senders (project_id, sender_id, provider, organization_id) VALUES (%s, %s, %s, %s)',
        (ids[client], sid, provider, ids['org']),
    )


def test_the_migration_keeps_every_link_pool_row_and_pin(old, upto):
    _seed(old)
    kept = {table: _rows(old, table) for table in KEPT}

    assert migrate(old, upto) == [NAME]

    for table in KEPT:
        assert kept[table], table
        assert _rows(old, table) == kept[table], table


def test_after_the_migration_a_client_adds_mailboxes_and_keeps_one_linkedin_account(old, upto):
    ids = _seed(old)
    assert migrate(old, upto) == [NAME]

    _link(old, ids, 'client', 'google', 'g3')
    _link(old, ids, 'client', 'microsoft', 'm2')
    with pytest.raises(psycopg.errors.UniqueViolation) as refused:
        _link(old, ids, 'client', 'unipile', 'l2')
    assert refused.value.diag.constraint_name == 'project_senders_single_provider_key'
    _link(old, ids, 'other', 'unipile', 'l3')

    counts = old.execute(
        'SELECT provider, count(*) AS n FROM project_senders WHERE project_id = %s GROUP BY provider ORDER BY 1',
        (ids['client'],),
    ).fetchall()
    assert [(r['provider'], r['n']) for r in counts] == [('google', 2), ('microsoft', 2), ('unipile', 1)]


def test_the_migration_runs_on_an_empty_schema(old, upto):
    assert migrate(old, upto) == [NAME]


def _google(user, pid, account: str, label: str):
    return call_as(
        user,
        project_senders.attach,
        user.id,
        pid,
        'google',
        account=account,
        cols={
            'refresh_token_encrypted': token_crypto.encrypt('refresh-1').value,
            'granted_scopes': ['openid', GMAIL_SEND],
            'account_label': label,
            'provider_account_id': account,
        },
    )


def _links(c, pid) -> set[str]:
    rows = c.execute('SELECT sender_id::text FROM project_senders WHERE project_id = %s', (pid,)).fetchall()
    return {r['sender_id'] for r in rows}


def _pool(c, cid) -> set[str]:
    rows = c.execute('SELECT sender_id::text FROM outreach_campaign_senders WHERE campaign_id = %s', (cid,))
    return {r['sender_id'] for r in rows}


def _pin(c, email) -> str | None:
    row = c.execute('SELECT sender_id::text FROM outreach_campaign_targets WHERE email = %s', (email,)).fetchone()
    return row['sender_id']


def _colleague(c, owner: str) -> str:
    """A member of ``owner``'s organization."""
    uid = _user(c)
    c.execute(
        """INSERT INTO member ("organizationId", "userId", role, "createdAt")
           VALUES (default_organization(%s), %s, 'member', now())""",
        (owner, uid),
    )
    return uid


def _in_org_of(c, uid: str, owner: str) -> CurrentUser:
    return CurrentUser(id=uid, org_id=member(c, owner).org_id, org_role='member')


def _other_client(c, owner: str) -> str:
    return c.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'B') RETURNING id",
        (owner, owner),
    ).fetchone()['id']


@pytest.mark.parametrize('second', ['connect', 'colleague_connects', 'link'])
async def test_a_second_google_account_joins_the_client_and_the_first_keeps_its_picks_and_conversations(
    db, provider, switches, linking, second
):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    assert _google(user, pid, 'sub-a', 'me@acme.test').is_ok()
    (a,) = _links(db, pid)
    cid = _campaign(db, uid, TWO_STEPS)
    sender_pool.pick(db, cid, [a])
    _heard_step_one(db, 'anna@client.test', a, NOW - timedelta(days=4))

    if second == 'connect':
        assert _google(user, pid, 'sub-b', 'b@acme.test').is_ok()
    elif second == 'colleague_connects':
        bo = _colleague(db, uid)
        assert _google(_in_org_of(db, bo, uid), pid, 'sub-b', 'b@acme.test').is_ok()
    else:
        b = _mailbox(db, uid, account_label='b@acme.test', projects=[_other_client(db, uid)])
        assert call_as(user, project_senders.link, user, b, pid).is_ok()
    await _beat(NOW + timedelta(minutes=20))

    (b,) = _links(db, pid) - {a}
    assert _links(db, pid) == {a, b}
    assert _pool(db, cid) == {a}
    assert _pin(db, 'anna@client.test') == a
    assert [(m['To'], m['From']) for m, _ in provider.sent] == [('anna@client.test', 'me@acme.test')]


def test_linking_or_connecting_a_mailbox_the_client_has_keeps_its_links_and_pools(db, provider, linking):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    assert _google(user, pid, 'sub-a', 'me@acme.test').is_ok()
    assert _google(user, pid, 'sub-b', 'b@acme.test').is_ok()
    links = _links(db, pid)
    a = str(db.execute("SELECT id FROM user_oauth_connections WHERE provider_account_id = 'sub-a'").fetchone()['id'])
    cid = _campaign(db, uid, TWO_STEPS)
    _heard_step_one(db, 'anna@client.test', a, NOW - timedelta(days=4))
    sender_pool.pick(db, cid, list(links - {a}))  # Anna's conversation on A is held until someone picks A again

    for sid in links:
        assert call_as(user, project_senders.link, user, sid, pid).is_ok()
    assert _google(user, pid, 'sub-a', 'renamed@acme.test').is_ok()

    assert _links(db, pid) == links
    assert _pool(db, cid) == links - {a}
    assert db.execute('SELECT count(*) AS n FROM user_oauth_connections').fetchone()['n'] == 2


def test_connecting_a_mailbox_waits_for_no_other_mailbox_of_the_client(db, provider, linking):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    assert _google(user, pid, 'sub-a', 'me@acme.test').is_ok()
    (a,) = _links(db, pid)
    connect = Side(user, _google, user, pid, 'sub-b', 'b@acme.test')
    with psycopg.connect(conninfo_of(db), row_factory=dict_row) as sending:
        sending.execute('SELECT 1 FROM user_oauth_connections WHERE id = %s FOR UPDATE', (a,))  # a send's pacing slot
        connect.start()
        connect.join(5)
        assert not connect.is_alive(), 'connecting B waited on A'
    assert connect.error is None and connect.result.is_ok(), connect.result
    assert len(_links(db, pid)) == 2


def test_two_first_connects_of_one_account_end_with_one_row_in_the_client(db, provider):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    connect = Side(user, _google, user, pid, 'sub-a', 'me@acme.test')
    with psycopg.connect(conninfo_of(db), row_factory=dict_row) as rival:
        rival.execute(
            'INSERT INTO user_oauth_connections (organization_id, user_id, provider, provider_account_id,'
            " account_label, refresh_token_encrypted, status) VALUES (%s, %s, 'google', 'sub-a', 'me@acme.test', 't', 'connected')",
            (user.org_id, uid),
        )
        connect.start()
        _waiting(db, connect)  # it chose a new row before the rival's commit
        rival.commit()
    connect.join(5)

    assert connect.error is None and connect.result.is_ok(), connect.error or connect.result
    assert db.execute('SELECT count(*) AS n FROM user_oauth_connections').fetchone()['n'] == 1
    assert len(_links(db, pid)) == 1


def _linkedin(c, uid, pid, account_id):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(linkedin_api, 'get_conn', scoped_conn(c))
        return call_as(member(c, uid), linkedin_api._store, uid, pid, account_id, name='Ada', premium=False)


def test_a_second_linkedin_account_replaces_the_first_and_linkedin_finds_the_one(db, provider, linking, monkeypatch):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    assert _google(user, pid, 'sub-a', 'me@acme.test').is_ok()
    assert _google(user, pid, 'sub-b', 'b@acme.test').is_ok()
    assert _linkedin(db, uid, pid, 'acc-1').is_ok()

    assert _linkedin(db, uid, pid, 'acc-2').is_ok()

    rows = db.execute(
        'SELECT u.provider, u.provider_account_id AS acc FROM project_senders ps'
        ' JOIN user_oauth_connections u ON u.id = ps.sender_id WHERE ps.project_id = %s ORDER BY 1, 2',
        (pid,),
    ).fetchall()
    assert [(r['provider'], r['acc']) for r in rows] == [('google', 'sub-a'), ('google', 'sub-b'), ('unipile', 'acc-2')]
    monkeypatch.setattr(linkedin_api, 'get_conn', scoped_conn(db))
    assert call_as(user, linkedin_api.connected_account_id, pid) == 'acc-2'


async def test_a_removed_member_leaves_the_client_and_its_pools_and_their_conversations_stay_held(
    db, provider, switches, linking
):
    uid = _user(db)
    bo = _colleague(db, uid)
    user, pid = member(db, uid), project(db, uid)
    assert _google(user, pid, 'sub-a', 'me@acme.test').is_ok()
    assert _google(_in_org_of(db, bo, uid), pid, 'sub-b', 'bo@acme.test').is_ok()
    a = str(db.execute("SELECT id FROM user_oauth_connections WHERE provider_account_id = 'sub-a'").fetchone()['id'])
    b = str(db.execute("SELECT id FROM user_oauth_connections WHERE provider_account_id = 'sub-b'").fetchone()['id'])
    cid = _campaign(db, uid, TWO_STEPS, emails=('anna@client.test', 'bea@client.test'))
    sender_pool.pick(db, cid, [a, b])
    _heard_step_one(db, 'anna@client.test', a, NOW - timedelta(days=4))
    _heard_step_one(db, 'bea@client.test', b, NOW - timedelta(days=4))

    db.execute('DELETE FROM member WHERE "userId" = %s', (bo,))
    await _beat(NOW + timedelta(minutes=20))
    await _beat(NOW + timedelta(minutes=40))

    assert _links(db, pid) == {a}
    assert _pool(db, cid) == {a}
    assert _pin(db, 'bea@client.test') == b
    assert [(m['To'], m['From']) for m, _ in provider.sent] == [('anna@client.test', 'me@acme.test')]
