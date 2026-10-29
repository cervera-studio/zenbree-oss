"""The #935 migration on data the old schema allowed: one target for each case of the backfill."""

import os
import shutil
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import COPY_PREFIX
from tests.pin_invariant import PIN_INVARIANT_SQL
from tests.test_agent import _user, needs_db

pytestmark = needs_db
NAME = '20261017191816_sender_accounts.sql'
HISTORY = (
    'user_oauth_connections',
    'outreach_campaign_sends',
    'outreach_inbox_replies',
    'outreach_inbound_messages',
    'project_senders',
)
T0, T1, T2, T3 = '2026-10-01T09:00Z', '2026-10-02T09:00Z', '2026-10-03T09:00Z', '2026-10-04T09:00Z'


@pytest.fixture(scope='module')
def before(tmp_path_factory):
    url = os.environ['TEST_DATABASE_URL']
    older = tmp_path_factory.mktemp('older')
    for p in MIGRATIONS.glob('*.sql'):
        if p.name < NAME:
            shutil.copy(p, older / p.name)
    name = f'{COPY_PREFIX}before_senders_{uuid.uuid4().hex[:10]}'
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
    name = f'{COPY_PREFIX}senders_{uuid.uuid4().hex[:12]}'
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


@dataclass
class World:
    org: str
    projects: dict[str, str]
    accounts: dict[str, str]
    campaigns: dict[str, str]
    targets: dict[str, str]


def _seed(c) -> World:
    user = _user(c)
    org = str(c.execute('SELECT default_organization(%s) AS o', (user,)).fetchone()['o'])
    stranger = _user(c)
    other = str(c.execute('SELECT default_organization(%s) AS o', (stranger,)).fetchone()['o'])

    def account(o, uid, provider, key, status='connected'):
        return _insert(
            c,
            'user_oauth_connections',
            organization_id=o,
            user_id=uid,
            provider=provider,
            refresh_token_encrypted=f'token-{key}',
            provider_account_id=f'acct-{key}-{uuid.uuid4().hex[:6]}',
            account_label=f'{key}@x.test',
            status=status,
        )

    accounts = {
        'old_google': account(org, user, 'google', 'a'),
        'google_link': account(org, user, 'google', 'b'),
        'microsoft_link': account(org, user, 'microsoft', 'm'),
        'microsoft_only': account(org, user, 'microsoft', 'o'),
        'disconnected_link': account(org, user, 'google', 'd', 'disconnected'),
        'linkedin_link': account(org, user, 'unipile', 'l'),
        'foreign': account(other, stranger, 'google', 'x'),
    }
    projects = {
        p: _insert(c, 'projects', organization_id=org, user_id=user, name=p.lower())
        for p in ('client', 'old_client', 'no_links', 'disconnected_client', 'microsoft_client')
    }
    for p, a, provider in [
        ('client', 'google_link', 'google'),
        ('client', 'microsoft_link', 'microsoft'),
        ('client', 'linkedin_link', 'unipile'),
        ('old_client', 'old_google', 'google'),
        ('disconnected_client', 'disconnected_link', 'google'),
        ('microsoft_client', 'microsoft_only', 'microsoft'),
    ]:
        c.execute(
            'INSERT INTO project_senders (project_id, sender_id, provider, organization_id) VALUES (%s, %s, %s, %s)',
            (projects[p], accounts[a], provider, org),
        )

    def campaign(name, project, pool, status='active'):
        return _insert(
            c,
            'outreach_campaigns',
            organization_id=org,
            project_id=projects[project],
            owner_id=user,
            name=name,
            sender_pool=pool,
            status=status,
        )

    campaigns = {
        'google_pool': campaign('c1', 'client', ['google']),
        'microsoft_pool': campaign('c2', 'client', ['microsoft']),
        'unlinked_pool': campaign('c3', 'no_links', ['google']),
        'draft_pool': campaign('c4', 'client', ['google', 'microsoft'], 'draft'),
        'empty_pool': campaign('c5', 'client', []),
        'disconnected_pool': campaign('c6', 'disconnected_client', ['google']),
        'dropped_provider': campaign('c7', 'client', ['microsoft']),
        'half_linked_pool': campaign('c8', 'microsoft_client', ['google', 'microsoft']),
        'pinned_off_link': campaign('c9', 'client', ['microsoft']),
    }

    def target(key, camp, status='active', provider='google'):
        return _insert(
            c,
            'outreach_campaign_targets',
            campaign_id=campaigns[camp],
            owner_id=user,
            email=f'{key}@lead.test',
            status=status,
            sender_provider=provider,
        )

    def send(
        t,
        camp,
        step,
        status,
        *,
        sender=None,
        attempted=None,
        delivered=None,
        attempts=1,
        channel='email',
        sender_provider='google',
        error_code=None,
    ):
        return _insert(
            c,
            'outreach_campaign_sends',
            campaign_id=campaigns[camp],
            owner_id=user,
            target_id=t,
            step=step,
            channel=channel,
            recipient='lead@lead.test',
            idempotency_key=uuid.uuid4().hex,
            status=status,
            sender_id=accounts[sender] if sender else None,
            sender_provider=sender_provider,
            attempted_at=attempted,
            delivered_at=delivered,
            attempts=attempts,
            error_code=error_code,
        )

    t = {
        'replaced_link': target('a', 'google_pool'),
        'uncertain': target('b', 'google_pool'),
        'never_sent': target('c', 'google_pool'),
        'rate_limited': target('r', 'google_pool'),
        'no_account_named': target('d', 'google_pool'),
        'split_thread': target('split', 'google_pool'),
        'draft': target('draft', 'google_pool', 'draft', None),
        'linkedin_only': target('li', 'google_pool', provider=None),
        'pool_changed': target('m', 'microsoft_pool'),
        'unknown_account': target('u', 'unlinked_pool'),
        'finished': target('done', 'empty_pool', 'replied'),
        'owned_unsent': target('owned', 'dropped_provider'),
        'pinned_to_unlinked': target('off', 'pinned_off_link'),
    }
    send(t['replaced_link'], 'google_pool', 1, 'sent', sender='old_google', attempted=T1, delivered=T1)
    send(t['replaced_link'], 'google_pool', 2, 'approved', attempts=0, sender_provider=None)
    send(
        t['uncertain'],
        'google_pool',
        1,
        'sending',
        sender='google_link',
        attempted=T2,
        error_code='INTEGRATIONS.TRANSPORT',
    )
    send(t['never_sent'], 'google_pool', 1, 'approved', attempts=0, sender_provider=None)
    send(
        t['rate_limited'],
        'google_pool',
        1,
        'approved',
        sender='google_link',
        attempted=T3,
        attempts=0,
        error_code='INTEGRATIONS.RATE_LIMITED',
    )
    send(t['no_account_named'], 'google_pool', 1, 'sent', delivered=T0)
    send(t['split_thread'], 'google_pool', 1, 'sent', sender='old_google', attempted=T1, delivered=T1)
    send(t['split_thread'], 'google_pool', 2, 'sent', sender='google_link', attempted=T2, delivered=T2)
    send(
        t['linkedin_only'],
        'google_pool',
        1,
        'sent',
        sender='linkedin_link',
        attempted=T1,
        delivered=T1,
        channel='linkedin_connect',
        sender_provider='unipile',
    )
    send(t['pool_changed'], 'microsoft_pool', 1, 'sent', sender='google_link', attempted=T1, delivered=T1)
    send(t['unknown_account'], 'unlinked_pool', 1, 'sent', delivered=T0)
    send(t['finished'], 'empty_pool', 1, 'sent', sender='google_link', attempted=T1, delivered=T1)
    send(t['owned_unsent'], 'dropped_provider', 1, 'approved', attempts=0, sender_provider=None)
    send(t['pinned_to_unlinked'], 'pinned_off_link', 1, 'sent', sender='old_google', attempted=T1, delivered=T1)

    inbound = _insert(
        c,
        'outreach_inbound_messages',
        project_id=projects['client'],
        owner_id=user,
        campaign_id=campaigns['google_pool'],
        target_id=t['replaced_link'],
        channel='email',
        kind='reply',
        provider_message_id='m-1',
    )
    _insert(
        c,
        'outreach_inbox_replies',
        inbound_id=inbound,
        owner_id=user,
        channel='email',
        sender_provider='google',
        recipient='lead@lead.test',
        body='thanks',
        sender_id=accounts['old_google'],
    )
    return World(org, projects, accounts, campaigns, t)


def _rows(c, table) -> list[str]:
    return [r['j'] for r in c.execute(f'SELECT to_jsonb(x)::text AS j FROM {table} x ORDER BY 1').fetchall()]


def _targets(c) -> dict[str, dict]:
    return {
        str(r['id']): r['j']
        for r in c.execute(
            "SELECT id, to_jsonb(t) - 'sender_id' - 'sender_pinned_at' AS j FROM outreach_campaign_targets t"
        ).fetchall()
    }


def _pin(c, target) -> tuple[str | None, str | None]:
    row = c.execute(
        'SELECT sender_id::text AS s, to_char(sender_pinned_at AT TIME ZONE \'UTC\', \'YYYY-MM-DD"T"HH24:MI"Z"\') AS at'
        ' FROM outreach_campaign_targets WHERE id = %s',
        (target,),
    ).fetchone()
    return row['s'], row['at']


def _pool(c, campaign) -> set[str]:
    return {
        str(r['sender_id'])
        for r in c.execute('SELECT sender_id FROM outreach_campaign_senders WHERE campaign_id = %s', (campaign,))
    }


@pytest.fixture
def world(old, upto) -> World:
    w = _seed(old)
    assert migrate(old, upto) == [NAME]
    return w


def test_each_conversation_keeps_the_account_it_was_on(world, old):
    a, t = world.accounts, world.targets
    assert _pin(old, t['replaced_link']) == (a['old_google'], T1)
    assert _pin(old, t['uncertain']) == (a['google_link'], T2)
    assert _pin(old, t['rate_limited']) == (a['google_link'], T3)
    assert _pin(old, t['no_account_named']) == (a['google_link'], T0)
    assert _pin(old, t['split_thread']) == (a['google_link'], T1)
    assert _pin(old, t['pool_changed']) == (a['google_link'], T1)
    assert _pin(old, t['finished']) == (a['google_link'], T1)


def test_a_person_no_mail_reached_is_not_pinned(world, old):
    for key in ('never_sent', 'draft', 'linkedin_only', 'owned_unsent'):
        assert _pin(old, world.targets[key]) == (None, None), key


def test_a_conversation_on_an_unknown_account_is_pinned_to_none(world, old):
    assert _pin(old, world.targets['unknown_account']) == (None, T0)


def test_each_pool_names_the_linked_accounts_of_its_providers_and_of_its_live_threads(world, old):
    a, c = world.accounts, world.campaigns
    assert _pool(old, c['google_pool']) == {a['google_link']}
    assert _pool(old, c['microsoft_pool']) == {a['microsoft_link'], a['google_link']}
    assert _pool(old, c['unlinked_pool']) == set()
    assert _pool(old, c['draft_pool']) == {a['google_link'], a['microsoft_link']}
    assert _pool(old, c['empty_pool']) == set()
    assert _pool(old, c['disconnected_pool']) == {a['disconnected_link']}
    assert _pool(old, c['half_linked_pool']) == {a['microsoft_only']}
    named = {str(r['sender_id']) for r in old.execute('SELECT sender_id FROM outreach_campaign_senders')}
    assert not named & {a['old_google'], a['linkedin_link'], a['foreign']}


def test_a_live_person_a_mailbox_took_keeps_that_mailbox_in_the_pool(world, old):
    assert _pool(old, world.campaigns['dropped_provider']) == {
        world.accounts['microsoft_link'],
        world.accounts['google_link'],
    }


def test_a_pinned_thread_adds_its_pin_not_the_link_of_its_provider(world, old):
    a, c = world.accounts, world.campaigns
    assert _pin(old, world.targets['pinned_to_unlinked']) == (a['old_google'], T1)
    assert _pool(old, c['pinned_off_link']) == {a['microsoft_link']}


def test_tokens_sends_replies_and_links_are_unchanged(old, upto):
    _seed(old)
    history = {table: _rows(old, table) for table in HISTORY}
    targets = _targets(old)
    assert migrate(old, upto) == [NAME]
    for table in HISTORY:
        assert history[table], table
        assert _rows(old, table) == history[table], table
    assert _targets(old) == targets


def test_the_migration_wakes_no_home_stream(old, upto):
    seen = []
    old.add_notify_handler(seen.append)
    old.execute('LISTEN home')
    w = _seed(old)
    old.execute("UPDATE outreach_campaign_targets SET role = 'x' WHERE id = %s", (w.targets['replaced_link'],))
    assert seen
    seen.clear()
    assert migrate(old, upto) == [NAME]
    old.execute('SELECT 1')
    assert seen == []


def test_the_pins_follow_the_invariant_the_send_claim_keeps(world, old):
    assert old.execute(PIN_INVARIANT_SQL).fetchall() == []


def test_the_migration_runs_on_an_empty_schema(old, upto):
    assert migrate(old, upto) == [NAME]
    assert old.execute(PIN_INVARIANT_SQL).fetchall() == []
    assert old.execute('SELECT count(*) AS n FROM outreach_campaign_senders').fetchone() == {'n': 0}


def _pool_row(c, w, campaign, account, provider, project='client'):
    c.execute(
        'INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)'
        ' VALUES (%s, %s, %s, %s, %s)',
        (w.campaigns[campaign], w.accounts[account], w.projects[project], provider, w.org),
    )


@pytest.mark.parametrize(
    ('account', 'provider', 'error'),
    [
        ('old_google', 'google', psycopg.errors.ForeignKeyViolation),
        ('linkedin_link', 'unipile', psycopg.errors.CheckViolation),
        ('linkedin_link', 'google', psycopg.errors.ForeignKeyViolation),
        ('foreign', 'google', psycopg.errors.ForeignKeyViolation),
    ],
)
def test_a_pool_names_only_a_mailbox_its_client_links(world, old, account, provider, error):
    with pytest.raises(error):
        _pool_row(old, world, 'google_pool', account, provider)


def _set_pin(c, target, account, pinned_at) -> None:
    c.execute(
        f'UPDATE outreach_campaign_targets SET sender_id = %s, sender_pinned_at = {pinned_at} WHERE id = %s',
        (account, target),
    )


def test_a_pin_needs_its_time(world, old):
    with pytest.raises(psycopg.errors.CheckViolation):
        old.execute(
            'UPDATE outreach_campaign_targets SET sender_id = %s WHERE id = %s',
            (world.accounts['google_link'], world.targets['never_sent']),
        )


@pytest.mark.parametrize(
    ('target', 'account', 'pinned_at'),
    [
        pytest.param('replaced_link', 'google_link', 'sender_pinned_at', id='to_another_account'),
        pytest.param('unknown_account', 'google_link', 'sender_pinned_at', id='unknown_account_to_an_account'),
        pytest.param('replaced_link', None, 'NULL', id='unpinned'),
        pytest.param('replaced_link', 'old_google', 'now()', id='pin_time_moved'),
        pytest.param('unknown_account', None, 'now()', id='unknown_account_pin_time_moved'),
    ],
)
def test_a_pinned_conversation_is_final(world, old, target, account, pinned_at):
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match='is final'):
        _set_pin(old, world.targets[target], world.accounts.get(account), pinned_at)


def test_a_conversation_whose_account_was_deleted_gets_no_other_account(world, old):
    old.execute('DELETE FROM user_oauth_connections WHERE id = %s', (world.accounts['google_link'],))
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match='is final'):
        _set_pin(old, world.targets['uncertain'], world.accounts['microsoft_link'], 'sender_pinned_at')


def test_a_claim_pins_a_new_conversation_and_keeps_a_pinned_one(world, old):
    t, a = world.targets, world.accounts
    _set_pin(old, t['never_sent'], a['google_link'], f"'{T3}'")
    assert _pin(old, t['never_sent']) == (a['google_link'], T3)
    _set_pin(old, t['replaced_link'], a['old_google'], 'coalesce(sender_pinned_at, now())')
    assert _pin(old, t['replaced_link']) == (a['old_google'], T1)


def test_a_deleted_account_leaves_its_conversations_held_not_free(world, old):
    t, a = world.targets, world.accounts
    old.execute('DELETE FROM user_oauth_connections WHERE id = %s', (a['google_link'],))
    assert _pin(old, t['uncertain']) == (None, T2)
    assert _pool(old, world.campaigns['google_pool']) == set()


@pytest.mark.parametrize('role', [None, pytest.param('zenbree_system', id='member_release')])
def test_an_unlink_removes_the_pool_rows_and_keeps_every_pin(world, old, role):
    pins = {k: _pin(old, v) for k, v in world.targets.items()}
    if role:
        old.execute(f'SET ROLE {role}')
    old.execute(
        'DELETE FROM project_senders WHERE project_id = %s AND sender_id = %s',
        (world.projects['client'], world.accounts['microsoft_link']),
    )
    old.execute('RESET ROLE')
    assert _pool(old, world.campaigns['microsoft_pool']) == {world.accounts['google_link']}
    assert _pool(old, world.campaigns['draft_pool']) == {world.accounts['google_link']}
    assert {k: _pin(old, v) for k, v in world.targets.items()} == pins


def test_replacing_a_clients_account_moves_its_pool_rows_and_no_pin(world, old):
    a, c = world.accounts, world.campaigns
    old.execute(
        'INSERT INTO project_senders (project_id, sender_id, provider, organization_id) VALUES (%s, %s, %s, %s)'
        ' ON CONFLICT (project_id, provider) DO UPDATE SET sender_id = EXCLUDED.sender_id, created_at = now()',
        (world.projects['client'], a['old_google'], 'google', world.org),
    )
    assert _pool(old, c['google_pool']) == {a['old_google']}
    assert _pool(old, c['microsoft_pool']) == {a['microsoft_link'], a['old_google']}
    assert _pin(old, world.targets['uncertain']) == (a['google_link'], T2)
