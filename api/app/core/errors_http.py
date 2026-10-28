"""
Thin adapter: Result or error code → FastAPI HTTP response.
Used at the HTTP boundary: routers and the request-scoped helpers they call.

The mapping has two layers, and the order between them is the whole design
(#2329):

* **Outcome** — what happened, independent of where. A timeout is a 504 in
  every domain. These are the suffix and fragment tables, matched in order.
* **Domain** — the fallback for an outcome no suffix classifies. Every prefix
  that reaches ``unwrap_or_raise`` should have an entry, deciding what its
  unclassified errors mean. Registering ``500`` is a real
  answer ("an unclassified failure here is ours"); *not* registering was how
  ``CONTACTS.NO_MATCH`` shipped as a 500 that pages somebody.

Every other HTTP error is raised through ``api_error``, so each body is
``{"detail": {"code", "message"}}``; ``tests/test_error_shape.py`` keeps
``HTTPException`` out of every other module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app.core.errors import is_err

if TYPE_CHECKING:
    from app.core.errors import Result

#: Codes whose HTTP meaning is neither their suffix's nor their domain's.
_EXACT_STATUS: dict[str, int] = {
    'AUTH.INSUFFICIENT_ROLE': 403,
    # Spend cap is a daily quota — 429 with Retry-After fits standard
    # HTTP semantics far better than the BILLING-domain 402 default.
    'BILLING.SPEND_CAP_EXCEEDED': 429,
    # The provider refused our own OAuth client (a wrong or expired secret):
    # the operator fixes it, not the caller, so a server fault, not the 422 default.
    'INTEGRATIONS.CLIENT_REJECTED': 503,
    # Instantly (#929): the request went out and no answer came back: the outcome is not known, so not a 502.
    'INSTANTLY.UNKNOWN_OUTCOME': 504,
    'INSTANTLY.BUSY': 409,
    'INSTANTLY.RECONCILE_TOO_SOON': 409,
    'INSTANTLY.DISABLED': 503,
}

#: Outcome suffixes, matched with ``endswith`` in order — first hit wins, so a
#: more specific ending must sit above the family it belongs to
#: (``HTTP_ERROR`` above ``_ERROR``, ``TOKEN_INVALID`` above the ``INVALID``
#: fragment rule below).
_SUFFIX_STATUS: tuple[tuple[str, int], ...] = (
    # A per-user cap (active schedules, active API keys) is a conflict with
    # current state, not a server fault.
    ('CAP_REACHED', 409),
    ('NOT_FOUND', 404),
    # A well-formed value the surface deliberately does not serve (a KG type
    # with its own dedicated route) — the caller must change the request.
    ('UNSUPPORTED_TYPE', 422),
    # A third party refusing what the CALLER supplied — a LinkedIn login it
    # did not accept, most often a mistyped password. 502 would blame the
    # upstream and 500 pages somebody; both turn every typo into an incident.
    ('PROVIDER_REJECTED', 422),
    ('TIMEOUT', 504),
    ('UNAVAILABLE', 503),
    ('RATE_LIMITED', 503),
    ('FORBIDDEN', 403),
    ('UNAUTHORIZED', 401),
    # A token that no longer authenticates anyone — a guest support link, a
    # session credential. 410 would describe the token; 401 tells the caller
    # the one thing it can act on, which is to get another.
    ('TOKEN_INVALID', 401),
    ('TOKEN_EXPIRED', 401),
    ('CONFLICT', 409),
    # A dependency the operator never wired (or a surface the operator left
    # off) is a server-side gap the caller cannot fix by changing the request,
    # but it is also not a fault — 503 says "come back later", 500 would page.
    ('_CONFIGURED', 503),
    ('NOT_ENABLED', 503),
    # Our own credit with a vendor ran out. Same shape as the above: nothing
    # the caller did, nothing they can fix, and it comes back.
    ('OUT_OF_BALANCE', 503),
    # The caller must complete a prerequisite (connect the account, confirm the
    # action) before this request can succeed: a conflict with current state.
    ('NOT_CONNECTED', 409),
    ('CONFIRMATION_REQUIRED', 409),
    ('CONFIRM_REQUIRED', 409),
    # A state machine refusing a move is a conflict with the current state, not
    # a malformed request — so it must win over the INVALID fragment below.
    ('INVALID_TRANSITION', 409),
    ('INVALIDATED', 409),
    # Well-formed request the surface cannot act on: the payload's shape does
    # not satisfy the domain, or nothing processable survived validation.
    ('INVALID_SHAPE', 422),
    ('NO_VALID_LEADS', 422),
    ('INSUFFICIENT_INPUT', 422),
    # A lookup that ran and found nobody. Not a fault — an empty answer.
    ('NO_MATCH', 404),
    ('NO_RESULTS', 404),
    # A budget or quota tripped — the caller's own ceiling, so 429 rather than
    # the 402/500 the domain would otherwise give.
    ('_EXCEEDED', 429),
    # Upstream misbehaved: it was unreachable, refused the credential we
    # store for it, or answered in a shape we do not accept. None of these
    # are the caller's fault and none are a bug in this service.
    ('UNREACHABLE', 502),
    ('UNEXPECTED_SHAPE', 502),
    ('UNEXPECTED_PAYLOAD', 502),
    ('HTTP_ERROR', 502),
    ('TRANSPORT', 502),
    ('TRANSPORT_ERROR', 502),
    ('.AUTH', 502),
    # Ours, and only ours: a write that did not land, an exception we caught.
    # Explicit rather than left to the domain default, so a domain whose
    # default is a 4xx does not drag its genuine failures down with it.
    ('_FAILED', 500),
    ('_ERROR', 500),
    ('_EXCEPTION', 500),
)

#: Fragments matched anywhere in the part after the domain, once no suffix
#: rule hit. Malformed input reads as ``INVALID_X`` as often as ``X_INVALID``,
#: which ``endswith`` alone cannot see.
_FRAGMENT_STATUS: tuple[tuple[str, int], ...] = (
    ('INVALID', 400),
    ('ALREADY_', 409),
)

#: Domain → status for an outcome no rule above classified. Only the domains
#: this codebase names; an unknown domain falls back to 500.
_STATUS_MAP: dict[str, int] = {
    'AUTH': 401,
    'BILLING': 402,
    # A vendor adapter: an unclassified error is the third party's, not ours.
    'HUBSPOT': 502,
    # Caller-driven surfaces: an unclassified error means the request or the
    # workspace state cannot support what was asked.
    'INTEGRATIONS': 422,
    # Instantly: an unclassified error is the customer's workspace, plan or key (a 4xx of theirs), not ours.
    'INSTANTLY': 422,
    'OUTREACH': 422,
    'WEBHOOKS': 422,
    # A move the current state does not allow.
    'CAMPAIGN': 409,
    # A lookup that produced nothing to return.
    'CONTACTS': 404,
    # Ours. An unclassified error in these is a bug or a dead dependency, and
    # 500 is the honest answer rather than the absence of one.
    'ADMIN': 500,
    'API_KEY': 500,
    'CHAT': 500,
    'INTERNAL': 500,
    'ONBOARDING': 500,
    'SCRAPE': 500,
}


def _http_status(code: str) -> int:
    if code in _EXACT_STATUS:
        return _EXACT_STATUS[code]
    for suffix, status in _SUFFIX_STATUS:
        if code.endswith(suffix):
            return status
    rest = code.split('.', 1)[-1]
    for fragment, status in _FRAGMENT_STATUS:
        if fragment in rest:
            return status
    return _STATUS_MAP.get(code.split('.')[0], 500)


def api_error(
    status: int, code: str, message: str, *, headers: dict[str, str] | None = None, **extra: object
) -> HTTPException:
    """The one way to build an HTTP error: ``{"detail": {"code", "message", **extra}}``. Raise what it returns.

    ``extra``: values the web interpolates into the code's text (the message is English, for logs and API clients)."""
    return HTTPException(
        status, {'code': code, 'message': message, **{k: v for k, v in extra.items() if v is not None}}, headers=headers
    )


def unwrap_or_raise(result: Result) -> Any:
    """Extract Ok value or raise HTTPException from Err."""
    if is_err(result):
        error = result.error
        raise HTTPException(
            status_code=_http_status(error.code),
            detail=error.to_response()['error'],
        )
    return result.value
