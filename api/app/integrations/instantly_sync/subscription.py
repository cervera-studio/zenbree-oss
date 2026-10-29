"""The webhook Zenbree registers in a connected Instantly workspace (#930), and why a connection has none.

The beat calls :func:`ensure` once a pass, as ``ForOrg`` of the connection's organization. The row is written before
the create call, so a crash between the two leaves a ``creating`` row that the next pass retires; a webhook Instantly
then holds posts a secret no current row has, gets 401 and is disabled by Instantly. A webhook Instantly refuses
(plan, scope) is asked again once a day, and the connection is polled meanwhile: the settings card says which.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from app.admin.settings import is_disabled
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import Result, is_err, ok
from app.integrations.instantly_sync import client
from app.integrations.instantly_sync.store import Binding

logger = structlog.get_logger()

RETRY_REFUSAL = timedelta(days=1)
CHECK_EVERY = timedelta(hours=1)
STALE_CREATE = timedelta(minutes=10)
DISABLED = -1  # Instantly's webhook status after failed deliveries
_REFUSALS = {'INSTANTLY.PLAN_REQUIRED': 'plan', 'INSTANTLY.MISSING_SCOPE': 'scope', 'INSTANTLY.REJECTED': 'refused'}


def digest(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def callback_url(subscription_id: str) -> str | None:
    """Where Instantly posts, or None when this deployment has no public address (a local stack)."""
    base = settings.api_public_base_url.rstrip('/')
    return f'{base}/v1/integrations/instantly/webhooks/{subscription_id}' if base else None


async def ensure(binding: Binding, key: str, *, now: datetime) -> Result[None]:
    """One webhook for the connection when Instantly gives one. An Err is a key refusal for the caller to mark; every
    other failure is recorded on the state and leaves the connection polled."""
    current = await asyncio.to_thread(_current, binding)
    if current is not None:
        return await _check(binding, key, current, now=now)
    refused = await asyncio.to_thread(_refusal, binding)
    if refused and refused['webhook_checked_at'] and now - refused['webhook_checked_at'] < RETRY_REFUSAL:
        return ok(None)
    return await _create(binding, key, now=now)


async def _create(binding: Binding, key: str, *, now: datetime) -> Result[None]:
    if callback_url('') is None:
        await asyncio.to_thread(_refuse, binding, 'no_public_url')
        return ok(None)
    secret = secrets.token_urlsafe(32)
    sub_id = await asyncio.to_thread(_insert, binding, digest(secret))
    if sub_id is None:  # disconnected since the pass began: no webhook for a key that is gone
        return ok(None)
    url = callback_url(sub_id) or ''
    made = await client.create_webhook(key, url=url, secret=secret, name=f'Zenbree {binding.project_id[:8]}')
    if is_err(made):
        if made.error.code == 'INSTANTLY.INVALID_KEY':
            await asyncio.to_thread(_settle, binding, sub_id, remote_id=None, refusal=None)
            return made
        refusal = _REFUSALS.get(made.error.code)
        # A timeout may still have made it: that copy gets 401 from the retired row and Instantly disables it.
        await asyncio.to_thread(_settle, binding, sub_id, remote_id=None, refusal=refusal)
        logger.info('instantly.webhook.not_created', code=made.error.code, connection_id=binding.connection_id)
        return ok(None)
    if not await asyncio.to_thread(_settle, binding, sub_id, remote_id=made.value, refusal=None):
        await client.delete_webhook(key, made.value)  # a disconnect retired the row meanwhile
    return ok(None)


async def _check(binding: Binding, key: str, current: dict, *, now: datetime) -> Result[None]:
    """Retires a webhook that can no longer deliver here; the next pass creates its successor."""
    if current['workspace_id'] != binding.workspace_id or (
        current['status'] == 'creating' and now - current['created_at'] > STALE_CREATE
    ):
        await asyncio.to_thread(_retire, current['id'])
        return ok(None)
    if not current['remote_id'] or (current['checked_at'] and now - current['checked_at'] < CHECK_EVERY):
        return ok(None)
    status = await client.webhook_status(key, current['remote_id'])
    if is_err(status) and status.error.code == 'INSTANTLY.INVALID_KEY':
        return status
    if (is_err(status) and status.error.code == 'INSTANTLY.NOT_FOUND') or (
        not is_err(status) and status.value == DISABLED
    ):
        await asyncio.to_thread(_retire, current['id'])
        await client.delete_webhook(key, current['remote_id'])  # best effort: a disabled copy posts nothing
        return ok(None)
    if not is_err(status):
        await asyncio.to_thread(_checked, current['id'])
    return ok(None)


@dataclass(frozen=True, slots=True)
class Leftover:
    """What a disconnect leaves at Instantly: deleted with the old key, after the key is cleared here."""

    key: str
    remote_ids: list[str]


def retire_all(conn, project_id: str, key: str | None) -> Leftover | None:
    """Retires the project's webhooks, so a delivery is refused from now on. The caller clears the key next."""
    rows = conn.execute(
        "UPDATE instantly_webhook_subscriptions SET status = 'retired', retired_at = now()"
        " WHERE project_id = %s AND status <> 'retired' RETURNING remote_id",
        (project_id,),
    ).fetchall()
    remote = [r['remote_id'] for r in rows if r['remote_id']]
    return Leftover(key=key, remote_ids=remote) if key and remote else None


async def delete_leftover(leftover: Leftover | None) -> None:
    """Best effort: a copy that stays posts to a retired row and gets 401, and Instantly disables it. The kill switch
    stops this call too."""
    if leftover is None or await asyncio.to_thread(is_disabled, 'vendor_disabled:instantly'):
        return
    for remote_id in leftover.remote_ids:
        await client.delete_webhook(leftover.key, remote_id)


def delivered(conn, subscription_id: str) -> None:
    """An authenticated delivery came: from now on the card may say live."""
    conn.execute(
        "UPDATE instantly_webhook_subscriptions SET status = 'live', last_delivery_at = now()"
        " WHERE id = %s AND status <> 'retired'",
        (subscription_id,),
    )


def _current(binding: Binding) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            'SELECT id::text, workspace_id, remote_id, status, created_at, checked_at'
            " FROM instantly_webhook_subscriptions WHERE connection_id = %s AND status <> 'retired'",
            (binding.connection_id,),
        ).fetchone()


def _refusal(binding: Binding) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            # A reconnect (a new key, perhaps a new plan) asks again at the next pass, not a day later.
            'SELECT s.webhook_refusal, s.webhook_checked_at FROM instantly_sync_state s'
            ' JOIN instantly_connections c ON c.id = s.connection_id'
            ' WHERE s.connection_id = %s AND s.webhook_refusal IS NOT NULL AND s.webhook_checked_at > c.connected_at',
            (binding.connection_id,),
        ).fetchone()


def _insert(binding: Binding, secret_sha256: bytes) -> str | None:
    """The new row's id, or None when the connection is no longer connected."""
    with get_conn() as conn:
        row = conn.execute(
            'INSERT INTO instantly_webhook_subscriptions (organization_id, project_id, connection_id, workspace_id,'
            ' secret_sha256) SELECT %s, %s, %s, %s, %s FROM instantly_connections'
            " WHERE id = %s AND status = 'connected' RETURNING id::text",
            (
                binding.organization_id,
                binding.project_id,
                binding.connection_id,
                binding.workspace_id,
                secret_sha256,
                binding.connection_id,
            ),
        ).fetchone()
    return row['id'] if row else None


def _settle(binding: Binding, sub_id: str, *, remote_id: str | None, refusal: str | None) -> bool:
    """False when the row was retired meanwhile (a disconnect), so the webhook Instantly made is not ours to keep."""
    kept = True
    with get_conn() as conn:
        if remote_id:
            kept = bool(
                conn.execute(
                    'UPDATE instantly_webhook_subscriptions SET remote_id = %s, checked_at = now(),'
                    " status = CASE WHEN status = 'creating' THEN 'registered' ELSE status END"
                    " WHERE id = %s AND status <> 'retired' RETURNING id",
                    (remote_id, sub_id),
                ).fetchone()
            )
        else:
            conn.execute(
                "UPDATE instantly_webhook_subscriptions SET status = 'retired', retired_at = now() WHERE id = %s",
                (sub_id,),
            )
    _refuse(binding, refusal)
    return kept


def _refuse(binding: Binding, refusal: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            'UPDATE instantly_sync_state SET webhook_refusal = %s, webhook_checked_at = now() WHERE connection_id = %s',
            (refusal, binding.connection_id),
        )


def _retire(sub_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE instantly_webhook_subscriptions SET status = 'retired', retired_at = now() WHERE id = %s",
            (sub_id,),
        )


def _checked(sub_id: str) -> None:
    with get_conn() as conn:
        conn.execute('UPDATE instantly_webhook_subscriptions SET checked_at = now() WHERE id = %s', (sub_id,))
