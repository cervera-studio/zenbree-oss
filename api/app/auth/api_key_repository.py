"""Postgres adapter for ``user_api_keys`` , rewritten from the
Supabase one to plain SQL.

Owns every write to the table plus the organization-scoped read behind the
keys listing: a key belongs to its organization and acts for whoever created it. The verifier's own lookup stays in
``app.auth.middleware``: it selects by digest, not by owner.

The plaintext key never reaches this module — callers hand it the
SHA-256 digest and nothing else.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.core.db import get_conn
from app.core.errors import Result, err, ok

logger = structlog.get_logger()

# ``key_hash`` is deliberately absent: nothing outside the verifier needs it.
_PUBLIC_COLUMNS = 'id, label, last4, created_at, revoked_at'

LIST_LIMIT = 100


class ApiKeyRepository:
    """Adapter for the ``user_api_keys`` table."""

    def insert_if_below_cap(
        self, user_id: str, org_id: str, label: str, key_hash: str, last4: str, cap: int
    ) -> Result[dict[str, Any] | None]:
        """Atomically enforce the per-organization cap and persist one key row."""
        try:
            with get_conn() as conn:
                conn.execute('SELECT id FROM organization WHERE id = %s FOR UPDATE', (org_id,))
                active = conn.execute(
                    'SELECT count(*) AS n FROM user_api_keys WHERE organization_id = %s AND revoked_at IS NULL',
                    (org_id,),
                ).fetchone()
                if active and int(active['n']) >= cap:
                    return ok(None)
                row = conn.execute(
                    f'INSERT INTO user_api_keys (organization_id, user_id, label, key_hash, last4)'
                    ' VALUES (%s, %s, %s, %s, %s)'
                    f' RETURNING {_PUBLIC_COLUMNS}',
                    (org_id, user_id, label, key_hash, last4),
                ).fetchone()
        except Exception as e:  # transport failure is a value here
            logger.warning('api_key.insert_failed', user_id=user_id, error=str(e)[:200])
            return err('API_KEY.INFRA_ERROR', detail=f'Key insert failed: {type(e).__name__}')
        if not row:
            return err('API_KEY.INFRA_ERROR', detail='Key insert returned no row')
        return ok(_public_projection(row))

    def list_for_owner(self, org_id: str, limit: int = LIST_LIMIT) -> Result[list[dict[str, Any]]]:
        """Every ACTIVE key of the caller's organization, then the most recent revoked ones.

        Active first is load-bearing: revoked rows are kept forever for audit,
        and a live key pushed past the page cap is a key nobody can revoke.
        """
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    f'SELECT {_PUBLIC_COLUMNS} FROM user_api_keys WHERE organization_id = %s'
                    ' ORDER BY revoked_at DESC NULLS FIRST, created_at DESC LIMIT %s',
                    (org_id, limit),
                ).fetchall()
        except Exception as e:
            logger.warning('api_key.list_failed', org_id=org_id, error=str(e)[:200])
            return err('API_KEY.INFRA_ERROR', detail=f'Key listing failed: {type(e).__name__}')
        return ok([_public_projection(row) for row in rows])

    def revoke(self, org_id: str, key_id: str, revoked_at: str) -> Result[dict[str, Any] | None]:
        """Stamp ``revoked_at`` on one key of the organization.

        ``ok(None)`` means no such key belongs to the caller's organization. Already-revoked
        keys keep their original timestamp (``revoked_at IS NULL`` filter), so
        a replayed revoke cannot rewrite the audit trail.
        """
        try:
            with get_conn() as conn:
                row = conn.execute(
                    'UPDATE user_api_keys SET revoked_at = %s'
                    ' WHERE organization_id = %s AND id = %s AND revoked_at IS NULL'
                    f' RETURNING {_PUBLIC_COLUMNS}',
                    (revoked_at, org_id, key_id),
                ).fetchone()
        except Exception as e:
            logger.warning('api_key.revoke_failed', org_id=org_id, key_id=key_id, error=str(e)[:200])
            return err('API_KEY.INFRA_ERROR', detail=f'Key revoke failed: {type(e).__name__}')
        return ok(_public_projection(row) if row else None)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _public_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Strip everything but the five columns a caller may see."""
    return {
        'id': str(row.get('id') or ''),
        'label': str(row.get('label') or ''),
        'last4': row.get('last4'),
        'created_at': str(_iso(row.get('created_at')) or ''),
        'revoked_at': _iso(row.get('revoked_at')),
    }
