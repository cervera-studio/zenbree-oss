"""Deliverability verdict for a non-Apollo email (#1073).

Apollo-sourced emails already carry a confidence signal — ``app.people.provenance``
maps Apollo's ``email_status`` onto the 0.0-1.0 chip scale, so ``enrich_email`` /
``find_decision_maker`` rows are covered. Emails from anywhere else (scraped off a
careers page, typed by the user, pattern-guessed) arrive with no signal at all, and
outreach to a dead address costs sender reputation for *every* user on the sending
domain, not just the sender.

Verdicts, deliberately on the SAME scale as the Apollo mapping so the FE renders one
comparable chip instead of two incommensurable ones:

- ``deliverable``   → 0.6  — the domain accepts mail. Sits in the FE's medium band and
  BELOW Apollo's ``probable`` (0.7) on purpose: an MX record says nothing about the
  mailbox, so this must never out-rank a provider's mailbox-level signal.
- ``undeliverable`` → 0.0  — bad syntax, no MX, null MX, NXDOMAIN, or SPF reject-all.
- ``unknown``       → None — no chip, same convention as Apollo ``unverified``.

``unknown`` is load-bearing and must never collapse into either neighbour. A DNS
timeout or a resolver-less container is not an invalid address: calling it
``undeliverable`` silently drops a real lead, calling it ``deliverable`` is exactly
the bounce we are trying to prevent. ``email_validator`` already draws that line —
it raises for a real negative and returns *without* ``mx`` when the resolver could
not answer.

MX level only: no SMTP probe, no paid verifier. That is what a free check can
honestly assert. This module sends nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import structlog

from app.admin.settings import vendor_disabled_now
from app.billing.paid_call import record_vendor_call
from app.billing.vendors import MILLIONVERIFIER
from app.core.config import settings

logger = structlog.get_logger()

# Canonical extraction_method tag for the FE chip (see app/shared/provenance.py).
MX_EXTRACTION_METHOD = 'email_mx_check'

# Short enough that a wedged resolver returns ``unknown`` inside one chat turn
# instead of holding it for the library's 15 s default.
_DNS_TIMEOUT_SECONDS = 5


class Deliverability(StrEnum):
    DELIVERABLE = 'deliverable'
    UNDELIVERABLE = 'undeliverable'
    UNKNOWN = 'unknown'


_CONFIDENCE: dict[Deliverability, float | None] = {
    Deliverability.DELIVERABLE: 0.6,
    Deliverability.UNDELIVERABLE: 0.0,
    Deliverability.UNKNOWN: None,
}


@dataclass(frozen=True, slots=True)
class EmailVerification:
    email: str
    verdict: Deliverability
    reason: str

    @property
    def confidence(self) -> float | None:
        return _CONFIDENCE[self.verdict]


# Returns an ``email_validator.ValidatedEmail``; raises ``EmailNotValidError`` on a
# real negative. Injected so unit tests never touch DNS.
EmailChecker = Callable[[str], Any]


def _dns_checker(email: str) -> Any:
    from email_validator import validate_email

    return validate_email(
        email,
        check_deliverability=True,
        timeout=_DNS_TIMEOUT_SECONDS,
    )


def verify_email(
    email: str,
    *,
    checker: EmailChecker = _dns_checker,
) -> EmailVerification:
    """Syntax + MX verdict for one address. Never raises."""
    from email_validator import EmailNotValidError

    address = str(email or '').strip()
    if not address:
        return EmailVerification(address, Deliverability.UNDELIVERABLE, 'empty address')

    try:
        validated = checker(address)
    except EmailNotValidError as exc:
        return EmailVerification(address, Deliverability.UNDELIVERABLE, str(exc))
    except Exception as exc:
        # Resolver bootstrap failures (no /etc/resolv.conf in a container →
        # dns.resolver.NoResolverConfiguration) escape email_validator's own
        # mapping: it sets the resolver up before its try block. Those are
        # OUR outage, not a bad address — unknown, never undeliverable.
        logger.warning('verify_email.check_failed', error=str(exc))
        return EmailVerification(
            address,
            Deliverability.UNKNOWN,
            'deliverability check failed',
        )

    normalized = str(getattr(validated, 'normalized', address))
    mx = getattr(validated, 'mx', None)
    if not mx:
        # Timeout / all-nameservers-failed, or a domain literal the library skips.
        return EmailVerification(
            normalized,
            Deliverability.UNKNOWN,
            'MX lookup did not resolve',
        )
    return EmailVerification(
        normalized,
        Deliverability.DELIVERABLE,
        f'domain accepts mail (MX {mx[0][1]})',
    )


# ── Mailbox-level verdict (Zenbree M1) ──────────────────────────────────────
# MX says the domain takes mail, not that the mailbox exists. MillionVerifier
# (pay-per-use, ~$0.0037/check, credits never expire) answers for the mailbox.
# Unset key → MX only, and the status says so ('mx_ok', never 'verified').
# Its v3 API only takes the key as ``?api=``: the URL is never logged here, and
# httpx's own request log is held at WARNING in app.core.logging.

_MV_URL = 'https://api.millionverifier.com/api/v3/'
# ``result`` → our ``email_status``; anything else falls back to MX.
_MV_STATUS = {'ok': 'verified', 'catch_all': 'catch_all', 'invalid': 'invalid', 'disposable': 'invalid'}
_MX_STATUS = {
    Deliverability.DELIVERABLE: 'mx_ok',
    Deliverability.UNDELIVERABLE: 'invalid',
    Deliverability.UNKNOWN: 'unknown',
}


async def millionverifier_status(email: str, *, client: httpx.AsyncClient | None = None) -> str | None:
    """MillionVerifier's mailbox verdict as an ``email_status`` (``verified | catch_all | invalid``), or None when it
    gave none: no key, switched off (``vendor_disabled:millionverifier``), an error, or a result such as ``unknown``.
    Each answered call is metered on its ``vendor_calls`` line. Never raises."""
    if not settings.millionverifier_api_key or await asyncio.to_thread(vendor_disabled_now, 'millionverifier'):
        return None
    try:
        async with client or httpx.AsyncClient(timeout=25) as c:
            resp = await c.get(_MV_URL, params={'api': settings.millionverifier_api_key, 'email': email, 'timeout': 20})
        body = resp.json()
        ok = resp.status_code == 200 and not body.get('error')
        await asyncio.to_thread(record_vendor_call, MILLIONVERIFIER, 'verify', metered=1 if ok else 0, success=ok)
        if ok and body.get('result') in _MV_STATUS:
            return _MV_STATUS[body['result']]
        if not ok:
            logger.warning('millionverifier.error', status_code=resp.status_code)
    except Exception as exc:
        logger.warning('millionverifier.failed', error_type=type(exc).__name__)
    return None


async def email_status(email: str, *, client: httpx.AsyncClient | None = None) -> str:
    """``verified | catch_all | invalid | mx_ok | unknown`` for one address. Never raises."""
    if status := await millionverifier_status(email, client=client):
        return status
    verdict = await asyncio.to_thread(verify_email, email)
    return _MX_STATUS[verdict.verdict]
