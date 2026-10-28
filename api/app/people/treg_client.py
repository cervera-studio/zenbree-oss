"""Treg HTTP client — pay-per-call proxy in front of many data providers.

Treg (``treg.superdesign.dev``) fronts Lusha, People Data Labs, Hunter,
LeadMagic and others behind one credential and one prepaid balance. It is
a **pass-through proxy**, not an API of its own:

- One call shape: ``<METHOD> /call/<endpoint-id>?<query>``. Method, path,
  query and body pass through to the upstream provider unchanged, so a
  provider whose endpoint is ``POST /v3/contacts/search-and-enrich`` is
  reached by POSTing a JSON body to the same ``/call/<endpoint-id>`` URL.
  Verified live against ``treg.superdesign.dev`` on 2026-08-12: a POST
  with a JSON body reaches Treg's router (it answers with the endpoint's
  own credential/permission verdict, not ``405 Method Not Allowed``).
- The response is the upstream provider's response **verbatim** — no
  envelope, no normalisation. Whatever Lusha would have returned is what
  comes back, so every provider needs its own response parsing (see
  ``app/people/adapters/treg_contact_enrichment.py``).
- ``<endpoint-id>`` is an opaque catalog string Treg assigns per
  provider+capability (e.g. ``pdl.people.enrich``,
  ``hunter.x.combined-find``). Look one up with ``treg catalog get <id>``.
- A parameter the upstream provider takes in its **path** (Crunchbase's
  ``GET /entities/organizations/{entity_id}``) is supplied to Treg as an
  ordinary query param — Treg templates it into the upstream path. This is
  what the catalog's own "RUN IT" line does
  (``treg call crunchbase.companies.enrich --query entity_id=apple``), and
  it was confirmed live: the query-param form reaches the endpoint's
  credential check rather than 404-ing on an unsubstituted path.

Auth is a single ``X-Treg-Token`` header; the organisation is baked into
the per-org token, so there is no separate org header.

Idempotency
-----------
Every call carries an ``Idempotency-Key: <uuid>`` header so a retry cannot
double-charge. Treg answers a replay with ``X-Treg-Idempotent-Replay:
true`` plus ``X-Treg-Cost-Micro: <original-cost>``; both are attributed
rather than returned, because they are call metadata and the caller's
contract is "the provider's payload, verbatim". The attribution is written
by ``app.billing.paid_call`` (#2306), which wraps the dial: the replay is
logged at zero and the meter's number is the price, so nobody here has to
remember to write one.

The key identifies a REQUEST, not an operation. Treg binds it to the first
request that completes with it and answers any DIFFERENT request carrying
it with ``422 Idempotency-Key '<k>' was already used for a different
request``, before the upstream provider is dialled. So a caller reusing one
key across a FALLBACK CHAIN silences every provider after the first
(#1923): pass your own key and repeat it only for a byte-identical retry of
the same call, and derive a distinct one per call when a caller makes
several.

Errors
------
Two different things can answer with a 4xx, and they mean opposite things:

- **The proxy refused**, before the provider was dialled. Treg marks these
  with the header ``x-treg-error: 1`` and a body of its own shape,
  ``{"detail": …}``. Verified live on 2026-08-20: an unknown endpoint id
  and a provider with no credential connected both answer ``404`` this
  way, and an empty balance answers ``402``.
- **The provider answered**, and the proxy passed its response through
  verbatim. No ``x-treg-error`` header; ``x-treg-call-id`` and
  ``x-treg-cost-micro`` instead. Measured the same day:
  ``pdl.people.enrich`` for a person nobody has answers ``404`` with
  ``{"status": 404, "error": {"type": "not_found", …}}`` and
  ``x-treg-cost-micro: 0``.

That second shape is a MISS, not an outage: the provider was reached, it
has no record, and the call was free. It maps to
``CONTACTS.TREG_PROVIDER_NO_MATCH`` so a fallback chain can tell "nobody
has this person" from "we could not ask", which is the distinction the
user reads (#1923).

The relay headers are what decides that, not the absence of the error one
(:func:`app.core.treg_relay.is_provider_answer`, where the rule lives
now, because the SEO and commerce clients had never learned it and were
reading every 404 as an outage, #2239). A 404 with no marks at all could
be either, and the two mistakes do not cost the same: reading an outage
as a miss closes a search that never happened, so an unrecognised shape
is treated as "we could not ask".

An earlier version decided on the body instead of the marks, and required
it NOT to carry a top-level ``detail``, as a net against a proxy build
that forgets its error header. It did not look at the marks at all.
That net is gone on purpose: a provider may legitimately answer
``{"detail": "not found"}``, and a body-shape rule would call that a
transport error. The marks decide, and a refusal that arrives with relay
marks and no error mark now reads as a miss. Every other 4xx/5xx stays
``Err('CONTACTS.TRANSPORT_ERROR', ..., treg_status=<code>)``.

**HTTP 402** is out-of-balance and keeps its own
``CONTACTS.TREG_OUT_OF_BALANCE`` — on-call needs "add funds" to look
different from "something broke". Its body nests everything under
``detail`` (``{"error": "insufficient_balance", "message": "<endpoint>
would cost ~$X … balance is $Y … add funds: <url>"}``), so the numbers
live in prose and the flat ``balance_micro`` / ``topup_url`` keys read
here are best-effort: absent on the shape observed on 2026-08-20.

``Err.detail`` is always OUR neutral sentence, never the upstream body's
``detail`` (#1922). Treg passes provider errors through verbatim, and a
marketplace endpoint with no credential answers with vendor names plus
CLI remediation lines (``treg connections connect --provider …``) — text
several tool handlers surface as-is in user-visible tool results. The
upstream text is preserved for debugging where it belongs: on the
``upstream_detail`` context key and in the ``treg.call.*`` log events.

Same shape as :class:`app.people.apollo_client.ApolloClient`: constructed
inline by callers, ``is_configured`` gates the whole path off when no key
is provisioned, and exceptions are converted to ``Err`` here so nothing
past this boundary ever sees a raw ``httpx`` error.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog

from app.billing.paid_call import paid_call
from app.core.config import settings
from app.core.errors import Result, err, is_err, ok
from app.core.treg_relay import (
    NOT_FOUND_STATUS,
    OUT_OF_BALANCE_STATUS,
    is_provider_answer,
    json_or_none,
    str_or_none,
    upstream_text,
)

logger = structlog.get_logger()

# Treg fans out to third-party providers, so its tail is the slowest
# provider's tail plus a proxy hop. 20 s: more slack than Apollo's 15 s,
# still short enough not to park a chat turn.
_TIMEOUT_SECONDS = 20.0


class TregClient:
    """HTTPX-backed Treg wrapper. Stateless except for the token + base URL."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.treg_api_key
        self._base_url = (base_url or settings.treg_base_url).rstrip('/')
        self._timeout = timeout_seconds

    @property
    def is_configured(self) -> bool:
        """False when no token is provisioned — the whole Treg path is then
        inert, exactly like an empty ``apollo_api_key`` disables Apollo."""
        return bool(self._api_key and self._base_url)

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        return {
            'Accept': 'application/json',
            'X-Treg-Token': self._api_key,
            'Idempotency-Key': idempotency_key,
        }

    async def call_endpoint(
        self,
        endpoint_id: str,
        params: dict[str, str] | None = None,
        *,
        method: str = 'GET',
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Result[dict[str, Any]]:
        """``<method> /call/<endpoint_id>`` with ``params`` as the query
        string and ``json_body`` as a JSON request body.

        Defaults to GET with no body, which is what the query-param
        providers (PDL, Hunter, Crunchbase's entity lookup) need. Pass
        ``method='POST'`` plus ``json_body=...`` for the providers whose
        upstream endpoint is a POST (Lusha, LeadMagic, Crunchbase's
        organization search) — Treg passes the method and body straight
        through, so the URL shape is identical either way.

        Returns the upstream provider's JSON body verbatim. A body that is
        not a JSON object (some providers answer with a bare array) is
        wrapped as ``{'data': <payload>}`` so the return type stays a dict
        the parsers can probe uniformly.

        ``idempotency_key`` defaults to a fresh uuid4. Pass your own and
        repeat it for an identical retry of THIS call, or the replay
        protection buys you nothing; never share one across calls that
        differ, which Treg answers with 422 (see the module docstring).
        Error mapping (401/402/4xx/5xx) is identical for every method — it
        all runs through :func:`_decode_call`.
        """
        if not self.is_configured:
            return err(
                'CONTACTS.TREG_NOT_CONFIGURED',
                detail='the data provider is not configured',
            )

        key = idempotency_key or str(uuid.uuid4())
        url = f'{self._base_url}/call/{endpoint_id}'

        async def _dial() -> Result[httpx.Response]:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    return ok(
                        await client.request(
                            method,
                            url,
                            headers=self._headers(key),
                            params=params or None,
                            # httpx treats json=None as "no body", so a GET stays
                            # a bodyless GET without branching here.
                            json=json_body,
                        )
                    )
            except httpx.HTTPError as exc:
                logger.warning(
                    'treg.call.transport_error',
                    endpoint_id=endpoint_id,
                    method=method,
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.TRANSPORT_ERROR',
                    detail='Data provider unreachable',
                    cause=type(exc).__name__,
                    treg_endpoint=endpoint_id,
                )

        dialled = await paid_call(
            'treg.call',
            _dial,
            endpoint_id=endpoint_id,
            method=method,
            bounded_context='people',
        )
        if is_err(dialled):
            return dialled
        return _decode_call(dialled.value, endpoint_id)


# ── Module-private helpers ──────────────────────────────────────────────


def _decode_call(resp: httpx.Response, endpoint_id: str) -> Result[dict[str, Any]]:
    """Map one Treg response onto ``Result[dict]``.

    Never raises: a body that will not parse as JSON is a transport error,
    not an exception the caller has to catch.
    """
    body = json_or_none(resp)

    # #1922: the upstream ``detail`` is the PROVIDER's error text, verbatim —
    # vendor brands plus, on marketplace endpoints with no credential, CLI
    # remediation lines meant for ops. Several handlers put ``Err.detail``
    # straight into user-visible tool results, so it must never carry that
    # text: our neutral sentence goes on ``detail``, the upstream text goes
    # on ``upstream_detail`` (context) and into the log event.
    if resp.status_code == OUT_OF_BALANCE_STATUS:
        upstream = upstream_text(body)
        # Best-effort: the shape measured on 2026-08-20 keeps the balance,
        # the price and the top-up link inside the ``detail.message`` prose
        # and carries none of these keys. They stay read because a body
        # that does carry them is worth more to on-call than the sentence.
        topup_url = str_or_none((body or {}).get('topup_url'))
        logger.warning(
            'treg.call.out_of_balance',
            endpoint_id=endpoint_id,
            balance_micro=(body or {}).get('balance_micro'),
            estimated_cost_micro=(body or {}).get('estimated_cost_micro'),
        )
        return err(
            'CONTACTS.TREG_OUT_OF_BALANCE',
            # Our balance, not the user's — "add funds" is an on-call action
            # (the log event above), never a sentence to show a customer.
            detail='Data source temporarily unavailable',
            upstream_detail=(upstream or '')[:500] or None,
            topup_url=topup_url,
            balance_micro=(body or {}).get('balance_micro'),
            estimated_cost_micro=(body or {}).get('estimated_cost_micro'),
            treg_endpoint=endpoint_id,
        )

    if resp.status_code == NOT_FOUND_STATUS and is_provider_answer(resp):
        # The provider was reached and has no record. It cost nothing and it
        # will say the same thing tomorrow, so a fallback chain must read it
        # as a miss: calling it an outage tells the user to retry a lookup
        # that cannot change, which is the mirror image of #1923's defect.
        upstream = upstream_text(body)
        logger.info(
            'treg.call.provider_no_match',
            endpoint_id=endpoint_id,
            upstream_detail=(upstream or '')[:300] or None,
        )
        return err(
            'CONTACTS.TREG_PROVIDER_NO_MATCH',
            # Neutral about WHAT was looked up: this client fronts the
            # people chain, the company fallback and the app catalog, and
            # every one of those surfaces this sentence to a user.
            detail='The data source has no record matching this request',
            upstream_detail=(upstream or '')[:500] or None,
            treg_status=resp.status_code,
            treg_endpoint=endpoint_id,
        )

    if resp.status_code >= 400:
        upstream = upstream_text(body)
        logger.warning(
            'treg.call.http_error',
            endpoint_id=endpoint_id,
            status=resp.status_code,
            upstream_detail=(upstream or '')[:300] or None,
        )
        return err(
            'CONTACTS.TRANSPORT_ERROR',
            detail=f'Data provider call failed (HTTP {resp.status_code})',
            upstream_detail=(upstream or '')[:500] or None,
            treg_status=resp.status_code,
            treg_endpoint=endpoint_id,
        )

    if body is None:
        logger.warning(
            'treg.call.undecodable_body',
            endpoint_id=endpoint_id,
            status=resp.status_code,
        )
        return err(
            'CONTACTS.TRANSPORT_ERROR',
            detail='Data provider returned an unreadable response',
            treg_status=resp.status_code,
            treg_endpoint=endpoint_id,
        )

    return ok(body)
