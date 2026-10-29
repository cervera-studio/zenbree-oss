"""Row-level security on real rows: what zenbree_app sees and may write in each scope (PR 5 of the
organization-scoping run).

Every case runs raw SQL, `SET LOCAL ROLE zenbree_app` and `zb.enter_member` or `zb.enter_org`, inside one
transaction: the settings are transaction-local, so outside it the scope is gone. No app code runs: PR 6 moves the
pool onto the roles. test_tenant_catalog_sql.py proves every table carries the policy its shape calls for, so these
cases cover each shape once, not each table.

The seed: U is a member of A and B, V of A only, W of B only. Each organization has a project, a list and a saved
search of U.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors

from tests.test_agent import db as db
from tests.test_agent import needs_db

pytestmark = needs_db

SECRET_TABLES = ('jwks', 'account', 'session', 'verification', 'schema_migrations')


@contextmanager
def scoped(db, role='zenbree_app', member=None, org=None):
    """One transaction as `role`, entered as Member(org, user) or ForOrg(org), or not entered at all."""
    with db.transaction():
        db.execute(f'SET LOCAL ROLE {role}')
        if member:
            db.execute('SELECT zb.enter_member(%s, %s)', member)
        elif org:
            db.execute('SELECT zb.enter_org(%s)', (org,))
        yield db


def refused(db, sql, params=(), error=errors.InsufficientPrivilege):
    """The statement fails with `error`, and the transaction around it goes on."""
    with pytest.raises(error), db.transaction():
        db.execute(sql, params)


def _user(db, name):
    return db.execute(
        'INSERT INTO "user" (name, email, "emailVerified") VALUES (%s, %s, true) RETURNING id', (name, f'{name}@x.test')
    ).fetchone()['id']


def _org(db, name, *members):
    org = db.execute(
        'INSERT INTO organization (name, slug, "createdAt") VALUES (%s, %s, now()) RETURNING id', (name, name)
    ).fetchone()['id']
    for user in members:
        db.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'admin\', now())',
            (org, user),
        )
    return org


def _search(db, org, user):
    project = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'p') RETURNING id", (org, user)
    ).fetchone()['id']
    lst = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, 'l', 'company')"
        ' RETURNING id',
        (org, project, user),
    ).fetchone()['id']
    search = db.execute(
        'INSERT INTO saved_searches (user_id, name, tool_name, tool_input, list_id, cadence, next_run_at)'
        " VALUES (%s, 's', 'search_companies', '{}', %s, 'daily', now()) RETURNING id",
        (user, lst),
    ).fetchone()['id']
    return lst, search


@pytest.fixture
def world(db):
    u, v, w = _user(db, 'u'), _user(db, 'v'), _user(db, 'w')
    a, b = _org(db, 'org-a', u, v), _org(db, 'org-b', u, w)
    (list_a, search_a), (list_b, search_b) = _search(db, a, u), _search(db, b, u)
    return {
        'u': u,
        'v': v,
        'w': w,
        'a': a,
        'b': b,
        'list_a': list_a,
        'list_b': list_b,
        'search_a': search_a,
        'search_b': search_b,
    }


def ids(db, sql, params=()):
    return {r['id'] for r in db.execute(sql, params).fetchall()}


def test_the_roles_have_exactly_their_attributes(db):
    """Catches: a role made earlier with other attributes, kept by the migration's "already exists" branch: a
    zenbree_app with BYPASSRLS reads every organization. A membership of either role is R9's, in
    test_tenant_catalog_sql.py."""
    roles = {r['rolname']: r for r in db.execute("SELECT * FROM pg_roles WHERE rolname LIKE 'zenbree\\_%'")}
    off = ('rolsuper', 'rolcanlogin', 'rolcreatedb', 'rolcreaterole', 'rolinherit', 'rolreplication')
    assert {k: roles['zenbree_app'][k] for k in (*off, 'rolbypassrls')} == dict.fromkeys((*off, 'rolbypassrls'), False)
    assert {k: roles['zenbree_system'][k] for k in off} == dict.fromkeys(off, False)
    assert roles['zenbree_system']['rolbypassrls'] is True


def test_a_member_reads_only_their_organizations_rows_whatever_the_query_says(db, world):
    """#701 at the SQL level: the query names the user and no organization, and still returns A's search only."""
    with scoped(db, member=(world['a'], world['u'])):
        assert ids(db, 'SELECT id FROM saved_searches WHERE user_id = %s', (world['u'],)) == {world['search_a']}
        assert ids(db, 'SELECT id FROM lists') == {world['list_a']}
    with scoped(db, member=(world['b'], world['u'])):
        assert ids(db, 'SELECT id FROM saved_searches WHERE user_id = %s', (world['u'],)) == {world['search_b']}


def test_a_temporary_table_cannot_forge_a_membership(db, world):
    """Catches: zb.is_live_member reading member through the search path, where a temporary table comes first."""
    with scoped(db):
        db.execute('CREATE TEMP TABLE member AS SELECT * FROM public.member WITH NO DATA')
        db.execute(
            'INSERT INTO pg_temp.member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'admin\', now())',
            (world['a'], world['w']),
        )
        with pytest.raises(psycopg.Error) as caught, db.transaction():
            db.execute('SELECT zb.enter_member(%s, %s)', (world['a'], world['w']))
    assert caught.value.sqlstate == 'ZB401'


def test_a_temporary_table_cannot_forge_a_role(db, world):
    """Catches: zb.enter_member reading its role from member through the search path. zb.is_live_member still decides
    who enters, so the forge shows only in the role it returns, which the request then acts on."""
    a, u = world['a'], world['u']
    db.execute('UPDATE member SET role = \'member\' WHERE "userId" = %s AND "organizationId" = %s', (u, a))
    with scoped(db):
        db.execute('CREATE TEMP TABLE member AS SELECT * FROM public.member WITH NO DATA')
        db.execute(
            'INSERT INTO pg_temp.member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'owner\', now())',
            (a, u),
        )
        assert db.execute('SELECT zb.enter_member(%s, %s) AS role', (a, u)).fetchone()['role'] == 'member'


def test_a_write_into_another_organization_is_refused(db, world):
    with scoped(db, member=(world['a'], world['u'])):
        # The fill trigger reads the parent under the caller's policies, so B's list is not there.
        refused(
            db,
            'INSERT INTO saved_searches (user_id, name, tool_name, tool_input, list_id, cadence, next_run_at)'
            " VALUES (%s, 'x', 'search_companies', '{}', %s, 'daily', now())",
            (world['u'], world['list_b']),
            errors.ForeignKeyViolation,
        )
        assert db.execute("UPDATE lists SET name = 'x' WHERE id = %s", (world['list_b'],)).rowcount == 0
        assert db.execute('DELETE FROM saved_searches WHERE id = %s', (world['search_b'],)).rowcount == 0
        refused(db, 'UPDATE lists SET organization_id = %s WHERE id = %s', (world['b'], world['list_a']))
    assert db.execute('SELECT name FROM lists WHERE id = %s', (world['list_b'],)).fetchone()['name'] == 'l'


def test_an_insert_takes_its_organization_from_the_scope_or_the_parent(db, world):
    with scoped(db, member=(world['a'], world['u'])):
        project = db.execute(
            "INSERT INTO projects (user_id, name) VALUES (%s, 'p2') RETURNING organization_id", (world['u'],)
        ).fetchone()
        search = db.execute(
            'INSERT INTO saved_searches (user_id, name, tool_name, tool_input, list_id, cadence, next_run_at)'
            " VALUES (%s, 'y', 'search_companies', '{}', %s, 'daily', now()) RETURNING organization_id",
            (world['u'], world['list_a']),
        ).fetchone()
    assert project['organization_id'] == world['a']  # DEFAULT zb.org()
    assert search['organization_id'] == world['a']  # the fill trigger, from the list
    # As the superuser with no scope the default is NULL, so an insert that names no organization still fails.
    refused(db, "INSERT INTO projects (user_id, name) VALUES (%s, 'p3')", (world['u'],), errors.NotNullViolation)


def test_a_removed_or_banned_member_cannot_enter(db, world):
    a, u, v = world['a'], world['u'], world['v']
    with scoped(db, member=(a, v)):
        pass
    for banned, expires in ((True, None), (True, datetime.now(UTC) + timedelta(days=1))):
        db.execute('UPDATE "user" SET banned = %s, "banExpires" = %s WHERE id = %s', (banned, expires, v))
        with pytest.raises(psycopg.Error) as caught, scoped(db, member=(a, v)):
            pass
        assert caught.value.sqlstate == 'ZB401'
    db.execute('UPDATE "user" SET "banExpires" = %s WHERE id = %s', (datetime.now(UTC) - timedelta(days=1), v))
    with scoped(db, member=(a, v)):  # a ban that ended
        pass
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (u, a))
    with pytest.raises(psycopg.Error) as caught, scoped(db, member=(a, u)):
        pass
    assert caught.value.sqlstate == 'ZB401'


def test_no_scope_sees_nothing_and_writes_nothing(db, world):
    tenant = [
        r['t']
        for r in db.execute(
            "SELECT oid::regclass::text AS t FROM pg_class WHERE relnamespace = 'public'::regnamespace"
            " AND relkind IN ('r', 'p') AND relrowsecurity ORDER BY 1"
        )
    ]
    seeded = ('saved_searches', 'lists', 'projects')
    assert all(db.execute(f'SELECT count(*) AS n FROM {t}').fetchone()['n'] > 0 for t in seeded)
    assert len(tenant) > 40
    with scoped(db):
        assert {t: db.execute(f'SELECT count(*) AS n FROM {t}').fetchone()['n'] for t in tenant} == dict.fromkeys(
            tenant, 0
        )
        refused(
            db, "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'p')", (world['a'], world['u'])
        )
    # A scope entered in one transaction is gone in the next.
    with scoped(db, member=(world['a'], world['u'])):
        pass
    with scoped(db):
        assert db.execute('SELECT count(*) AS n FROM lists').fetchone()['n'] == 0


def test_the_secret_tables_are_refused(db):
    with scoped(db):
        for table in SECRET_TABLES:
            refused(db, f'SELECT 1 FROM {table} LIMIT 1')


def test_the_app_writes_only_the_columns_of_unscoped_tables_it_uses(db, world):
    """Catches: a table-wide UPDATE on "user" or organization, which no policy limits: any request could make every
    user an admin or lift a ban. The writes and row locks the app does still work."""
    u, a = world['u'], world['a']
    with scoped(db, member=(a, u)):
        refused(db, 'UPDATE "user" SET role = \'admin\', banned = false')
        # A membership moved or made admin would pass zb.enter_member for an organization the person is not in.
        refused(db, 'UPDATE member SET role = \'owner\', "organizationId" = %s', (world['b'],))
        refused(db, 'DELETE FROM member')
        refused(db, "UPDATE organization SET name = 'x' WHERE id = %s", (a,))
        refused(db, "UPDATE settings SET value = 'true'")
        refused(db, 'DELETE FROM llm_usage')
        refused(db, 'SELECT 1 FROM webhook_events')
        assert db.execute('UPDATE "user" SET locale = %s WHERE id = %s', ('en', u)).rowcount == 1
        db.execute('SELECT id FROM "user" WHERE id = %s FOR NO KEY UPDATE', (u,))
        db.execute('SELECT id FROM organization WHERE id = %s FOR UPDATE', (a,))
        db.execute('SELECT 1 FROM organization WHERE id = %s FOR NO KEY UPDATE', (a,))


def test_a_teammates_person_rows_are_readable_and_writable(db, world):
    """Interrogate Act on 3: notify() writes a teammate's notification and the urgent-slot upsert of their
    settings, as ForOrg from a beat task and as Member from a request."""
    a, v, w = world['a'], world['v'], world['w']
    notify = (
        "INSERT INTO notifications (organization_id, user_id, kind, href) VALUES (%s, %s, 'reply', '/x') RETURNING id"
    )
    upsert = (
        'INSERT INTO notification_settings (user_id, last_urgent_email_at) VALUES (%s, now())'
        ' ON CONFLICT (user_id) DO UPDATE SET last_urgent_email_at = now() RETURNING user_id'
    )
    for scope in ({'org': a}, {'member': (a, world['u'])}):
        with scoped(db, **scope):
            assert db.execute(notify, (a, v)).fetchone()['id']
            assert db.execute(upsert, (v,)).fetchone()['user_id'] == v
            # #412: W is not a member of A.
            refused(db, notify, (a, w))
            refused(db, upsert, (w,))
    with scoped(db, member=(a, v)):
        assert db.execute('SELECT count(*) AS n FROM notifications WHERE user_id = %s', (v,)).fetchone()['n'] == 2


def test_the_bell_lists_the_persons_notifications_in_every_live_organization(db, world):
    """Today's bell (notifications/service.py, VISIBLE_SQL): a person sees their notifications of every organization
    where they are a member, whichever one is active, and a billing notice links to /billing/switch. Anyone else's
    stay inside the current organization."""
    a, b, u, v, w = world['a'], world['b'], world['u'], world['v'], world['w']
    notify = (
        "INSERT INTO notifications (organization_id, user_id, kind, href) VALUES (%s, %s, 'reply', '/x') RETURNING id"
    )
    in_a, in_b, ws = (db.execute(notify, args).fetchone()['id'] for args in ((a, u), (b, u), (b, w)))
    mine = 'SELECT id FROM notifications WHERE user_id = %s'
    with scoped(db, member=(a, u)):
        assert ids(db, mine, (u,)) == {in_a, in_b}
        assert ids(db, 'SELECT id FROM notifications') == {in_a, in_b}  # not W's, of B
        # Marking read, as notifications/routes.py does, reaches B's row from A.
        assert db.execute('UPDATE notifications SET read_at = now() WHERE user_id = %s', (u,)).rowcount == 2
        # A write for anyone else stays inside A: W is a live member of B, not of A.
        refused(db, notify, (b, w))
        assert db.execute('UPDATE notifications SET read_at = now() WHERE id = %s', (ws,)).rowcount == 0
        # #412: a notification in A for W, who is not a member of A.
        refused(db, notify, (a, w))
        # The person's own row cannot move to an organization where they are not a member.
        c = _org(db, 'org-c', v)
        refused(db, 'UPDATE notifications SET organization_id = %s WHERE id = %s', (c, in_b))
    with scoped(db, member=(a, v)):
        assert ids(db, 'SELECT id FROM notifications') == {in_a}  # a teammate's, of A only
    db.execute('UPDATE "user" SET banned = true WHERE id = %s', (w,))
    with scoped(db, org=b):
        assert ids(db, 'SELECT id FROM notifications') == {in_b}  # a banned member's are out of reach
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (u, b))
    with scoped(db, member=(a, u)):
        assert ids(db, mine, (u,)) == {in_a}  # #323: B's notice is no longer U's to see


def test_the_memory_document_is_the_persons_alone(db, world):
    db.execute("INSERT INTO user_memory_doc (user_id, body) VALUES (%s, 'mine')", (world['u'],))
    for org in (world['a'], world['b']):
        with scoped(db, member=(org, world['u'])):
            assert db.execute('SELECT body FROM user_memory_doc').fetchone()['body'] == 'mine'
    with scoped(db, member=(world['a'], world['v'])):
        assert db.execute('SELECT count(*) AS n FROM user_memory_doc').fetchone()['n'] == 0
    with scoped(db, org=world['a']):
        assert db.execute('SELECT count(*) AS n FROM user_memory_doc').fetchone()['n'] == 0


# hubspot._store, as it runs today.
STORE = """INSERT INTO user_oauth_connections
               (user_id, organization_id, provider, refresh_token_encrypted, granted_scopes, account_label,
                provider_account_id, provider_owner_id, status, status_detail, connected_at)
           VALUES (%s, %s, 'hubspot', 'enc', '{}', NULL, NULL, NULL, 'connected', NULL, now())
           ON CONFLICT (user_id) WHERE provider = 'hubspot' DO UPDATE SET
               organization_id = EXCLUDED.organization_id, refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
               status = 'connected', status_detail = NULL, connected_at = now()
           RETURNING id"""
HUBSPOT = "SELECT id, organization_id FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot'"


def test_a_hubspot_grant_follows_its_user_across_their_organizations(db, world):
    a, b, u = world['a'], world['b'], world['u']
    mailbox = db.execute(
        'INSERT INTO user_oauth_connections (user_id, organization_id, provider, refresh_token_encrypted)'
        " VALUES (%s, %s, 'google', 'enc') RETURNING id",
        (u, b),
    ).fetchone()['id']
    with scoped(db, member=(b, u)):
        grant = db.execute(STORE, (u, b)).fetchone()['id']
    with scoped(db, member=(a, u)):
        # The row sits in B, and the reconnect from A moves it: ON CONFLICT would raise on a row it cannot see.
        assert db.execute(STORE, (u, a)).fetchone()['id'] == grant
        assert ids(db, 'SELECT id FROM user_oauth_connections') == {grant}  # B's mailbox stays out of sight
    for scope in ({'member': (b, u)}, {'org': b}, {'org': a}, {'member': (a, world['v'])}):
        with scoped(db, **scope):
            assert db.execute(HUBSPOT, (u,)).fetchone() == {'id': grant, 'organization_id': a}
    # A write in place, as crm/gate.py does when HubSpot refuses the token: the owner from any of their
    # organizations, anyone else only inside zb.org().
    for scope in ({'member': (b, u)}, {'org': a}, {'member': (a, world['v'])}):
        with scoped(db, **scope):
            assert db.execute("UPDATE user_oauth_connections SET status = 'error' WHERE id = %s", (grant,)).rowcount
    with scoped(db, org=b):  # zb_hubspot_owner_updates: not the owner, and the grant is stored in A
        assert not db.execute("UPDATE user_oauth_connections SET status = 'error' WHERE id = %s", (grant,)).rowcount
    with scoped(db, member=(b, world['w'])):
        assert db.execute(HUBSPOT, (u,)).fetchone()  # U is a live member of B
    # A new grant must land where its owner is a member: W is not one of A.
    with scoped(db, member=(b, world['w'])):
        refused(db, STORE, (world['w'], a))
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (u, b))
    with scoped(db, org=b):
        assert db.execute(HUBSPOT, (u,)).fetchone() is None
        assert db.execute('SELECT id FROM user_oauth_connections WHERE id = %s', (mailbox,)).fetchone()


def test_a_teammate_reads_the_owners_grant_but_writes_it_only_in_their_own_organization(db, world):
    """Catches: a teammate V (a member of A only) moving U's grant to B, or writing it where it sits in B. The read
    stays open: a campaign of A needs its owner's grant wherever the grant sits (outreach/routes.py activate)."""
    a, b, u, v = world['a'], world['b'], world['u'], world['v']
    with scoped(db, member=(a, u)):
        grant = db.execute(STORE, (u, a)).fetchone()['id']
    with scoped(db, member=(a, v)):
        refused(db, 'UPDATE user_oauth_connections SET organization_id = %s WHERE id = %s', (b, grant))
    with scoped(db, member=(b, u)):
        assert db.execute(STORE, (u, b)).fetchone()['id'] == grant  # the owner moves it, as a reconnect from B does
    with scoped(db, member=(a, v)):
        row = db.execute(
            "SELECT organization_id, refresh_token_encrypted FROM user_oauth_connections WHERE provider = 'hubspot'"
        ).fetchone()
        assert row == {'organization_id': b, 'refresh_token_encrypted': 'enc'}
        # zb_hubspot_owner_updates leaves the row out of reach: the update finds nothing.
        for change in ("status = 'error'", "refresh_token_encrypted = 'forged'"):
            assert not db.execute(f'UPDATE user_oauth_connections SET {change} WHERE id = %s', (grant,)).rowcount
    # A locking read applies the UPDATE policies too: the row V reads above is not there to lock.
    for scope in ({'member': (a, v)}, {'org': a}):
        with scoped(db, **scope):
            for lock in ('FOR UPDATE', 'FOR SHARE'):
                assert not ids(db, f'SELECT id FROM user_oauth_connections WHERE id = %s {lock}', (grant,))
    with scoped(db, member=(a, u)):  # the owner, scoped to A, writes it in place in B and moves it back
        assert ids(db, 'SELECT id FROM user_oauth_connections WHERE id = %s FOR UPDATE', (grant,)) == {grant}
        assert db.execute("UPDATE user_oauth_connections SET status = 'error' WHERE id = %s", (grant,)).rowcount
        db.execute('UPDATE user_oauth_connections SET organization_id = %s WHERE id = %s', (a, grant))
    assert db.execute(HUBSPOT, (u,)).fetchone() == {'id': grant, 'organization_id': a}


# hubspot.disconnect, as it runs today.
DISCONNECT = """UPDATE user_oauth_connections SET status = 'disconnected', status_detail = NULL,
                   refresh_token_encrypted = '', granted_scopes = '{}', crm_write_back = false,
                   crm_write_back_since = NULL
                WHERE user_id = %s AND provider = 'hubspot'"""


def test_a_teammate_cannot_take_move_or_delete_the_owners_grant(db, world):
    """Catches: a teammate V (a member of A only) taking U's grant, and U's refresh token, as their own, moving it into
    A, or deleting it. The read policy shows V the grant wherever it sits, and UPDATE and DELETE reach every row a read
    shows, so the restrictive policies and the trigger narrow them."""
    a, b, u, v = world['a'], world['b'], world['u'], world['v']
    with scoped(db, member=(b, u)):
        grant = db.execute(STORE, (u, b)).fetchone()['id']
    with scoped(db, member=(a, v)):
        assert db.execute(HUBSPOT, (u,)).fetchone()['id'] == grant  # the read stays open
        for change in (f"user_id = '{v}', organization_id = '{a}'", f"organization_id = '{a}'", f"user_id = '{v}'"):
            assert not db.execute(f'UPDATE user_oauth_connections SET {change} WHERE id = %s', (grant,)).rowcount
        assert not db.execute('DELETE FROM user_oauth_connections WHERE id = %s', (grant,)).rowcount
    with scoped(db, org=a):
        assert not db.execute(
            'UPDATE user_oauth_connections SET organization_id = %s WHERE id = %s', (a, grant)
        ).rowcount
        assert not db.execute('DELETE FROM user_oauth_connections WHERE id = %s', (grant,)).rowcount
    assert db.execute(HUBSPOT, (u,)).fetchone() == {'id': grant, 'organization_id': b}
    # Stored in A, the grant passes every policy for V, so the trigger keeps it U's.
    with scoped(db, member=(a, u)):
        db.execute(STORE, (u, a))
    with scoped(db, member=(a, v)):
        refused(db, 'UPDATE user_oauth_connections SET user_id = %s WHERE id = %s', (v, grant))
        # Through a mailbox: turn the grant into one, change its user, turn it back.
        refused(db, "UPDATE user_oauth_connections SET provider = 'google' WHERE id = %s", (grant,))
    assert db.execute('SELECT user_id, provider FROM user_oauth_connections WHERE id = %s', (grant,)).fetchone() == {
        'user_id': u,
        'provider': 'hubspot',
    }


def test_the_owner_still_moves_and_disconnects_their_grant(db, world):
    """The writes of the PR body's HubSpot list, under their scopes: _store from either organization, a write in place
    from the other one, and the disconnect. A member of A still writes A's mailboxes."""
    a, b, u, v = world['a'], world['b'], world['u'], world['v']
    with scoped(db, member=(a, u)):
        grant = db.execute(STORE, (u, a)).fetchone()['id']
    with scoped(db, member=(b, u)):
        assert db.execute(STORE, (u, b)).fetchone()['id'] == grant
    with scoped(db, member=(a, u)):
        assert db.execute('UPDATE user_oauth_connections SET organization_id = %s WHERE id = %s', (a, grant)).rowcount
    with scoped(db, member=(b, u)):  # stored in A, disconnected from B
        assert db.execute(DISCONNECT, (u,)).rowcount == 1
    with scoped(db, member=(b, u)):
        assert db.execute('DELETE FROM user_oauth_connections WHERE id = %s', (grant,)).rowcount == 1
    with scoped(db, member=(a, u)):
        mailbox = db.execute(
            'INSERT INTO user_oauth_connections (user_id, organization_id, provider, refresh_token_encrypted)'
            " VALUES (%s, %s, 'google', 'enc') RETURNING id",
            (u, a),
        ).fetchone()['id']
    with scoped(db, member=(a, v)):  # a teammate's mailbox of A, as project_senders and the dispatcher write it
        assert db.execute("UPDATE user_oauth_connections SET status = 'error' WHERE id = %s", (mailbox,)).rowcount
        assert db.execute('DELETE FROM user_oauth_connections WHERE id = %s', (mailbox,)).rowcount
    # A mailbox never becomes a grant.
    other = db.execute(
        'INSERT INTO user_oauth_connections (user_id, organization_id, provider, refresh_token_encrypted)'
        " VALUES (%s, %s, 'google', 'enc') RETURNING id",
        (v, a),
    ).fetchone()['id']
    refused(db, "UPDATE user_oauth_connections SET provider = 'hubspot' WHERE id = %s", (other,))


def test_deleting_the_owners_account_orphans_the_grant_and_nobody_adopts_it(db, world):
    """The foreign key's ON DELETE SET NULL is an UPDATE, which the trigger lets through; a later owner is refused."""
    a, u, v = world['a'], world['u'], world['v']
    with scoped(db, member=(a, u)):
        grant = db.execute(STORE, (u, a)).fetchone()['id']
    db.execute('DELETE FROM "user" WHERE id = %s', (u,))
    assert db.execute('SELECT user_id FROM user_oauth_connections WHERE id = %s', (grant,)).fetchone() == {
        'user_id': None
    }
    refused(db, 'UPDATE user_oauth_connections SET user_id = %s WHERE id = %s', (v, grant))


def test_the_system_role_sees_every_organization(db, world):
    with scoped(db, role='zenbree_system'):
        found = ids(db, 'SELECT id FROM saved_searches WHERE user_id = %s', (world['u'],))
    assert found == {world['search_a'], world['search_b']}


NEW_RELATIONS = {
    'zt_plain': 'CREATE TABLE zt_plain (id int, user_id uuid); INSERT INTO zt_plain VALUES (1, NULL)',
    'zt_as': 'CREATE TABLE zt_as AS SELECT 1 AS id',
    'zt_into': 'SELECT 1 AS id INTO zt_into',
}


def test_a_new_table_is_closed_whatever_command_makes_it(db, world):
    for sql in NEW_RELATIONS.values():
        db.execute(sql)
    db.execute('CREATE MATERIALIZED VIEW zt_mv AS SELECT 1 AS id')
    for table in NEW_RELATIONS:
        rel = db.execute(
            "SELECT relrowsecurity, (SELECT bool_and(has_table_privilege('zenbree_app', c.oid, p))"
            " FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p) AS app,"
            ' (SELECT count(*) FROM pg_policy WHERE polrelid = c.oid) AS policies FROM pg_class c WHERE oid = %s::regclass',
            (table,),
        ).fetchone()
        assert rel == {'relrowsecurity': True, 'app': True, 'policies': 0}, table
        assert db.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] == 1
        with scoped(db, member=(world['a'], world['u'])):
            assert db.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] == 0, table
    with scoped(db, member=(world['a'], world['u'])):
        refused(db, 'SELECT 1 FROM zt_mv')
    with scoped(db, role='zenbree_system'):
        assert db.execute('SELECT id FROM zt_mv').fetchone()['id'] == 1


def test_a_new_table_with_an_organization_or_a_label_gets_its_policy(db, world):
    """The event trigger reads the CREATE, and the COMMENT that labels a table after it."""
    a, b, u = world['a'], world['b'], world['u']
    db.execute("""
        CREATE TABLE zt_org (id serial PRIMARY KEY, organization_id uuid NOT NULL);
        CREATE TABLE zt_person (id int, user_id uuid NOT NULL);
        COMMENT ON TABLE zt_person IS 'zb:personal user_id: a test';
        CREATE TEMP TABLE zt_temp (id int);
    """)
    db.execute('INSERT INTO zt_org (organization_id) VALUES (%s), (%s)', (a, b))
    db.execute('INSERT INTO zt_person VALUES (1, %s), (2, %s)', (u, world['w']))
    with scoped(db, member=(a, u)):
        assert [r['organization_id'] for r in db.execute('SELECT organization_id FROM zt_org')] == [a]
        assert (
            db.execute('INSERT INTO zt_org DEFAULT VALUES RETURNING organization_id').fetchone()['organization_id'] == a
        )
        assert ids(db, 'SELECT id FROM zt_person') == {1}
    assert not db.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'zt_temp'::regclass").fetchone()[
        'relrowsecurity'
    ]


@pytest.mark.parametrize('table', ['lists', 'notifications', 'notification_settings', 'user_memory_doc'])
def test_scoping_a_table_again_changes_nothing(db, table):
    """zb.scope runs on every COMMENT and CREATE TRIGGER: on a table already right it must not recreate the policy,
    which would take an ACCESS EXCLUSIVE lock in the middle of a migration that asked for less. Also catches
    zb.policy_of writing a text other than the one pg_get_expr prints."""
    policies = 'SELECT oid FROM pg_policy WHERE polrelid = %s::regclass'
    before = db.execute(policies, (table,)).fetchall()
    db.execute('SELECT zb.scope(%s)', (table,))
    if table == 'lists':
        db.execute('COMMENT ON TABLE lists IS NULL')
    assert db.execute(policies, (table,)).fetchall() == before
