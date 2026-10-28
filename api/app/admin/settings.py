"""Admin kill switches (`settings` table), read with a 30s per-process cache.

    from app.admin.settings import is_disabled
    if is_disabled(f'vendor_disabled:{provider}'): ...

A flip takes effect everywhere within 30s (each api/worker process caches).
"""

import time

import psycopg
import structlog

from app.core.db import get_conn

logger = structlog.get_logger()
TTL_SECONDS = 30.0
_cache: tuple[float, dict] = (0.0, {})


def all_settings() -> dict:
    global _cache
    at, values = _cache
    if time.monotonic() - at < TTL_SECONDS:
        return values
    try:
        with get_conn() as conn:
            values = {r['key']: r['value'] for r in conn.execute('SELECT key, value FROM settings')}
    except psycopg.Error as e:
        # Fail open: no table yet / DB blip must not block every tool.
        logger.warning('admin.settings.read_failed', error=str(e)[:200])
    _cache = (time.monotonic(), values)
    return values


def is_disabled(key: str) -> bool:
    return all_settings().get(key) is True


def invalidate() -> None:
    global _cache
    _cache = (0.0, {})


def vendor_disabled_now(provider: str) -> bool:
    """Admin kill switch, read now (no cache): ``settings`` row ``vendor_disabled:<provider>`` with a
    truthy value. The table belongs to m1-billing-admin; absent = no block."""
    try:
        with get_conn() as conn:
            row = conn.execute('SELECT value FROM settings WHERE key = %s', (f'vendor_disabled:{provider}',)).fetchone()
    except psycopg.errors.UndefinedTable:
        return False
    return bool(row) and row['value'] not in (None, False, 0, '', 'false', '0')
