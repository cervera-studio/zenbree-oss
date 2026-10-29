"""Symmetric encryption for refresh tokens at rest, and for the OAuth
``state`` parameter.

Fernet (AES-128-CBC + HMAC) from ``cryptography``, which is already a
first-party dependency via ``PyJWT[crypto]`` — no new package for what
is two function calls.

``state`` reuses the same primitive instead of a signed JWT plus a
server-side nonce table: a Fernet token is already tamper-proof and
already carries a timestamp, so ``decrypt(ttl=...)`` gives the replay
window for free and the callback stays stateless.

The key lives in ``settings.oauth_token_encryption_key`` (Terraform →
ACA secret, per ``feedback_tf_source_of_truth``). Rotating it invalidates
every stored grant: users reconnect, nothing is lost but the click. That
is an acceptable ceiling for v1.

ponytail: single key, no key-id envelope. Add a ``key_version`` column
and a decrypt-with-fallback list when a rotation has to be seamless.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import Result, err, ok

# How long an authorize redirect stays valid. Long enough for a consent
# screen with a login and an MFA prompt in the middle, short enough that a
# leaked URL in a browser history is not a standing CSRF primitive.
STATE_TTL_SECONDS = 15 * 60


def is_configured() -> bool:
    # Terraform collapses an unset deployment variable to the literal "not-configured"
    # rather than "" — an empty Key Vault value makes the ACA revision
    # update fail. It is not a valid Fernet key, so treating it as set
    # would answer 500 CRYPTO_FAILED where 503 NOT_CONFIGURED is the truth
    # Same guard as captcha.py / resend.py.
    key = settings.oauth_token_encryption_key
    return bool(key) and key != 'not-configured'


def _cipher() -> Fernet:
    return Fernet(settings.oauth_token_encryption_key.encode())


def encrypt(plaintext: str) -> Result[str]:
    """Encrypt a refresh token for storage."""
    if not is_configured():
        return err(
            'INTEGRATIONS.NOT_CONFIGURED',
            detail='oauth_token_encryption_key is not set',
        )
    if not plaintext:
        return err('INTEGRATIONS.INVALID_TOKEN', detail='empty token')
    try:
        return ok(_cipher().encrypt(plaintext.encode()).decode())
    except (ValueError, TypeError) as exc:
        # Malformed key (wrong length / not urlsafe-base64) — a config
        # error, not a user error. Never log the key or the token.
        return err(
            'INTEGRATIONS.CRYPTO_FAILED',
            detail='token encryption failed',
            cause=type(exc).__name__,
        )


def decrypt(ciphertext: str) -> Result[str]:
    """Recover a stored refresh token."""
    if not is_configured():
        return err(
            'INTEGRATIONS.NOT_CONFIGURED',
            detail='oauth_token_encryption_key is not set',
        )
    try:
        return ok(_cipher().decrypt(ciphertext.encode()).decode())
    except (InvalidToken, ValueError, TypeError) as exc:
        # Wrong key, tampered row, or a value encrypted before a rotation.
        # The caller turns this into "reconnect your mailbox".
        return err(
            'INTEGRATIONS.CRYPTO_FAILED',
            detail='token decryption failed',
            cause=type(exc).__name__,
        )


def issue_state(
    *, user_id: str, provider: str, return_to: str = '', pkce_verifier: str = '', project_id: str = ''
) -> Result[str]:
    """Mint the ``state`` parameter binding a callback to one user.

    ``return_to`` is a KEY from ``domain.return_to``, never a URL — see that
    module for why the difference is the whole security argument. It rides
    inside the encrypted payload rather than as a query parameter so the
    callback cannot be re-pointed by editing the redirect it came back on.
    ``project_id`` is the project the connected sender joins (#307), sealed the same way.
    """
    if not is_configured():
        return err(
            'INTEGRATIONS.NOT_CONFIGURED',
            detail='oauth_token_encryption_key is not set',
        )
    claims: dict[str, str] = {'uid': user_id, 'provider': provider}
    if return_to:
        claims['ret'] = return_to
    if pkce_verifier:  # PKCE code_verifier: encrypted, so only our exchange can present it
        claims['cv'] = pkce_verifier
    if project_id:
        claims['pid'] = project_id
    payload = json.dumps(claims, separators=(',', ':'))
    try:
        return ok(_cipher().encrypt(payload.encode()).decode())
    except (ValueError, TypeError) as exc:
        return err(
            'INTEGRATIONS.CRYPTO_FAILED',
            detail='state encryption failed',
            cause=type(exc).__name__,
        )


def _claim(state: str, key: str) -> str:
    if not is_configured():
        return ''
    try:
        payload = json.loads(_cipher().decrypt(state.encode(), ttl=STATE_TTL_SECONDS))
    except InvalidToken, ValueError, TypeError:
        return ''
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, str) else ''


def pkce_verifier(state: str) -> str:
    """The PKCE ``code_verifier`` carried by ``state``, or ``''``. Verify the state first."""
    return _claim(state, 'cv')


def state_project(state: str) -> str:
    """The project carried by ``state``, or ``''``. Verify the state first."""
    return _claim(state, 'pid')


def return_to(state: str) -> str:
    """The destination key carried by ``state``, or ``''``.

    Deliberately separate from :func:`verify_state`, which stays exactly the
    function it was: one job, one return type, and every existing caller
    unchanged. This one answers a cosmetic question — where to send the
    browser — and answers it with ``''`` for anything it cannot read, because
    a state that fails to decrypt is already being rejected by
    ``verify_state`` on the same request.
    """
    return _claim(state, 'ret')


def verify_state(state: str, *, provider: str) -> Result[str]:
    """Return the user id carried by ``state``, or an error.

    Rejects a state minted for a different provider so a callback can't be
    replayed across connectors.
    """
    if not is_configured():
        return err(
            'INTEGRATIONS.NOT_CONFIGURED',
            detail='oauth_token_encryption_key is not set',
        )
    try:
        raw = _cipher().decrypt(state.encode(), ttl=STATE_TTL_SECONDS)
        payload = json.loads(raw)
    except InvalidToken, ValueError, TypeError:
        return err('INTEGRATIONS.INVALID_STATE', detail='state is invalid or expired')
    user_id = payload.get('uid') if isinstance(payload, dict) else None
    if not user_id or payload.get('provider') != provider:
        return err('INTEGRATIONS.INVALID_STATE', detail='state does not match this provider')
    return ok(str(user_id))
