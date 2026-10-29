"""Issue, list and revoke personal API keys .

The verifier half of this feature shipped first (#1456): a bearer that
starts with ``sbk_`` is resolved against ``user_api_keys``. Until this
module existed a row could only be created by hand as service_role, so
no user could actually obtain a key.

The plaintext is generated here, returned to the caller once, and never
persisted — only its SHA-256 digest reaches the table. There is no
"show me the key again" path by construction; losing it means issuing a
new one.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from app.auth.api_key_repository import ApiKeyRepository
from app.auth.middleware import API_KEY_PREFIX, CurrentUser, hash_api_key
from app.core.errors import Result, err, is_err, ok

logger = structlog.get_logger()

# Active keys one organization may hold at once. Rotation is issue-then-revoke, so
# the ceiling has to leave room for an overlap; beyond that a growing key
# set is a leak waiting to happen, not a use case.
MAX_ACTIVE_KEYS = 10

# 32 bytes of urandom, base64url-encoded. Full entropy is what lets the
# digest go unsalted (see ``hash_api_key``).
_KEY_ENTROPY_BYTES = 32

LABEL_MAX_LENGTH = 120


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    """A freshly minted key. ``key`` is the only time the plaintext exists."""

    id: str
    label: str
    key: str
    last4: str
    created_at: str


def issue_api_key(
    user: CurrentUser,
    label: str,
    repo: ApiKeyRepository | None = None,
) -> Result[IssuedApiKey]:
    """Mint one key of ``user``'s organization, acting for ``user``, and return its plaintext exactly once."""
    clean_label = label.strip()
    if not clean_label or len(clean_label) > LABEL_MAX_LENGTH:
        return err(
            'API_KEY.INVALID_LABEL',
            detail=f'Label must be 1 to {LABEL_MAX_LENGTH} characters',
        )

    repo = repo or ApiKeyRepository()

    plaintext = f'{API_KEY_PREFIX}{secrets.token_urlsafe(_KEY_ENTROPY_BYTES)}'
    inserted = repo.insert_if_below_cap(
        user_id=user.id,
        org_id=user.org_id,
        label=clean_label,
        key_hash=hash_api_key(plaintext),
        last4=plaintext[-4:],
        cap=MAX_ACTIVE_KEYS,
    )
    if is_err(inserted):
        return inserted
    if inserted.value is None:
        return err(
            'API_KEY.CAP_REACHED',
            detail=f'At most {MAX_ACTIVE_KEYS} active keys per organization; revoke one first',
        )

    row = inserted.value
    logger.info('api_key.issued', user_id=user.id, key_id=row['id'])
    return ok(
        IssuedApiKey(
            id=row['id'],
            label=row['label'],
            key=plaintext,
            last4=row['last4'],
            created_at=row['created_at'],
        )
    )


def list_api_keys(
    org_id: str,
    repo: ApiKeyRepository | None = None,
) -> Result[list[dict]]:
    """The organization's keys, without the digest and without the plaintext.

    Every active key is always present; the revoked ones are the most
    recent, since they are kept indefinitely for audit and would otherwise
    grow past any page.
    """
    return (repo or ApiKeyRepository()).list_for_owner(org_id)


def revoke_api_key(
    org_id: str,
    key_id: str,
    repo: ApiKeyRepository | None = None,
) -> Result[dict]:
    """Revoke one key of the organization. Unknown or already-revoked ids are NOT_FOUND.

    Revocation is a soft delete, so the row survives for audit while the
    verifier stops accepting the digest (it filters ``revoked_at IS NULL``
    in SQL).
    """
    repo = repo or ApiKeyRepository()
    revoked = repo.revoke(
        org_id=org_id,
        key_id=key_id,
        revoked_at=datetime.now(UTC).isoformat(),
    )
    if is_err(revoked):
        return revoked
    if revoked.value is None:
        # Same answer for "not yours", "does not exist" and "already
        # revoked": telling them apart would confirm another tenant's id.
        return err('API_KEY.NOT_FOUND', detail='No active key with that id')

    logger.info('api_key.revoked', org_id=org_id, key_id=key_id)
    return ok(revoked.value)
