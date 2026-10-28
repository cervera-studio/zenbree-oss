"""Persistence for a user's single personal memory document."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from app.core.db import get_conn


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    user_id: UUID
    body: str
    locked: bool
    created_at: datetime | None
    updated_at: datetime | None


_COLUMNS = 'user_id, body, locked, created_at, updated_at'


def read(user_id: UUID) -> MemoryDocument:
    """Return the user's document, or its unsaved empty default."""
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT {_COLUMNS} FROM user_memory_doc WHERE user_id = %s',
            (user_id,),
        ).fetchone()
    if row is None:
        return MemoryDocument(user_id, '', False, None, None)
    return MemoryDocument(**row)


def save(user_id: UUID, body: str) -> MemoryDocument:
    """Insert or replace the body while preserving the existing lock."""
    with get_conn() as conn:
        row = conn.execute(
            f"""INSERT INTO user_memory_doc (user_id, body)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                    SET body = EXCLUDED.body, updated_at = now()
                RETURNING {_COLUMNS}""",
            (user_id, body),
        ).fetchone()
    if row is None:
        raise RuntimeError('memory save did not return a row')
    return MemoryDocument(**row)


def save_with_lock(user_id: UUID, body: str, locked: bool) -> MemoryDocument:
    """Insert or replace body and lock together: the user's own edit."""
    with get_conn() as conn:
        row = conn.execute(
            f"""INSERT INTO user_memory_doc (user_id, body, locked)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                    SET body = EXCLUDED.body, locked = EXCLUDED.locked, updated_at = now()
                RETURNING {_COLUMNS}""",
            (user_id, body, locked),
        ).fetchone()
    if row is None:
        raise RuntimeError('memory save did not return a row')
    return MemoryDocument(**row)


def set_locked(user_id: UUID, locked: bool) -> MemoryDocument:
    """Insert or update the lock without changing an existing body."""
    with get_conn() as conn:
        row = conn.execute(
            f"""INSERT INTO user_memory_doc (user_id, locked)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                    SET locked = EXCLUDED.locked, updated_at = now()
                RETURNING {_COLUMNS}""",
            (user_id, locked),
        ).fetchone()
    if row is None:
        raise RuntimeError('memory lock update did not return a row')
    return MemoryDocument(**row)


def edit(user_id: UUID, change: Callable[[MemoryDocument], str]) -> MemoryDocument:
    """Read-modify-write the body under a row lock, so two concurrent edits
    cannot drop each other's entries. ``change`` raising rolls back."""
    with get_conn() as conn:
        return edit_in(conn, user_id, change)


def edit_in(conn: psycopg.Connection[dict], user_id: UUID, change: Callable[[MemoryDocument], str]) -> MemoryDocument:
    """``edit`` inside the caller's transaction, for callers that write more in it."""
    conn.execute('INSERT INTO user_memory_doc (user_id) VALUES (%s) ON CONFLICT DO NOTHING', (user_id,))
    current = conn.execute(
        f'SELECT {_COLUMNS} FROM user_memory_doc WHERE user_id = %s FOR UPDATE', (user_id,)
    ).fetchone()
    if current is None:
        raise RuntimeError('memory edit did not find the row it locked')
    row = conn.execute(
        f"""UPDATE user_memory_doc SET body = %s, updated_at = now()
            WHERE user_id = %s RETURNING {_COLUMNS}""",
        (change(MemoryDocument(**current)), user_id),
    ).fetchone()
    if row is None:
        raise RuntimeError('memory edit did not return a row')
    return MemoryDocument(**row)
