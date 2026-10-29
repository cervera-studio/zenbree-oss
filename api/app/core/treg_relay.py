"""How to read a Treg response: the rules the three clients share.

Treg is a pass-through proxy. Every context
that buys through it (``app.people``, ``app.seo``, ``app.commerce``) has
its own client, for the reasons each of those modules gives, and they
are right to: the error codes are domain vocabulary and the request
shapes genuinely differ. What is NOT domain vocabulary is the answer to
"did a provider answer this, or is this the proxy talking", and that
question was answered in one client only (#1923) while the other two
folded every 4xx into a transport error (#2239).

So this module holds the reading rules, not the client. It knows about
``httpx.Response`` and JSON bodies and nothing about balances, retries,
credentials or error codes. Each client keeps deciding what a miss is
CALLED in its own namespace; they only stop disagreeing about what a
miss IS.

The rule, in one line: **the positive mark proves a provider answered,
the absence of the error mark proves nothing.** Asking for the absence
is what produced #1923.

The header contract
-------------------
Measured live against Treg on 2026-08-25, over the
five endpoints the SEO and commerce contexts call plus one that does not
exist, and it holds identically on all of them:

- Responses Treg GENERATES (unknown endpoint, missing required query
  parameter, wrong method, out of balance) carry ``x-treg-error: 1`` and
  NO relay marks. No provider was dialled and nothing was billed.
- Responses Treg RELAYS carry ``x-treg-call-id`` and
  ``x-treg-cost-micro`` and no ``x-treg-error``, including relayed
  FAILURES: Moz answering ``400 {"message": "Invalid 'target_scope'
  parameter."}`` came back with both marks and ``x-treg-cost-micro: 0``.

A relayed 404 was not observed on those five endpoints: SerpApi and Moz
both report "nothing found" as ``200`` with an empty array, and Moz does
so even for a target it has never seen. The rule below is therefore
correct but latent for SEO and commerce today. It is worth having
anyway, in the same place as the case it already governs in the people
chain, because the alternative is each client re-deciding it the first
time an endpoint does answer 404, which is how the two clients came to
disagree in the first place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import httpx

#: HTTP 402 is Treg's out-of-balance signal, not a generic client error.
OUT_OF_BALANCE_STATUS: Final[int] = 402

#: 404 is the one status whose meaning depends on who sent it.
NOT_FOUND_STATUS: Final[int] = 404

#: Stamped by Treg on responses Treg itself generated.
TREG_ERROR_HEADER: Final[str] = 'x-treg-error'

#: Stamped by Treg on responses it relayed from a provider. Treg cannot
#: know a call id or a cost until it has forwarded and billed the call,
#: which is what makes their presence proof rather than a hint.
RELAY_HEADERS: Final[tuple[str, ...]] = ('x-treg-call-id', 'x-treg-cost-micro')


def is_provider_answer(resp: httpx.Response) -> bool:
    """Did a provider answer this, or is this the proxy talking?

    Asked in the positive on purpose. The relay marks PROVE a provider
    was dialled. The absence of ``x-treg-error`` proves nothing, and
    reading it as proof puts every unrecognised shape (an HTML page from
    a hop in between, an empty body, a body whose keys nobody has seen)
    on the expensive side of the decision, closing a search that may
    never have happened. So: relay marks present and no error mark, or
    this is not a provider's answer.

    Presence, not truth, on both sides. Read for truth, an error mark
    sent empty would count as absent while an empty cost mark would
    count as present, and the asymmetry leans the wrong way: it turns a
    refusal into a miss, which is the direction this function exists to
    avoid. A cost of zero is also a real value: it is what a free miss
    bills, and it is what the relayed Moz 400 carried.
    """
    if resp.headers.get(TREG_ERROR_HEADER) is not None:
        return False
    return any(resp.headers.get(name) is not None for name in RELAY_HEADERS)


def upstream_text(body: dict[str, Any] | None) -> str | None:
    """The upstream's own message, wherever this body happens to keep it.

    Treg's refusals put it on ``detail``, as a string or, for 402, as a
    nested ``{"error", "message"}`` object; a provider's own error body
    passed through verbatim tends to use ``error.message``, and Moz uses
    a top-level ``message``. Anything else yields ``None`` rather than a
    guess.

    What comes back here is for the log and for the ``upstream_detail``
    context key. It is NEVER an ``Err.detail``: handlers surface that
    field to the user verbatim, and Treg's own refusals are written for
    whoever runs the account: ``no tool '<id>' in this org``, ``<id> is
    POST, add --method POST``, and for an unconnected provider a
    ``treg connections connect --provider <name>`` command line (#1922).
    """
    if not body:
        return None
    for key in ('detail', 'error', 'message'):
        value = body.get(key)
        if isinstance(value, dict):
            nested = str_or_none(value.get('message'))
            if nested is not None:
                return nested
            continue
        text = str_or_none(value)
        if text is not None:
            return text
    return None


def json_or_none(resp: httpx.Response) -> dict[str, Any] | None:
    """Parse the body into a dict, or ``None`` when it will not parse.

    A non-object JSON payload (bare list, string, number) is wrapped
    under ``data``: the provider response is passed through verbatim, so
    the shape is the provider's choice, not ours, and callers should be
    able to probe a dict uniformly.
    """
    try:
        payload = resp.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        return payload
    return {'data': payload}


def str_or_none(value: Any) -> str | None:
    """Coerce a JSON scalar to a trimmed string; ``None`` when empty.

    Defensive: ``detail`` is documented as a string but a provider error
    passed through verbatim can put anything there, and Treg's own 402
    puts a nested object there.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None
