"""#503: the daily sweep deletes the photo objects nobody can reach and keeps the ones a profile names."""

import uuid
from datetime import UTC, datetime, timedelta

from app.auth import photo_sweep
from app.core.config import settings
from app.worker import celery_app
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_photo import FakeStore

pytestmark = needs_db
OLD = datetime.now(UTC) - timedelta(days=2)


def _put(store, uid, token, modified=OLD):
    key = f'avatars/{uid}/{token}.webp'
    store.objects[key] = b'x'
    store.modified[key] = modified
    return key


def test_it_keeps_the_current_photo_and_deletes_orphans_of_live_and_deleted_users(db, monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(photo_sweep, 'photo_store', lambda: store)
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.example.com')
    ada, bo = _user(db), _user(db)
    gone = str(uuid.uuid4())
    db.execute(
        'UPDATE "user" SET image = %s WHERE id = %s',
        (f'https://api.example.com/v1/avatars/{ada}/current-token-0001', ada),
    )
    db.execute('UPDATE "user" SET image = %s WHERE id = %s', ('https://lh3.googleusercontent.com/a', bo))
    current = _put(store, ada, 'current-token-0001')
    _put(store, ada, 'failed-save-token-02')  # an upload whose user.image save failed
    _put(store, bo, 'provider-photo-token')  # bo uses a provider photo
    _put(store, gone, 'deleted-user-token-1')  # the account delete could not reach the bucket
    fresh = _put(store, ada, 'uploading-right-now', modified=datetime.now(UTC))  # inside the grace period

    assert celery_app.tasks['sweep_profile_photos']()['deleted'] == 3
    assert sorted(store.objects) == sorted([current, fresh])


def test_a_short_read_of_user_deletes_nothing(db, monkeypatch):
    """A scope mistake that hides rows of "user" must not make live users' photos look orphaned."""
    store = FakeStore()
    monkeypatch.setattr(photo_sweep, 'photo_store', lambda: store)
    monkeypatch.setattr(photo_sweep, '_images', lambda ids: {})
    for _ in range(8):
        _put(store, _user(db), 'a-live-users-token-1')
    _put(store, uuid.uuid4(), 'x' * 20)
    assert celery_app.tasks['sweep_profile_photos']()['deleted'] == 0
    assert len(store.objects) == 9
