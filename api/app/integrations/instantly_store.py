"""A project's Instantly connection (#929): one workspace, one encrypted API key.

The key is Fernet-encrypted (``token_crypto``) and read back only to call Instantly. No function here returns it to
a route: a route reads :func:`visible` and :func:`api_key` keeps the plaintext inside the API. Any member of the
organization enrolls through a project's connection; whoever connected it, or an admin, replaces or disconnects it
(the rule of ``project_senders``).
"""

from __future__ import annotations

from datetime import datetime

import psycopg

from app.auth.middleware import CurrentUser
from app.core.errors import Result, err, is_err, ok
from app.integrations import instantly_client, token_crypto
from app.integrations.instantly_client import Workspace

HELD = ('publishing', 'accepted', 'uncertain')


def row(conn, project_id: str) -> dict | None:
    return conn.execute(
        'SELECT id, project_id, organization_id, connected_by, name, workspace_id, workspace_name,'
        ' status, status_detail, connected_at, api_key_encrypted FROM instantly_connections WHERE project_id = %s',
        (project_id,),
    ).fetchone()


def visible(connection: dict | None) -> dict:
    """What the browser may read of a connection: never the key, not even encrypted."""
    if connection is None or connection['status'] == 'disconnected':
        return {
            'connected': False,
            'status': 'disconnected',
            'name': None,
            'workspace_name': None,
            'status_detail': None,
        }
    return {
        'connected': connection['status'] == 'connected',
        'status': connection['status'],
        'name': connection['name'],
        'workspace_name': connection['workspace_name'],
        'status_detail': connection['status_detail'],
    }


def may_manage(connection: dict | None, user: CurrentUser) -> bool:
    return (
        connection is None
        or connection['status'] == 'disconnected'
        or (connection['connected_by'] is not None and str(connection['connected_by']) == user.id)
        or user.org_role == 'admin'
    )


def api_key(connection: dict | None) -> Result[str]:
    """The plaintext key of a connected workspace, for one call to Instantly."""
    if not connection or connection['status'] != 'connected' or not connection['api_key_encrypted']:
        return err('INSTANTLY.NOT_CONNECTED', detail='connect Instantly first')
    return token_crypto.decrypt(connection['api_key_encrypted'])


async def validated(key: str) -> Result[Workspace]:
    """The workspace the key opens, with its campaigns readable: a key that may not read campaigns is refused now, not
    when the user picks one."""
    found = await instantly_client.workspace(key)
    if is_err(found):
        return found
    probe = await instantly_client.list_campaigns(key)
    return found if not is_err(probe) else probe


def store(conn, *, user: CurrentUser, project_id: str, name: str, key: str, workspace: Workspace) -> Result[None]:
    encrypted = token_crypto.encrypt(key)
    if is_err(encrypted):
        return encrypted
    try:
        conn.execute(
            """INSERT INTO instantly_connections
                   (organization_id, project_id, connected_by, name, api_key_encrypted, workspace_id, workspace_name,
                    status, status_detail, connected_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'connected', NULL, now(), now())
               ON CONFLICT (project_id) DO UPDATE SET
                   connected_by = EXCLUDED.connected_by, name = EXCLUDED.name,
                   api_key_encrypted = EXCLUDED.api_key_encrypted, workspace_id = EXCLUDED.workspace_id,
                   workspace_name = EXCLUDED.workspace_name,
                   status = 'connected', status_detail = NULL, connected_at = now(), updated_at = now()""",
            (
                user.org_id,
                project_id,
                user.id,
                name,
                encrypted.value,
                workspace.id,
                workspace.name or None,
            ),
        )
    except psycopg.errors.ForeignKeyViolation:
        return err('PROJECT.NOT_FOUND', detail='project not found')
    return ok(None)


def lock_connected(conn, connection_id: str, connected_at: datetime) -> bool:
    """Locks the connection while it is still connected with the key of ``connected_at``. The lock waits for a connect
    in progress, so a write after it never lands between a reconnect and the re-arm of its stops."""
    return (
        conn.execute(
            "SELECT 1 FROM instantly_connections WHERE id = %s AND status = 'connected' AND connected_at = %s"
            ' FOR NO KEY UPDATE',
            (connection_id, connected_at),
        ).fetchone()
        is not None
    )


def mark_error(conn, connection_id: str, detail: str) -> None:
    """Instantly refused the key: the settings card offers Reconnect, and enrollments wait. Called through
    ``stop_outbox.refuse_key``, which also fails the pending stops."""
    conn.execute(
        "UPDATE instantly_connections SET status = 'error', status_detail = %s, updated_at = now() WHERE id = %s",
        (detail[:500], connection_id),
    )


def disconnect(conn, project_id: str) -> None:
    """Clears the key and keeps the row, so the leads Instantly holds stay on record (#931)."""
    conn.execute(
        "UPDATE instantly_connections SET status = 'disconnected', status_detail = NULL, api_key_encrypted = '',"
        ' updated_at = now() WHERE project_id = %s',
        (project_id,),
    )
