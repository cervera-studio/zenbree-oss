import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.config import settings
from app.home.nav import load_nav
from app.home.overview import load_overview
from app.lists.rows import add_rows
from tests.conftest import conninfo_of, member, project
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db
from tests.test_holds_sql import lead, person, stop
from tests.test_home_overview import _campaign, _job, _list

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: home NOTIFY SQL tests NOT run')

EXEMPT = {'user'}


@dataclass
class World:
    uid: str
    org: str
    pid: str
    session: str
    job: str
    list: str
    row: str
    campaign: str
    target: str
    send: str
    view: str


@pytest.fixture
def listen(db):
    with psycopg.connect(conninfo_of(db), autocommit=True) as conn:
        conn.execute('LISTEN home')
        yield conn


def _delivered(listen, db) -> list[str]:
    """Every payload delivered so far. Postgres delivers in commit order, so a marker NOTIFY committed now arrives
    after anything committed before it: absence is proven without a sleep."""
    marker = f'marker:{uuid.uuid4()}'
    db.execute("SELECT pg_notify('home', %s)", (marker,))
    out = []
    for n in listen.notifies(timeout=5):
        if n.payload == marker:
            return out
        out.append(n.payload)
    raise AssertionError('the marker notification never arrived')


@pytest.fixture
def world(db, listen) -> World:
    uid = _user(db)
    org = member(db, uid).org_id
    pid = project(db, uid)
    lid = _list(db, uid, 'People', cells=[{'email_status': 'verified'}])
    session = _job(db, uid, pid, [{'tool': 'search_people', 'units': 2, 'done': 1}], list_id=lid)
    cid = _campaign(db, uid, 'Live', list_id=lid, waiting=1)
    db.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href, data) VALUES (%s, %s, 'reply', '/inbox', %s)",
        (uid, org, Jsonb({'name': 'Giulia Rossi'})),
    )
    one = db.execute
    view = one(
        'INSERT INTO saved_views (organization_id, project_id, list_id, name) VALUES (%s, %s, %s, %s) RETURNING id',
        (org, pid, lid, 'Saved'),
    ).fetchone()['id']
    stop(db, pid, 'held@client.test')
    person(db, pid, ('email', 'held@client.test'))
    w = World(
        uid=uid,
        org=org,
        pid=pid,
        session=session,
        job=str(one('SELECT id FROM jobs WHERE chat_session_id = %s', (session,)).fetchone()['id']),
        list=lid,
        row=str(one('SELECT id FROM list_rows WHERE list_id = %s', (lid,)).fetchone()['id']),
        campaign=cid,
        target=str(one('SELECT id FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)).fetchone()['id']),
        send=str(one('SELECT id FROM outreach_campaign_sends WHERE campaign_id = %s', (cid,)).fetchone()['id']),
        view=str(view),
    )
    _delivered(listen, db)
    return w


def test_every_table_the_overview_and_the_nav_read_notifies(db, world):
    # A fresh connection: pg_stat_xact_* also holds a backend's earlier counts it has not flushed yet.
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        overview = load_overview(conn, world.uid, world.org, world.pid).overview
        nav, _ = load_nav(conn, world.uid, world.org, world.pid)
        read = {
            r['relname']
            for r in conn.execute(
                'SELECT relname FROM pg_stat_xact_user_tables'
                ' WHERE schemaname = current_schema() AND seq_scan + coalesce(idx_scan, 0) > 0'
            ).fetchall()
        }
    assert overview.approvals.total and overview.replies.count and overview.running and overview.lists
    assert overview.campaigns
    assert nav.views
    notifying = {
        r['relname']
        for r in db.execute(
            'SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid'
            " WHERE c.relnamespace = current_schema()::regnamespace AND p.proname = 'home_touch'"
        ).fetchall()
    }
    assert read >= EXEMPT
    assert read - EXEMPT == notifying


Write = Callable[[psycopg.Connection, World], None]

WRITES: dict[str, tuple[Write, str]] = {
    'jobs': (lambda c, w: c.execute('UPDATE jobs SET progress_done = 7 WHERE id = %s', (w.job,)), 'p'),
    'chat_sessions': (lambda c, w: c.execute("UPDATE chat_sessions SET title = 'x' WHERE id = %s", (w.session,)), 'p'),
    'lists': (lambda c, w: c.execute("UPDATE lists SET name = 'x' WHERE id = %s", (w.list,)), 'p'),
    'list_rows': (lambda c, w: c.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (w.row,)), 'p'),
    'outreach_campaigns': (
        lambda c, w: c.execute("UPDATE outreach_campaigns SET status = 'paused' WHERE id = %s", (w.campaign,)),
        'p',
    ),
    'outreach_campaign_sends': (
        lambda c, w: c.execute("UPDATE outreach_campaign_sends SET status = 'skipped' WHERE id = %s", (w.send,)),
        'p',
    ),
    'outreach_campaign_targets': (
        lambda c, w: c.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE id = %s", (w.target,)),
        'p',
    ),
    'notifications': (
        lambda c, w: c.execute('UPDATE notifications SET read_at = now() WHERE user_id = %s', (w.uid,)),
        'u',
    ),
    'saved_views': (
        lambda c, w: c.execute(
            'INSERT INTO saved_views (organization_id, project_id, list_id, name) VALUES (%s, %s, %s, %s)',
            (w.org, w.pid, w.list, 'Another'),
        ),
        'p',
    ),
    'member': (lambda c, w: c.execute('DELETE FROM member WHERE "userId" = %s', (w.uid,)), 'u'),
    'outreach_stops': (
        lambda c, w: c.execute(
            "UPDATE outreach_stops SET status = 'failed', last_error_code = 'INSTANTLY.UNREACHABLE'"
            ' WHERE project_id = %s',
            (w.pid,),
        ),
        'p',
    ),
    'instantly_leads': (
        lambda c, w: c.execute("UPDATE instantly_leads SET outcome = 'accepted' WHERE project_id = %s", (w.pid,)),
        'p',
    ),
    'audience_aliases': (
        lambda c, w: c.execute(
            "UPDATE audience_aliases SET value = 'other@client.test' WHERE project_id = %s AND value = 'held@client.test'",
            (w.pid,),
        ),
        'p',
    ),
}


def _topic(w: World, scope: str) -> str:
    return f'p:{w.pid}' if scope == 'p' else f'u:{w.uid}'


@pytest.mark.parametrize('table', WRITES)
def test_a_committed_write_notifies_its_topic(db, listen, world, table):
    write, scope = WRITES[table]
    write(db, world)
    assert _delivered(listen, db) == [_topic(world, scope)]


@pytest.mark.parametrize('table', ['jobs'])
def test_a_rolled_back_write_notifies_nothing(db, listen, world, table):
    write, _ = WRITES[table]
    with db.transaction():
        write(db, world)
        raise psycopg.Rollback
    assert _delivered(listen, db) == []


def test_one_notification_per_transaction(db, listen, world):
    with db.transaction():
        add_rows(db, world.list, 'person', [{'email': f'p{i}@example.test'} for i in range(5)])
        db.execute('UPDATE jobs SET progress_done = 3, progress_total = 9 WHERE id = %s', (world.job,))
        db.execute('UPDATE jobs SET progress_done = 4 WHERE id = %s', (world.job,))
    assert _delivered(listen, db) == [f'p:{world.pid}']


def test_writes_the_overview_does_not_show_stay_silent(db, listen, world):
    db.execute(
        'UPDATE jobs SET reservation_id = coalesce(reservation_id, gen_random_uuid()), credits_reserved = 40'
        ' WHERE id = %s',
        (world.job,),
    )
    db.execute("UPDATE jobs SET phase = 'searching', updated_at = now() WHERE id = %s", (world.job,))
    db.execute(
        'UPDATE outreach_stops SET attempts = attempts + 1, next_attempt_at = now() WHERE project_id = %s', (world.pid,)
    )
    db.execute(
        'INSERT INTO notifications (user_id, organization_id, kind, href, data)'
        " VALUES (%s, default_organization(%s), 'search_done', '/', %s)",
        (world.uid, world.uid, Jsonb({})),
    )
    assert _delivered(listen, db) == []


# One UPDATE per table of a column no Home query reads: a backfill of such a column sends nothing (#1112).
SILENT: dict[str, str] = {
    'chat_sessions': "UPDATE chat_sessions SET goal = 'campaign' WHERE id = %(session)s",
    'lists': 'UPDATE lists SET last_search = \'{"q": 1}\' WHERE id = %(list)s',
    'list_rows': 'UPDATE list_rows SET relevance_score = 5, cells = cells || \'{"note": "x"}\' WHERE id = %(row)s',
    'outreach_campaigns': "UPDATE outreach_campaigns SET rationale = 'x' WHERE id = %(campaign)s",
    'outreach_campaign_sends': "UPDATE outreach_campaign_sends SET error_code = 'X', subject = 'y' WHERE id = %(send)s",
    'outreach_campaign_targets': "UPDATE outreach_campaign_targets SET first_name = 'Z', role = 'CTO' WHERE id = %(target)s",
    'notifications': 'UPDATE notifications SET email_pending = true, emailed_at = now() WHERE user_id = %(uid)s',
    'member': 'UPDATE member SET role = \'owner\' WHERE "userId" = %(uid)s',
}


def test_every_table_the_home_updates_notify_has_a_silent_write():
    assert set(SILENT) == set(WRITES) - {'jobs', 'saved_views', 'outreach_stops', 'instantly_leads', 'audience_aliases'}


@pytest.mark.parametrize('table', SILENT)
def test_an_update_of_a_column_the_home_does_not_read_is_silent(db, listen, world, table):
    db.execute(SILENT[table], vars(world))
    assert _delivered(listen, db) == []


def _other_project(db, w: World) -> str:
    return db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id',
        (w.org, w.uid, 'Other client'),
    ).fetchone()['id']


def _other_member(db) -> tuple[str, str]:
    uid = _user(db)
    return uid, member(db, uid).org_id


def _other_campaign(db, w: World) -> str:
    return _campaign(db, w.uid, 'Other', list_id=w.list, waiting=1)


# One UPDATE per watched column: (table, column) -> (statement, topics). A topic is `p` (the project), `o` (another
# project), `u` (the user) or `v` (another user). organization_id of a chat, list or campaign is not here: a composite
# foreign key to its project keeps it fixed, so it never changes.
def _update(table: str, sets: str, where: str) -> str:
    return f'UPDATE {table} SET {sets} WHERE {where}'


Case = tuple[str, str, str, str]  # table, column, statement, topics (letters above)
WATCHED: list[Case] = [
    ('chat_sessions', 'title', _update('chat_sessions', "title = 'T'", 'id = %(session)s'), 'p'),
    ('chat_sessions', 'pinned_at', _update('chat_sessions', 'pinned_at = now()', 'id = %(session)s'), 'p'),
    (
        'chat_sessions',
        'updated_at',
        _update('chat_sessions', "updated_at = now() + interval '1 s'", 'id = %(session)s'),
        'p',
    ),
    ('chat_sessions', 'project_id', _update('chat_sessions', 'project_id = %(other)s', 'id = %(session)s'), 'po'),
    ('lists', 'name', _update('lists', "name = 'N'", 'id = %(list)s'), 'p'),
    ('lists', 'entity', _update('lists', "entity = 'company'", 'id = %(list)s'), 'p'),
    ('lists', 'mode', _update('lists', "mode = 'enrich'", 'id = %(list)s'), 'p'),
    ('lists', 'deleted_at', _update('lists', 'deleted_at = now()', 'id = %(list)s'), 'p'),
    ('lists', 'chat_session_id', _update('lists', 'chat_session_id = %(session)s', 'id = %(list)s'), 'p'),
    ('lists', 'user_id', _update('lists', 'user_id = %(v)s', 'id = %(list)s'), 'p'),
    ('lists', 'project_id', _update('lists', 'project_id = %(other)s', 'id = %(list)s'), 'po'),
    ('list_rows', 'list_id', _update('list_rows', "list_id = %(list2)s, dedupe_key = 'moved'", 'id = %(row)s'), 'p'),
    ('list_rows', 'excluded', _update('list_rows', 'excluded = true', 'id = %(row)s'), 'p'),
    ('list_rows', 'cells', _update('list_rows', 'cells = cells || \'{"email": "a@x.test"}\'', 'id = %(row)s'), 'p'),
    (
        'list_rows',
        'cells',
        _update('list_rows', 'cells = cells || \'{"email_status": "invalid"}\'', 'id = %(row)s'),
        'p',
    ),
    ('outreach_campaigns', 'name', _update('outreach_campaigns', "name = 'N'", 'id = %(campaign)s'), 'p'),
    ('outreach_campaigns', 'status', _update('outreach_campaigns', "status = 'paused'", 'id = %(campaign)s'), 'p'),
    (
        'outreach_campaigns',
        'steps',
        _update('outreach_campaigns', 'steps = \'[{"channel": "email"}]\'', 'id = %(campaign)s'),
        'p',
    ),
    (
        'outreach_campaigns',
        'activated_at',
        _update('outreach_campaigns', 'activated_at = now()', 'id = %(campaign)s'),
        'p',
    ),
    ('outreach_campaigns', 'list_id', _update('outreach_campaigns', 'list_id = NULL', 'id = %(campaign)s'), 'p'),
    ('outreach_campaigns', 'owner_id', _update('outreach_campaigns', 'owner_id = NULL', 'id = %(campaign)s'), 'p'),
    (
        'outreach_campaigns',
        'project_id',
        _update('outreach_campaigns', 'project_id = %(other)s', 'id = %(campaign)s'),
        'po',
    ),
    (
        'outreach_campaign_sends',
        'status',
        _update('outreach_campaign_sends', "status = 'skipped'", 'id = %(send)s'),
        'p',
    ),
    (
        'outreach_campaign_sends',
        'attempts',
        _update('outreach_campaign_sends', 'attempts = attempts + 1', 'id = %(send)s'),
        'p',
    ),
    ('outreach_campaign_sends', 'step', _update('outreach_campaign_sends', 'step = step + 1', 'id = %(send)s'), 'p'),
    (
        'outreach_campaign_sends',
        'channel',
        _update('outreach_campaign_sends', "channel = 'hubspot_task'", 'id = %(send)s'),
        'p',
    ),
    (
        'outreach_campaign_sends',
        'bounce_kind',
        _update('outreach_campaign_sends', "bounce_kind = 'hard', bounced_at = now()", 'id = %(send)s'),
        'p',
    ),
    (
        'outreach_campaign_sends',
        'delivered_at',
        _update('outreach_campaign_sends', 'delivered_at = now()', 'id = %(send)s'),
        'p',
    ),
    (
        'outreach_campaign_sends',
        'owner_id',
        _update('outreach_campaign_sends', 'owner_id = NULL', 'id = %(send)s'),
        'p',
    ),
    (
        'outreach_campaign_sends',
        'campaign_id',
        _update(
            'outreach_campaign_sends', 'campaign_id = %(camp2)s, target_id = %(target2)s, step = 2', 'id = %(send)s'
        ),
        'p',
    ),
    (
        'outreach_campaign_sends',
        'target_id',
        _update('outreach_campaign_sends', 'target_id = %(target3)s', 'id = %(send)s'),
        'p',
    ),
    (
        'outreach_campaign_targets',
        'status',
        _update('outreach_campaign_targets', "status = 'replied'", 'id = %(target)s'),
        'p',
    ),
    (
        'outreach_campaign_targets',
        'next_step',
        _update('outreach_campaign_targets', 'next_step = next_step + 1', 'id = %(target)s'),
        'p',
    ),
    (
        'outreach_campaign_targets',
        'replied_at',
        _update('outreach_campaign_targets', 'replied_at = now()', 'id = %(target)s'),
        'p',
    ),
    (
        'outreach_campaign_targets',
        'email',
        _update('outreach_campaign_targets', "email = 'new@x.test'", 'id = %(target)s'),
        'p',
    ),
    (
        'outreach_campaign_targets',
        'linkedin_alias',
        _update('outreach_campaign_targets', "linkedin_url = 'https://www.linkedin.com/in/new-one'", 'id = %(target)s'),
        'p',
    ),
    (
        'outreach_campaign_targets',
        'owner_id',
        _update('outreach_campaign_targets', 'owner_id = NULL', 'id = %(target)s'),
        'p',
    ),
    (
        'outreach_campaign_targets',
        'campaign_id',
        _update('outreach_campaign_targets', "campaign_id = %(camp2)s, email = 'moved@x.test'", 'id = %(target3)s'),
        'p',
    ),
    ('notifications', 'read_at', _update('notifications', 'read_at = now()', 'user_id = %(uid)s'), 'u'),
    ('notifications', 'data', _update('notifications', 'data = \'{"name": "Other"}\'', 'user_id = %(uid)s'), 'u'),
    ('notifications', 'kind', _update('notifications', "kind = 'reply_urgent'", 'user_id = %(uid)s'), 'u'),
    ('notifications', 'user_id', _update('notifications', 'user_id = %(v)s', 'user_id = %(uid)s'), 'uv'),
    (
        'notifications',
        'organization_id',
        _update('notifications', 'organization_id = %(vorg)s', 'user_id = %(uid)s'),
        'u',
    ),
    ('member', 'userId', _update('member', '"userId" = %(v)s', '"userId" = %(uid)s'), 'uv'),
    ('member', 'organizationId', _update('member', '"organizationId" = %(vorg)s', '"userId" = %(uid)s'), 'u'),
]


def _watched_columns(db, table: str) -> set[str]:
    """The columns of the NEW row in the WHEN clause of the table's home_touch_update trigger."""
    sql = db.execute(
        'SELECT pg_get_triggerdef(t.oid) AS d FROM pg_trigger t'
        " WHERE t.tgrelid = %s::regclass AND t.tgname = 'home_touch_update'",
        (table,),
    ).fetchone()['d']
    return set(re.findall(r'new\.\"?(\w+)\"?', sql.split('WHEN', 1)[1]))


def test_every_watched_column_has_a_case(db):
    fixed = {'chat_sessions', 'lists', 'outreach_campaigns'}
    for table in {t for t, *_ in WATCHED}:
        covered = {c for t, c, *_ in WATCHED if t == table} | ({'organization_id'} if table in fixed else set())
        assert covered == _watched_columns(db, table), table


@pytest.mark.parametrize('case', WATCHED, ids=lambda c: f'{c[0]}.{c[1]}')
def test_an_update_of_a_watched_column_notifies(db, listen, world, case):
    _, _, statement, topics = case
    other = _other_project(db, world)
    v, vorg = _other_member(db)
    camp2 = _other_campaign(db, world)
    list2 = _list(db, world.uid, 'Second', pid=world.pid)
    target3 = db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status)'
        " VALUES (%s, %s, 'third@x.test', 'active') RETURNING id",
        (world.campaign, world.uid),
    ).fetchone()['id']
    target2 = db.execute('SELECT id FROM outreach_campaign_targets WHERE campaign_id = %s', (camp2,)).fetchone()['id']
    _delivered(listen, db)
    db.execute(
        statement,
        {
            **vars(world),
            'other': other,
            'v': v,
            'vorg': vorg,
            'camp2': camp2,
            'target2': target2,
            'list2': list2,
            'target3': target3,
        },
    )
    named = {'p': f'p:{world.pid}', 'o': f'p:{other}', 'u': f'u:{world.uid}', 'v': f'u:{v}'}
    assert sorted(_delivered(listen, db)) == sorted(named[t] for t in topics)


def test_a_list_row_notifies_when_its_email_verdict_changes_inside_the_cells(db, listen, world):
    db.execute('UPDATE list_rows SET cells = cells || \'{"email_status": "invalid"}\' WHERE id = %s', (world.row,))
    assert _delivered(listen, db) == [f'p:{world.pid}']


def test_a_lead_notifies_only_when_it_enters_or_leaves_the_hold(db, listen, world):
    moving = lead(db, world.pid, 'moving@client.test', 'publishing')
    _delivered(listen, db)
    db.execute("UPDATE instantly_leads SET outcome = 'accepted' WHERE id = %s", (moving,))
    assert _delivered(listen, db) == []
    db.execute("UPDATE instantly_leads SET outcome = 'skipped' WHERE id = %s", (moving,))
    assert _delivered(listen, db) == [f'p:{world.pid}']


def test_a_row_that_moves_project_refreshes_both(db, listen, world):
    other = db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id',
        (world.org, world.uid, 'Other client'),
    ).fetchone()['id']
    db.execute('UPDATE lists SET project_id = %s WHERE id = %s', (other, world.list))
    assert sorted(_delivered(listen, db)) == sorted([f'p:{world.pid}', f'p:{other}'])


def test_a_deleted_project_notifies_its_topic(db, listen, world):
    db.execute('DELETE FROM projects WHERE id = %s', (world.pid,))
    assert _delivered(listen, db) == [f'p:{world.pid}']


def test_deleting_a_saved_view_notifies_its_topic(db, listen, world):
    db.execute('DELETE FROM saved_views WHERE id = %s', (world.view,))
    assert _delivered(listen, db) == [f'p:{world.pid}']
