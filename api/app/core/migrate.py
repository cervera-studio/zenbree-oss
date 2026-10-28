"""Schema migrations (#268): api/db/<timestamp>_<name>.sql, applied once each, in name order.

`python -m app.core.migrate` runs them against DATABASE_URL (compose service `migrate`, the deploy, the
verify stack). The SQL tests call `migrate(conn)` on a throwaway schema, so every database is built the same way.
"""

import hashlib
import logging
import re
from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

MIGRATIONS = Path(__file__).resolve().parents[2] / 'db'
_LOCK = 268  # pg advisory lock key: one runner per database at a time
_NAME = re.compile(r'\d{14}_\w+\.sql')
logger = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    pass


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS) -> list[str]:
    """Apply the pending migrations, each in its own transaction with its bookkeeping row. Returns their names.

    Raises MigrationError, before running anything, when an applied migration was edited or a file is misnamed.
    An applied migration with no file only warns: an older image (a rollback) runs with fewer files."""
    cur = conn.cursor(row_factory=tuple_row)
    cur.execute('SELECT pg_advisory_lock(%s)', (_LOCK,))
    try:
        # A database built before migrations (prod, old volumes) has no table yet: the loop below then runs the
        # baseline on it, which is idempotent, so it only adds what that database was missing.
        cur.execute(
            'CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, checksum text NOT NULL,'
            ' applied_at timestamptz NOT NULL DEFAULT now())'
        )
        files = {p.name: p for p in sorted(directory.glob('*.sql'))}
        misnamed = [n for n in files if not _NAME.fullmatch(n)]
        if misnamed:  # e.g. a schema_*.sql from a branch older than #268: it would never run
            raise MigrationError(f'not a migration name (<YYYYMMDDHHMMSS>_<name>.sql): {misnamed}')
        cur.execute('SELECT name, checksum FROM schema_migrations')
        applied: dict[str, str] = dict(cur.fetchall())
        if missing := sorted(applied.keys() - files.keys()):
            logger.warning('applied migrations have no file here (older image?), schema stays forward: %s', missing)
        changed = sorted(n for n, c in applied.items() if n in files and _checksum(files[n]) != c)
        if changed:
            raise MigrationError(f'applied migrations were edited: {changed}. Add a new migration instead.')
        done = []
        for name, path in files.items():
            if name in applied:
                continue
            with conn.transaction():
                cur.execute(path.read_text())
                cur.execute('INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)', (name, _checksum(path)))
            done.append(name)
        return done
    finally:
        cur.execute('SELECT pg_advisory_unlock(%s)', (_LOCK,))


if __name__ == '__main__':
    from app.core.config import settings

    # A migration waiting on a lock held by live traffic fails the deploy instead of stalling the app.
    with psycopg.connect(settings.database_url, autocommit=True, options='-c lock_timeout=10s') as c:
        names = migrate(c)
    print(f'migrations applied: {", ".join(names) or "none"}')  # noqa: T201 — CLI output, read in the deploy log
