"""Database behavior for per-user memory documents."""

import uuid

import psycopg
import pytest

from app.memory import store
from tests.conftest import act_in_own_org
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db

needs_db = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: memory SQL tests NOT run')


@needs_db
def test_read_returns_empty_default_without_inserting(db):
    user_id = uuid.UUID(_user(db))
    act_in_own_org(db, user_id)  # the user's memory routes

    document = store.read(user_id)

    assert (document.user_id, document.body, document.locked) == (user_id, '', False)
    assert document.created_at is None
    assert document.updated_at is None
    assert db.execute('SELECT count(*) AS n FROM user_memory_doc WHERE user_id = %s', (user_id,)).fetchone()['n'] == 0


@needs_db
def test_save_inserts_and_updates_empty_or_nonempty_body(db):
    user_id = uuid.UUID(_user(db))
    act_in_own_org(db, user_id)  # the user's memory routes

    first = store.save(user_id, '')
    updated = store.save(user_id, 'Useful context')

    assert first.body == ''
    assert updated.body == 'Useful context'
    assert updated.locked is False
    assert updated.created_at == first.created_at
    assert updated.updated_at >= first.updated_at


@needs_db
def test_memory_documents_are_isolated_by_user(db):
    first_user = uuid.UUID(_user(db))
    second_user = uuid.UUID(_user(db))

    act_in_own_org(db, first_user)
    store.save(first_user, 'private to first user')
    assert store.read(first_user).body == 'private to first user'

    act_in_own_org(db, second_user)
    assert store.read(second_user).body == ''


@needs_db
def test_database_enforces_8000_character_limit(db):
    user_id = uuid.UUID(_user(db))
    act_in_own_org(db, user_id)  # the user's memory routes

    assert len(store.save(user_id, 'x' * 8000).body) == 8000
    with pytest.raises(psycopg.errors.CheckViolation):
        store.save(user_id, 'x' * 8001)
    assert len(store.read(user_id).body) == 8000


@needs_db
def test_lock_upsert_and_body_upsert_preserve_each_other(db):
    user_id = uuid.UUID(_user(db))
    act_in_own_org(db, user_id)  # the user's memory routes

    locked_empty = store.set_locked(user_id, True)
    assert (locked_empty.body, locked_empty.locked) == ('', True)

    saved = store.save(user_id, 'saved while locked')
    assert (saved.body, saved.locked) == ('saved while locked', True)

    unlocked = store.set_locked(user_id, False)
    assert (unlocked.body, unlocked.locked) == ('saved while locked', False)
