"""Projects carry the client (#307): the backfill, and each project's own exclusions, senders and inbox.

Needs TEST_DATABASE_URL; builds throwaway schemas from api/db migrations.
"""

import json
import shutil
from datetime import timedelta
from unittest.mock import ANY

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.billing.credits import Hold, Payer, Refused
from app.billing.tab import Tab
from app.core.migrate import MIGRATIONS, migrate
from app.integrations import project_senders, token_crypto
from app.main import app
from app.outreach import dispatcher, sender_pool
from app.outreach.domain.campaign import validate_steps
from tests import test_dispatcher_sql
from tests.conftest import (
    act_as,
    call_as,
    conninfo_of,
    each_org_async,
    empty_database,
    give_role,
    link,
    member,
    pool,
    project,
    scoped_conn,
    sender,
    sign_in,
)
from tests.test_dispatcher_sql import DB_URL, GMAIL_SEND, NOW, ONE_STEP, _beat, _mailbox, _sends
from tests.test_dispatcher_sql import db as db
from tests.test_tools_sql import _user

provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: project clients SQL test NOT run')
MIGRATION = '20261006134459_project_clients.sql'


def second_project(c, uid, name='Client B') -> str:
    """Another client of the user's organization."""
    return c.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id::text',
        (member(c, uid).org_id, uid, name),
    ).fetchone()['id']


def campaign_in(c, uid, project_id, *, email='anna@client.test') -> str:
    cid = c.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps,
               approve_before_sending, activated_at)
           VALUES ((SELECT organization_id FROM projects WHERE id = %s), %s, %s, 'C', 'active', %s, false, %s)
           RETURNING id::text""",
        (project_id, project_id, uid, json.dumps(validate_steps(ONE_STEP)), NOW - timedelta(hours=1)),
    ).fetchone()['id']
    pool(c, cid, ('google',))
    c.execute(
        """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status)
           VALUES (%s, %s, %s, 'Anna', 'Client', 'active')""",
        (cid, uid, email),
    )
    return cid


# ── backfill ─────────────────────────────────────────────────────────────────


@pytest.fixture
def before(tmp_path):
    """A schema migrated up to just before this change, and the directory to finish it from."""
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name < MIGRATION:
            shutil.copy(path, tmp_path)
    with empty_database() as c:
        migrate(c, tmp_path)
        yield c, tmp_path


def test_backfill_gives_each_project_its_creators_profile_exclusions_and_senders(before):
    c, directory = before
    ada, bo, cy, dee = (_user(c) for _ in range(4))
    org = c.execute('SELECT default_organization(%s) AS id', (ada,)).fetchone()['id']
    first, second = (
        c.execute(
            "INSERT INTO projects (organization_id, user_id, name, created_at) VALUES (%s, %s, %s, now() - %s * interval '1 day')"
            ' RETURNING id',
            (org, ada, name, age),
        ).fetchone()['id']
        for name, age in (('Mine', 2), ('Client', 1))
    )
    c.execute(
        "INSERT INTO business_profiles (user_id, profile, completed_at, exclusion_lines) VALUES (%s, %s, now(), '{Rival}')",
        (ada, json.dumps({'company_name': 'Ada Srl'})),
    )
    c.execute(
        "INSERT INTO exclusions (user_id, kind, value, source) VALUES (%s, 'domain', 'rival.com', NULL),"
        " (%s, 'company_name', 'rival', 'onboarding')",
        (ada, ada),
    )
    c.execute(
        "INSERT INTO user_oauth_connections (user_id, provider, refresh_token_encrypted) VALUES (%s, 'google', 'x')",
        (ada,),
    )
    campaigns = {}
    for pid, n in ((first, 1), (second, 2)):  # the second project holds most of Ada's campaigns
        lid = c.execute(
            "INSERT INTO lists (organization_id, user_id, project_id, name, entity) VALUES (%s, %s, %s, 'L', 'person')"
            ' RETURNING id',
            (org, ada, pid),
        ).fetchone()['id']
        for _ in range(n):
            campaigns[pid] = c.execute(
                "INSERT INTO outreach_campaigns (organization_id, owner_id, name, list_id) VALUES (%s, %s, 'C', %s)"
                ' RETURNING id',
                (org, ada, lid),
            ).fetchone()['id']
    c.execute(
        'INSERT INTO outreach_inbound_messages (owner_id, channel, kind, provider_message_id, campaign_id)'
        " VALUES (%s, 'email', 'reply', 'm1', %s), (%s, 'email', 'reply', 'm2', NULL)",
        (ada, campaigns[first], ada),
    )
    # Bo onboarded and never opened a project; Cy predates onboarding (a chat, no profile); Dee is brand new.
    c.execute('INSERT INTO business_profiles (user_id, completed_at) VALUES (%s, now())', (bo,))
    cy_org = c.execute('SELECT default_organization(%s) AS id', (cy,)).fetchone()['id']
    cy_project = c.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'P') RETURNING id", (cy_org, cy)
    ).fetchone()['id']
    c.execute(
        'INSERT INTO chat_sessions (organization_id, user_id, project_id) VALUES (%s, %s, %s)', (cy_org, cy, cy_project)
    )

    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]

    profiles = {
        r['project_id']: r
        for r in c.execute('SELECT project_id, profile, completed_at, exclusion_lines FROM business_profiles')
    }
    for pid in (first, second):
        assert profiles[pid]['profile'] == {'company_name': 'Ada Srl'} and profiles[pid]['completed_at']
        assert profiles[pid]['exclusion_lines'] == ['Rival']
    rules = c.execute('SELECT project_id, kind, value, source FROM exclusions ORDER BY project_id, kind').fetchall()
    assert {(r['project_id'], r['kind'], r['value'], r['source']) for r in rules} == {
        (pid, kind, value, source)
        for pid in (first, second)
        for kind, value, source in (('domain', 'rival.com', None), ('company_name', 'rival', 'onboarding'))
    }
    (sender,) = c.execute(
        'SELECT u.user_id, u.organization_id, ARRAY(SELECT ps.project_id FROM project_senders ps'
        ' WHERE ps.sender_id = u.id ORDER BY ps.project_id) AS projects FROM user_oauth_connections u'
    ).fetchall()
    assert str(sender['user_id']) == ada and sender['organization_id'] == org  # one row, never copied
    assert sorted(sender['projects']) == sorted([first, second])  # used by every project of its connector
    by_campaign = {r['id']: r['project_id'] for r in c.execute('SELECT id, project_id FROM outreach_campaigns')}
    assert by_campaign[campaigns[first]] == first and by_campaign[campaigns[second]] == second
    inbox = {r['provider_message_id']: r['project_id'] for r in c.execute('SELECT * FROM outreach_inbound_messages')}
    assert inbox == {'m1': first, 'm2': first}  # its campaign's project; else the oldest its owner's sender serves

    (bo_project,) = c.execute('SELECT id, name FROM projects WHERE user_id = %s', (bo,)).fetchall()
    assert bo_project['name'] == 'Il mio progetto' and profiles[bo_project['id']]['completed_at']
    assert profiles[cy_project]['completed_at'] and profiles[cy_project]['profile'] is None
    assert not c.execute('SELECT 1 FROM projects WHERE user_id = %s', (dee,)).fetchone()  # made on first use
    assert len(profiles) == 4


async def test_an_owner_with_campaigns_in_two_projects_keeps_sending_in_both_after_the_migration(
    before, provider, switches, monkeypatch
):
    """#299: "nothing changes visibly". Ada's one mailbox wrote for campaigns in two projects before #307; after the
    migration it is linked to both, and the dispatcher sends both campaigns' due mail through it, one per beat."""
    c, directory = before
    monkeypatch.setattr(dispatcher, 'get_conn', scoped_conn(c))
    monkeypatch.setattr('app.outreach.bounces.get_conn', scoped_conn(c))  # #747: each send rechecks its bounces
    monkeypatch.setattr(test_dispatcher_sql, '_DB', [c])  # the organizations ``_beat`` runs
    ada = _user(c)
    org = c.execute('SELECT default_organization(%s) AS id', (ada,)).fetchone()['id']
    c.execute(
        'INSERT INTO user_oauth_connections (user_id, provider, refresh_token_encrypted, granted_scopes,'
        " account_label, created_at) VALUES (%s, 'google', %s, %s, 'ada@acme.test', %s)",
        (ada, token_crypto.encrypt('refresh-1').value, ['openid', GMAIL_SEND], NOW - timedelta(days=30)),
    )
    for name, email in (('Mine', 'anna@client.test'), ('Client', 'bea@client.test')):
        pid = c.execute(
            'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id', (org, ada, name)
        ).fetchone()['id']
        lid = c.execute(
            "INSERT INTO lists (organization_id, user_id, project_id, name, entity) VALUES (%s, %s, %s, 'L', 'person')"
            ' RETURNING id',
            (org, ada, pid),
        ).fetchone()['id']
        cid = c.execute(
            """INSERT INTO outreach_campaigns (organization_id, owner_id, name, list_id, status, steps,
                   approve_before_sending, sender_pool, activated_at)
               VALUES (%s, %s, %s, %s, 'active', %s, false, '{google}', %s) RETURNING id""",
            (org, ada, name, lid, json.dumps(validate_steps(ONE_STEP)), NOW - timedelta(hours=1)),
        ).fetchone()['id']
        c.execute(
            """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status)
               VALUES (%s, %s, %s, 'Anna', 'Client', 'active')""",
            (cid, ada, email),
        )
    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]
    migrate(c)  # the dispatcher reads later migrations: crm_gate_checks (#120), the mailbox window (#348)
    monkeypatch.setattr('app.outreach.sender_slot.get_conn', scoped_conn(c))

    assert (await _beat())['sent'] == 1
    assert (await each_org_async(c, dispatcher.send_due, NOW + timedelta(minutes=15)))['sent'] == 1
    assert sorted(m['To'] for m, _ in provider.sent) == ['anna@client.test', 'bea@client.test']
    assert {m['From'] for m, _ in provider.sent} == {'ada@acme.test'}


def test_a_foreign_project_id_never_answers_with_another_organizations_data(db, monkeypatch):
    """The active project comes from the caller's header: one outside the caller's organization falls back to their
    own default project, it never reads the other organization's rows."""
    monkeypatch.setattr('app.exclusions.routes.get_conn', dispatcher.get_conn)
    ada, eve = _user(db), _user(db)
    theirs = project(db, ada)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'secret.test')",
        (theirs, ada),
    )
    sign_in(app, member(db, eve))
    try:
        client = TestClient(app)
        assert client.get('/v1/exclusions', headers={'X-Project-Id': theirs}).json() == {'exclusions': []}
        client.post('/v1/exclusions', json={'items': [{'value': 'mine.test'}]}, headers={'X-Project-Id': theirs})
    finally:
        app.dependency_overrides.clear()
    rows = db.execute('SELECT project_id::text AS p, value FROM exclusions ORDER BY value').fetchall()
    assert rows == [{'p': project(db, eve), 'value': 'mine.test'}, {'p': theirs, 'value': 'secret.test'}]


# ── each project's own exclusions, senders and inbox ─────────────────────────


async def test_an_exclusion_in_one_project_does_not_block_another(db, provider, switches):
    uid = _user(db)
    a, b = project(db, uid), second_project(db, uid)
    _mailbox(db, uid, projects=[a, b])  # one mailbox serving both clients
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'client.test')", (a, uid)
    )
    in_a, in_b = campaign_in(db, uid, a), campaign_in(db, uid, b)
    await _beat()
    status = {
        r['campaign_id']: r['status']
        for r in db.execute('SELECT campaign_id::text, status FROM outreach_campaign_targets')
    }
    assert status == {in_a: 'excluded', in_b: 'active'}
    assert [str(s['campaign_id']) for s in _sends(db)] == [in_b] and len(provider.sent) == 1


async def test_a_campaign_sends_only_through_a_mailbox_linked_to_its_project(db, provider, switches, monkeypatch):
    monkeypatch.setattr(project_senders, 'get_conn', dispatcher.get_conn)
    uid = _user(db)
    a, b = project(db, uid), second_project(db, uid)
    mailbox = _mailbox(db, uid, projects=[a])  # only client A uses the mailbox
    cid = campaign_in(db, uid, b)
    counts = await _beat()
    assert provider.sent == [] and counts.get('sent', 0) == 0
    assert [s['status'] for s in _sends(db)] == ['approved']  # waits for a mailbox of its own project
    user = member(db, uid)
    assert call_as(user, project_senders.link, user, mailbox, b).is_ok()
    sender_pool.pick(db, cid, [mailbox])  # a link joins no pool by itself: a person picks it
    assert (await each_org_async(db, dispatcher.send_due, NOW))['sent'] == 1
    ((msg, _),) = provider.sent
    assert msg['From'] == 'me@acme.test'


def test_activating_needs_a_sender_in_the_campaigns_project(db, provider, monkeypatch):
    monkeypatch.setattr('app.outreach.routes.get_conn', dispatcher.get_conn)
    uid = _user(db)
    a, b = project(db, uid), second_project(db, uid)
    _mailbox(db, uid, projects=[a])
    cid = campaign_in(db, uid, b)
    db.execute("UPDATE outreach_campaigns SET status = 'draft', activated_at = NULL WHERE id = %s", (cid,))
    db.execute("UPDATE outreach_campaign_targets SET status = 'draft'")
    sign_in(app, member(db, uid))
    try:
        client = TestClient(app)
        res = client.post(f'/v1/campaigns/{cid}/activate')
        assert res.status_code == 422 and res.json()['detail']['code'] == 'CAMPAIGN.NO_SENDER'
        listed = client.get('/v1/campaigns', headers={'X-Project-Id': a}).json()['campaigns']
        assert listed == []  # the campaigns list is the active project's
        assert [c['id'] for c in client.get('/v1/campaigns', headers={'X-Project-Id': b}).json()['campaigns']] == [cid]
    finally:
        app.dependency_overrides.clear()


def test_concurrent_first_requests_create_one_default_project(db):
    """A page fires several API calls at once; each resolves the active project, so the first ones race to create
    the default. The organization lock makes the loser wait and reuse the winner's."""
    import threading

    from app.projects.routes import default_project

    uid = _user(db)
    user = member(db, uid)
    found: list[str] = []
    same = conninfo_of(db)
    with psycopg.connect(same, row_factory=dict_row) as first, psycopg.connect(same, row_factory=dict_row) as other:
        mine = default_project(first, user)  # created, uncommitted, the organization row held

        def race():
            found.append(default_project(other, user))
            other.commit()

        t = threading.Thread(target=race)
        t.start()
        t.join(0.5)
        assert t.is_alive()  # waiting on the lock, not inserting its own
        first.commit()
        t.join(10)
    assert found == [mine]
    assert db.execute('SELECT count(*) AS n FROM projects').fetchone()['n'] == 1


def test_an_ai_column_fill_holds_no_more_than_its_projects_hard_cap(db, monkeypatch):
    """#307: the wallet holds 100, the list's client has 5 left this month: the fill holds what the cap allows
    (whole cells only), and the next fill gets nothing and reads as capped."""
    from app.lists import ai_columns

    monkeypatch.setattr(ai_columns, 'get_conn', dispatcher.get_conn)
    monkeypatch.setattr(ai_columns.balance, 'changed', lambda *a, **k: None)
    uid = _user(db)
    org, pid = member(db, uid).org_id, project(db, uid)
    db.execute(
        'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (%s, %s, 100)', (org, uid)
    )
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, 'L', 'company')"
        ' RETURNING id::text',
        (org, pid, uid),
    ).fetchone()['id']
    db.execute("UPDATE projects SET credit_cap = 5, credit_cap_mode = 'hard' WHERE id = %s", (pid,))
    payer = Payer(str(org), str(uid), pid)
    act_as(org, uid)  # the fill task runs as the list's member
    tab = ai_columns._open(payer, lid, 'ai_x', 10, None)
    assert isinstance(tab, Tab) and tab.hold.amount == 5
    hold = tab.hold
    assert (
        db.execute('SELECT project_id::text AS p FROM credit_transactions WHERE id = %s', (hold.id,)).fetchone()['p']
        == pid
    )
    assert ai_columns._open(payer, lid, 'ai_x', 10, None) == Refused(capped=True)
    db.execute("UPDATE projects SET credit_cap_mode = 'soft' WHERE id = %s", (pid,))
    assert ai_columns._open(payer, lid, 'ai_x', 2, None) == Tab(Hold(ANY, 2))  # soft: only reported


def test_only_an_admin_sets_a_projects_credit_cap(db):
    from app.auth.middleware import CurrentUser

    uid = _user(db)
    pid, org = project(db, uid), member(db, uid).org_id
    cap = {'credit_cap': 500, 'credit_cap_mode': 'hard'}
    try:
        client = TestClient(app)
        sign_in(app, give_role(db, CurrentUser(id=uid, org_id=org, org_role='member')))
        res = client.patch(f'/v1/projects/{pid}', json=cap)
        assert res.status_code == 403 and res.json()['detail']['code'] == 'AUTH.ORG_ADMIN_ONLY'
        assert client.patch(f'/v1/projects/{pid}', json={'name': 'Renamed'}).status_code == 200  # renaming is anyone's
        assert client.patch(f'/v1/projects/{pid}', json={'credit_cap': 5}).status_code == 422  # both together
        sign_in(app, give_role(db, CurrentUser(id=uid, org_id=org, org_role='admin')))
        body = client.patch(f'/v1/projects/{pid}', json=cap).json()['project']
        assert (body['name'], body['credit_cap'], body['credit_cap_mode'], body['credits_this_month']) == (
            'Renamed',
            500,
            'hard',
            0,
        )
        cleared = client.patch(f'/v1/projects/{pid}', json={'credit_cap': None, 'credit_cap_mode': None}).json()
        assert cleared['project']['credit_cap'] is None and cleared['project']['name'] == 'Renamed'
    finally:
        app.dependency_overrides.clear()


def test_backfill_keeps_personal_senders_profile_and_exclusions_out_of_a_team_organization(before):
    """Ada belongs to her own organization and to Tom's team (#308), with a project she created in each. Her
    mailbox, profile and exclusions were hers, in her own organization: after the migration they serve only that
    organization's project, never the team's, whose members could otherwise send from her mailbox."""
    c, directory = before
    ada, tom = _user(c), _user(c)
    own = c.execute('SELECT own_organization(%s) AS id', (ada,)).fetchone()['id']
    team = c.execute('SELECT own_organization(%s) AS id', (tom,)).fetchone()['id']
    c.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (team, ada, 'member'),
    )
    mine, theirs = (
        c.execute(
            'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id', (org, ada, name)
        ).fetchone()['id']
        for org, name in ((own, 'Mine'), (team, 'Team client'))
    )
    c.execute(
        'INSERT INTO business_profiles (user_id, profile, completed_at) VALUES (%s, %s, now())',
        (ada, json.dumps({'company_name': 'Ada Srl'})),
    )
    c.execute("INSERT INTO exclusions (user_id, kind, value) VALUES (%s, 'domain', 'rival.com')", (ada,))
    c.execute(
        "INSERT INTO user_oauth_connections (user_id, provider, refresh_token_encrypted) VALUES (%s, 'google', 'x')",
        (ada,),
    )
    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]

    links = c.execute('SELECT project_id FROM project_senders').fetchall()
    assert [r['project_id'] for r in links] == [mine]
    assert [r['project_id'] for r in c.execute('SELECT project_id FROM exclusions')] == [mine]
    profiles = {r['project_id']: r['profile'] for r in c.execute('SELECT project_id, profile FROM business_profiles')}
    assert profiles == {mine: {'company_name': 'Ada Srl'}}
    assert theirs not in profiles
    (sender,) = c.execute('SELECT organization_id FROM user_oauth_connections').fetchall()
    assert sender['organization_id'] == own


async def test_one_member_sends_from_two_gmail_accounts_for_two_clients(db, provider, switches):
    """Owner decision (#307): senders are keyed by account, so a member holds Gmail A for client 1 and Gmail B for
    client 2 in one organization; each client's campaign goes out from its own account."""
    uid = _user(db)
    a, b = project(db, uid), second_project(db, uid)
    _mailbox(db, uid, projects=[a], account_label='one@client-a.test', provider_account_id='sub-a')
    _mailbox(db, uid, projects=[b], account_label='two@client-b.test', provider_account_id='sub-b')
    campaign_in(db, uid, a, email='anna@client.test')
    campaign_in(db, uid, b, email='bea@client.test')
    assert (await _beat())['sent'] == 2  # one per mailbox per beat
    assert {m['To']: m['From'] for m, _ in provider.sent} == {
        'anna@client.test': 'one@client-a.test',
        'bea@client.test': 'two@client-b.test',
    }
    per_account = db.execute(
        'SELECT u.account_label, count(s.id) AS n FROM user_oauth_connections u'
        ' LEFT JOIN outreach_campaign_sends s ON s.sender_id = u.id GROUP BY u.account_label ORDER BY 1'
    ).fetchall()
    assert [(r['account_label'], r['n']) for r in per_account] == [('one@client-a.test', 1), ('two@client-b.test', 1)]


def test_the_migration_merges_two_rows_of_one_account_into_the_last_connected(before):
    """Two rows of one account in one organization (impossible before #307 without dropping #317's index and the
    per-user key, done here to build them) merge into the most recently connected: its token and connector stay,
    the other's project links and sends move to it, the other goes."""
    c, directory = before
    c.execute('DROP INDEX idx_user_oauth_connections_provider_account')
    c.execute('ALTER TABLE user_oauth_connections DROP CONSTRAINT user_oauth_connections_user_id_provider_key')
    ada = _user(c)
    org = c.execute('SELECT own_organization(%s) AS id', (ada,)).fetchone()['id']
    pid = c.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'P') RETURNING id", (org, ada)
    ).fetchone()['id']
    old, new = (
        c.execute(
            'INSERT INTO user_oauth_connections (user_id, provider, refresh_token_encrypted, provider_account_id,'
            " connected_at) VALUES (%s, 'unipile', %s, 'acc-1', now() - %s * interval '1 day') RETURNING id",
            (ada, token, age),
        ).fetchone()['id']
        for token, age in (('old-token', 5), ('new-token', 1))
    )
    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]
    (row,) = c.execute('SELECT id, refresh_token_encrypted AS t FROM user_oauth_connections').fetchall()
    assert (row['id'], row['t']) == (new, 'new-token') and old != new
    links = c.execute('SELECT project_id, sender_id FROM project_senders').fetchall()
    assert links == [{'project_id': pid, 'sender_id': new}]


async def test_a_mailboxs_daily_count_follows_what_it_sent_not_its_current_links(db, provider, switches):
    """L1 (#307): the cap counts the sends recorded with the account (sender_id); moving the account to another
    client mid-day neither forgets nor double counts them."""
    from app.outreach import sending

    uid = _user(db)
    a, b = project(db, uid), second_project(db, uid)
    mailbox = _mailbox(db, uid, projects=[a])
    campaign_in(db, uid, a)
    assert (await _beat())['sent'] == 1
    assert sending.sent_today(db, mailbox, NOW, 'Europe/Rome') == 1
    db.execute('DELETE FROM project_senders WHERE project_id = %s', (a,))
    link(db, b, mailbox)
    assert sending.sent_today(db, mailbox, NOW, 'Europe/Rome') == 1


# ── linking an account to another client (#327) ─────────────────────────────


@pytest.fixture
def team(db, provider, monkeypatch):
    """Ada (admin), Bo and Cy (members) in Ada's organization, with clients A and B; calls go through ``as_``."""
    from app.auth.middleware import CurrentUser

    for mod in ('app.integrations.project_senders', 'app.integrations.senders'):
        monkeypatch.setattr(f'{mod}.get_conn', scoped_conn(db))
    ada, bo, cy = _user(db), _user(db), _user(db)
    org = member(db, ada).org_id
    for uid in (bo, cy):
        db.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (org, uid, 'member'),
        )
    roles = {ada: 'admin', bo: 'member', cy: 'member'}
    client = TestClient(app)

    def as_(uid):
        sign_in(app, CurrentUser(id=uid, org_id=org, org_role=roles[uid]))
        return client

    try:
        yield (
            type('Team', (), {'ada': ada, 'bo': bo, 'cy': cy, 'a': project(db, ada), 'b': second_project(db, ada)})(),
            as_,
        )
    finally:
        app.dependency_overrides.clear()


def _links(db, sender_id) -> list[str]:
    return [
        str(r['project_id'])
        for r in db.execute(
            'SELECT project_id FROM project_senders WHERE sender_id = %s ORDER BY created_at', (sender_id,)
        )
    ]


def _url(sender_id, project_id) -> str:
    return f'/v1/integrations/senders/accounts/{sender_id}/projects/{project_id}'


def test_a_connector_uses_her_mailbox_in_another_client_and_it_sends_there(db, team):
    t, as_ = team
    box = _mailbox(db, t.bo, projects=[t.a])
    assert as_(t.bo).put(_url(box, t.b)).status_code == 200
    assert sorted(_links(db, box)) == sorted([t.a, t.b])  # one row, two clients
    senders = as_(t.cy).get('/v1/integrations/senders?dns=false', headers={'X-Project-Id': t.b}).json()['senders']
    assert [s['account_label'] for s in senders] == ['me@acme.test']
    (acc,) = as_(t.cy).get('/v1/integrations/senders/accounts').json()['accounts']
    assert sorted(acc['project_ids']) == sorted([t.a, t.b]) and acc['can_manage'] is False  # Cy only sees it
    assert as_(t.bo).put(_url(box, t.b)).status_code == 200  # idempotent


def test_only_the_connector_or_an_admin_links_or_unlinks_an_account(db, team):
    t, as_ = team
    box = _mailbox(db, t.bo, projects=[t.a])
    for res in (as_(t.cy).put(_url(box, t.b)), as_(t.cy).delete(_url(box, t.a))):
        assert res.status_code == 403 and res.json()['detail']['code'] == 'INTEGRATIONS.LINK_FORBIDDEN'
    assert _links(db, box) == [t.a]
    assert as_(t.ada).put(_url(box, t.b)).status_code == 200  # an admin moves anyone's


def test_linking_a_mailbox_adds_it_beside_a_colleagues(db, team):
    t, as_ = team
    bos = _mailbox(db, t.bo, projects=[t.a])
    cys = sender(db, t.cy, projects=[t.b], account_label='cy@acme.test', provider_account_id='sub-cy')
    assert as_(t.bo).put(_url(bos, t.b)).status_code == 200
    assert _links(db, cys) == [t.b] and sorted(_links(db, bos)) == sorted([t.a, t.b])


def test_linking_over_a_colleagues_linkedin_account_follows_the_replace_rule(db, team):
    t, as_ = team
    bos = sender(db, t.bo, 'unipile', projects=[t.a], provider_account_id='acc-bo')
    cys = sender(db, t.cy, 'unipile', projects=[t.b], provider_account_id='acc-cy')
    res = as_(t.bo).put(_url(bos, t.b))
    assert res.status_code == 403 and res.json()['detail']['code'] == 'INTEGRATIONS.REPLACE_FORBIDDEN'
    assert _links(db, cys) == [t.b] and _links(db, bos) == [t.a]  # nothing changed
    db.execute("UPDATE user_oauth_connections SET status = 'disconnected' WHERE id = %s", (cys,))
    assert as_(t.bo).put(_url(bos, t.b)).status_code == 200  # a disconnected one is anyone's to replace
    assert _links(db, cys) == [] and sorted(_links(db, bos)) == sorted([t.a, t.b])
    db.execute("UPDATE user_oauth_connections SET status = 'connected' WHERE id = %s", (cys,))
    assert as_(t.ada).put(_url(cys, t.b)).status_code == 200  # an admin replaces
    assert _links(db, cys) == [t.b] and _links(db, bos) == [t.a]


def test_unlinking_the_last_client_keeps_the_account_connected(db, team):
    t, as_ = team
    box = _mailbox(db, t.bo, projects=[t.a])
    assert as_(t.bo).delete(_url(box, t.a)).status_code == 200
    row = db.execute(
        'SELECT status, refresh_token_encrypted FROM user_oauth_connections WHERE id = %s', (box,)
    ).fetchone()
    assert row['status'] == 'connected' and row['refresh_token_encrypted']
    (acc,) = as_(t.bo).get('/v1/integrations/senders/accounts').json()['accounts']
    assert acc['project_ids'] == [] and acc['can_manage'] is True
    res = as_(t.bo).delete(_url(box, t.a))
    assert res.status_code == 404 and res.json()['detail']['code'] == 'INTEGRATIONS.LINK_NOT_FOUND'
    assert as_(t.bo).put(_url(box, t.b)).status_code == 200  # still usable


def test_an_account_or_client_of_another_organization_is_not_found(db, team):
    t, as_ = team
    box = _mailbox(db, t.bo, projects=[t.a])
    eve = _user(db)
    foreign = sender(db, eve, account_label='eve@evil.test', provider_account_id='sub-eve')
    res = as_(t.ada).put(_url(box, project(db, eve)))
    assert res.status_code == 404 and res.json()['detail']['code'] == 'INTEGRATIONS.PROJECT_NOT_FOUND'
    res = as_(t.ada).put(_url(foreign, t.a))
    assert res.status_code == 404 and res.json()['detail']['code'] == 'INTEGRATIONS.SENDER_NOT_FOUND'
    assert as_(t.ada).delete(_url(foreign, project(db, eve))).status_code == 404
    assert _links(db, foreign) == [project(db, eve)]
