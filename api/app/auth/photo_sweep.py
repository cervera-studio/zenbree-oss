"""The daily sweep of the photos bucket (#503). The account delete removes a photo best effort, so a bucket that was
down then leaves a deleted user's photo behind, and a failed ``user.image`` save leaves a live user's upload that no
profile points to. Both are personal data nobody can reach: this deletes them."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

import structlog

from app.core.db import get_conn
from app.core.object_store import photo_store
from app.core.system_reasons import SystemReason

logger = structlog.get_logger()

_KEY = re.compile(r'avatars/(?P<user>[^/]+)/(?P<token>[^/]+)\.webp')
#: A fresh upload is not yet the user's ``image``: the web saves it right after the PUT.
GRACE = timedelta(hours=1)
#: A run that would delete more than this share of the objects, and more than MAX_ABSOLUTE, deletes nothing.
MAX_SHARE, MAX_ABSOLUTE = 0.2, 5


def _named(key: str, images: dict[str, str | None]) -> bool:
    """Whether the user's ``image`` names this object."""
    m = _KEY.fullmatch(key)
    image = images.get(m['user']) if m else None
    return bool(m and image and image.endswith(f'/v1/avatars/{m["user"]}/{m["token"]}'))


def _images(user_ids: list[str]) -> dict[str, str | None]:
    with get_conn() as conn:
        rows = conn.execute('SELECT id::text AS id, image FROM "user" WHERE id::text = ANY(%s)', (user_ids,)).fetchall()
    return {r['id']: r['image'] for r in rows}


async def sweep_photos(now: datetime | None = None) -> int:
    """Deletes the objects of users that no longer exist and those a live user's ``image`` does not name, but only
    those older than GRACE. Returns how many."""
    store = photo_store()
    if store is None:
        return 0
    cutoff = (now or datetime.now(UTC)) - GRACE
    everything = await store.list('avatars/')
    old = [s for s in everything if s.modified < cutoff]
    images = _images(sorted({m['user'] for s in old if (m := _KEY.fullmatch(s.key))}))
    doomed = [s.key for s in old if not _named(s.key, images)]
    # Fail closed: a read of "user" that came back short (a scope mistake) would make live photos look orphaned.
    if len(doomed) > MAX_ABSOLUTE and len(doomed) > MAX_SHARE * len(everything):
        logger.error('photos.sweep_refused', doomed=len(doomed), objects=len(everything))
        return 0
    for key in doomed:
        await store.delete(key)
    deleted = len(doomed)
    logger.info('photos.sweep', deleted=deleted)
    return deleted


def register_sweep(system_task) -> None:
    """The daily task, declared here (the one module ``PHOTO_SWEEP`` allows) and registered by app/worker.py. It reads
    only which user ids exist, no organization's data."""

    @system_task(SystemReason.PHOTO_SWEEP, name='sweep_profile_photos', soft_time_limit=240, time_limit=270)
    def sweep_profile_photos() -> dict:
        return {'deleted': asyncio.run(sweep_photos())}
