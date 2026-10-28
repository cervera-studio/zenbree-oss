"""Every notification names its organization (#702, PR 3 of the organization-scoping run).

Needs TEST_DATABASE_URL. The turn tests run on the migrated template; the migration test builds the schema from every
migration but this one, seeds hand-made rows of each href shape, then runs it.
"""

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.core.migrate import MIGRATIONS, migrate
from app.notifications import service as n
from tests.conftest import empty_database, migrated_database
from tests.test_tenant_rows_sql import _insert, _org, _project
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: notifications org test NOT run')
NAME = '20261013175800_notifications_name_their_organization.sql'
OLD = datetime.now(UTC) - timedelta(minutes=6)  # long enough that the bell does not skip the notification


def _join(c, org, uid, role='member') -> None:
    c.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, uid, role),
    )


# ---------------------------------------------------------------------------------------------- notify_turn (#702)


@pytest.fixture
def db():
    with migrated_database() as (c, _):
        yield c


def _team_chat(c):
    """Member A created a chat with two lists in an organization where member M is a member too."""
    a, m = _user(c), _user(c)
    org = _org(c, 'team')
    _join(c, org, a, 'owner')
    _join(c, org, m)
    project = _project(c, org, a)
    sid = _insert(c, 'chat_sessions', organization_id=org, project_id=project, user_id=a, title='CEO a Milano')
    for entity, rows in (('person', 3), ('company', 2)):
        lid = _insert(
            c,
            'lists',
            organization_id=org,
            project_id=project,
            user_id=a,
            chat_session_id=sid,
            name=entity,
            entity=entity,
        )
        for i in range(rows):
            c.execute("INSERT INTO list_rows (list_id, dedupe_key, cells) VALUES (%s, %s, '{}')", (lid, f'{entity}{i}'))
    return a, m, org, sid


def test_a_turn_on_a_teammates_chat_notifies_with_the_organization_title_and_list_counts(db):
    _, m, org, sid = _team_chat(db)
    done = n.notify_turn(db, m, sid, OLD, ('search_done', {'refunded': 0, 'spent': 10, 'undelivered': 0}))
    assert done
    row = db.execute('SELECT user_id::text, organization_id::text, data FROM notifications').fetchone()
    assert row['user_id'] == m and row['organization_id'] == org
    assert row['data']['chat'] == 'CEO a Milano' and row['data']['people'] == 3 and row['data']['companies'] == 2
    assert 'balance' in row['data']


def test_a_turn_stores_nothing_when_the_user_is_not_a_live_member_of_the_chats_organization(db):
    a, _, org, sid = _team_chat(db)
    outsider, banned = _user(db), _user(db)
    _join(db, org, banned)
    db.execute('UPDATE "user" SET banned = true WHERE id = %s', (banned,))
    for uid in (outsider, banned):
        assert n.notify_turn(db, uid, sid, OLD, ('agent_question', {'reason': None})) is None
    assert db.execute('SELECT count(*) AS n FROM notifications').fetchone()['n'] == 0
    assert n.notify_turn(db, a, sid, OLD, ('agent_question', {'reason': None}))  # the creator still gets theirs


def test_a_turn_on_a_chat_that_is_gone_stores_nothing(db):
    _, m, _, _ = _team_chat(db)
    assert n.notify_turn(db, m, str(uuid.uuid4()), OLD, ('agent_question', {'reason': None})) is None
    assert db.execute('SELECT count(*) AS n FROM notifications').fetchone()['n'] == 0


def test_a_turn_on_a_chat_that_is_gone_does_not_call_notify(db, monkeypatch):
    # notify refuses a missing organization too, so the stored count alone cannot tell the guard from that refusal.
    _, m, _, _ = _team_chat(db)
    calls = []
    monkeypatch.setattr(n, 'notify', lambda *a, **k: calls.append((a, k)))
    assert n.notify_turn(db, m, str(uuid.uuid4()), OLD, ('agent_question', {'reason': None})) is None
    assert calls == []


def test_a_notification_cannot_be_stored_without_an_organization(db):
    uid = _user(db)
    with pytest.raises(psycopg.errors.NotNullViolation):
        db.execute("INSERT INTO notifications (user_id, kind, href) VALUES (%s, 'agent_question', '/')", (uid,))


def test_the_table_label_is_kept_and_no_longer_says_when_set(db):
    comment = db.execute("SELECT obj_description('notifications'::regclass, 'pg_class') AS c").fetchone()['c']
    assert comment.startswith('zb:personal-in-org user_id:') and 'when set' not in comment


# ---------------------------------------------------------------------------------------------- the migration on rows


@pytest.fixture
def before(tmp_path):
    older = tmp_path / 'db'
    older.mkdir()
    for f in MIGRATIONS.glob('*.sql'):
        if f.name != NAME:
            shutil.copy(f, older / f.name)
    with empty_database() as c:
        assert migrate(c, older)
        yield c


def _old_note(c, uid, href, org=None, data=None) -> str:
    return str(
        c.execute(
            "INSERT INTO notifications (user_id, organization_id, kind, href, data) VALUES (%s, %s, 'reply', %s, %s)"
            ' RETURNING id',
            (uid, org, href, Jsonb(data or {})),
        ).fetchone()['id']
    )


def test_each_rule_places_its_rows_and_the_rest_is_deleted(before):
    c = before
    multi, solo, nobody, banned, solo2, twice = (_user(c) for _ in range(6))
    a, b = _org(c, 'a'), _org(c, 'b')
    for org, uid in ((a, multi), (b, multi), (a, solo), (b, banned), (a, solo2), (a, twice), (a, twice)):
        _join(c, org, uid)
    c.execute('UPDATE "user" SET banned = true WHERE id = %s', (banned,))
    pa, pb = _project(c, a, multi), _project(c, b, multi)
    chat = _insert(c, 'chat_sessions', organization_id=a, project_id=pa, user_id=multi)
    chat_b = _insert(c, 'chat_sessions', organization_id=b, project_id=pb, user_id=multi)
    camp = _insert(c, 'outreach_campaigns', organization_id=b, project_id=pb, owner_id=multi, name='c')
    inbound = _insert(
        c,
        'outreach_inbound_messages',
        project_id=pb,
        owner_id=multi,
        channel='email',
        kind='reply',
        provider_message_id='m',
    )
    lst = _insert(c, 'lists', organization_id=b, project_id=pb, user_id=multi, name='l', entity='person')
    uuid_case = f'/billing/switch?org={str(b).upper()}'  # a uuid in capitals still places

    expect = {  # note id -> the organization it must have after, or None when the migration deletes it
        _old_note(c, multi, f'/billing/switch?org={b}'): b,  # rule 1
        _old_note(c, multi, uuid_case): b,  # rule 1
        _old_note(c, multi, f'/?s={chat}'): a,  # rule 2
        _old_note(c, multi, f'/inbox?m={inbound}'): b,  # rule 3
        _old_note(c, multi, f'/campaigns/{camp}'): b,  # rule 4
        _old_note(c, multi, '/billing', data={'list_id': str(lst)}): b,  # data.list_id: the chat is gone
        _old_note(c, multi, f'/?s={uuid.uuid4()}', data={'list_id': str(lst).upper()}): b,  # the href names a gone chat
        _old_note(c, multi, '/billing', data={'list_id': 'nope'}): None,  # malformed list_id: unplaced
        _old_note(c, multi, '/billing', data={'list_id': str(uuid.uuid4())}): None,  # a list that is gone
        _old_note(c, multi, f'/lists/{lst}'): None,  # no code wrote this shape: not a rule
        _old_note(c, multi, '/?s=not-a-uuid'): None,  # malformed /?s=: rule 2 leaves it, the rules below do too
        _old_note(c, twice, '/billing'): a,  # rule 5 counts organizations: two member rows in A are one
        _old_note(c, solo, f'/?s={chat_b}'): b,  # the data rules run before rule 5, which would say A
        _old_note(c, solo, '/settings/integrations'): a,  # rule 5: one live membership
        _old_note(c, solo, '/billing'): a,  # the old shape of a billing link
        _old_note(
            c, solo, f'/billing/switch?org={uuid.uuid4()}'
        ): a,  # an organization that is gone: rule 5 still holds
        _old_note(c, multi, f'/?s={uuid.uuid4()}'): None,  # a chat that is gone, several organizations
        _old_note(c, multi, '/billing/switch?org=not-a-uuid'): None,  # malformed: unplaced, migration still runs
        _old_note(c, multi, '/inbox?m=1'): None,
        _old_note(c, multi, '/billing'): None,  # rule 6: several organizations, no data
        _old_note(c, nobody, '/billing'): None,  # rule 6: no membership
        _old_note(c, banned, '/billing'): b,  # rule 5 has no ban test: today's visibility has none
        _old_note(c, solo2, '/', org=b): b,  # a row that already names an organization is not touched
    }
    # solo2 is a member of A only, but its row says B: it keeps B (the visibility filter hides it, as before).

    assert migrate(c) == [NAME]

    got = {
        str(r['id']): (str(r['organization_id']) if r['organization_id'] else None)
        for r in c.execute('SELECT id, organization_id FROM notifications')
    }
    placed = {k: v for k, v in expect.items() if v is not None}
    assert got == placed
    # rule 1 places 2, rule 2 two, rule 3 one, rule 4 one, data.list_id two, rule 5 five (a banned user, two member
    # rows in one organization), one row kept its own
    assert len(placed) == 14 and len(expect) - len(placed) == 9  # and rule 6 deletes 9
    nullable = c.execute(
        "SELECT is_nullable FROM information_schema.columns WHERE table_name = 'notifications'"
        " AND column_name = 'organization_id' AND table_schema = current_schema()"
    ).fetchone()['is_nullable']
    assert nullable == 'NO'
