"""Composite contact-enrichment adapter — sequential fallback chain.

Chains :class:`ContactEnrichmentService` adapters in priority order.
Typical wiring is ``[ApolloContactEnrichment, TregContactEnrichment]``:
Apollo is the incumbent and covers the bulk; Treg (PDL / Hunter behind one
credential) is the pay-per-call fallback for the
people Apollo has nothing on.

Behaviour rules (covered 1:1 by tests):

- Empty adapter list → ``Err(CONTACTS.NO_ADAPTERS_CONFIGURED)``.
- First ``Ok`` whose ``has_contact_channel`` is True → return it
  immediately, later adapters are never called.
- An ``Ok`` with NO channel is treated as a miss and the chain continues.
  It is remembered, though: if every later adapter answered and had
  nothing, the empty ``Ok`` is returned rather than an ``Err``, so the
  handler's ``CONTACTS.NO_CONTACT_REVEALED`` refund path still fires the
  way it does today instead of the user seeing a provider error for "we
  looked, there was nothing".
- Any source that FAILED instead of answering outranks that empty ``Ok``:
  the chain returns ``Err(CONTACTS.SOURCES_UNREACHABLE)`` carrying
  ``unreachable_sources``. Both shapes refund (the handler answers
  ``success: False`` either way), but they are not the same sentence:
  "there is nothing" closes the search, "the channel is broken" says to
  retry (#1923, the philosophy of #1882). Before this, one empty Apollo
  match buried two Treg providers that never got past the proxy, and the
  user was told the person had no email.
- Every adapter ``Err`` → propagate the **last** ``Err``, matching
  ``CompositeCompanyEnrichment``'s convention: the last error is the
  deepest fallback failure, which is what on-call wants. Its code is
  preserved even when a source was unreachable, because with no empty
  ``Ok`` in the way there is nothing to outrank and the specific verdict
  (rate limited, out of balance, not configured) is worth more than a
  generic one. The failed sources ride along on
  ``unreachable_sources`` so the handler can still say "retry" rather
  than "this person has no email". A one-adapter chain is the Treg gate
  OFF, and that path must keep surfacing Apollo's own codes verbatim.

Why no confidence comparison
----------------------------
Unlike the company composite, there is no confidence spectrum to weigh: a
reveal either surfaced a usable channel or it did not. Adding a threshold
here would mean paying a second provider for a contact we already have,
which is the opposite of what a pay-per-call fallback is for.

Result discipline
-----------------
This adapter never raises. Each ``await adapter.reveal(...)`` is wrapped in
``try/except`` and any escaping exception becomes
``Err('CONTACTS.ADAPTER_EXCEPTION', ..., adapter=<class name>)``, so:

- the composite honours its own port contract (always a Result);
- one buggy adapter cannot break the chain — the next still runs;
- on-call can correlate the crash with the offending adapter class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.core.errors import Err, Ok, Result, err
from app.people.adapters.aiark_contact_enrichment import AiArkContactEnrichment
from app.people.adapters.apollo_contact_enrichment import ApolloContactEnrichment
from app.people.adapters.treg_contact_enrichment import TregContactEnrichment
from app.people.ports.contact_enrichment import (
    ContactEnrichmentService,
    ContactFacts,
    ContactRevealHints,
    serves,
    with_unreachable_sources,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.people.aiark_client import AiArkClient
    from app.people.apollo_client import ApolloClient
    from app.people.treg_client import TregClient

logger = structlog.get_logger()

# Codes that mean the source ANSWERED and had nothing, or was never asked.
# Anything else is a source that failed instead of answering. The set is
# written this way round on purpose: a failure code added later then
# defaults to "that source had a problem", which is the safer thing to
# tell a user who is about to conclude the contact does not exist.
_ANSWERED_OR_SKIPPED_CODES: frozenset[str] = frozenset(
    {
        'CONTACTS.NO_MATCH',
        'CONTACTS.TREG_NO_MATCH',
        'CONTACTS.TREG_PROVIDER_NO_MATCH',
        'CONTACTS.NOT_CONFIGURED',
        'CONTACTS.TREG_NOT_CONFIGURED',
        'CONTACTS.MATCH_INSUFFICIENT_INPUT',
        # Not CONTACTS.INVALID_INPUT: Apollo's 422, a refused request (billing, a required field), never a miss.
        'CONTACTS.NO_ADAPTERS_CONFIGURED',
        'CONTACTS.TREG_IDENTITY_UNCONFIRMED',
    }
)


class CompositeContactEnrichment(ContactEnrichmentService):
    """Sequential fallback chain over :class:`ContactEnrichmentService`.

    Stops at the first ``Ok`` that carries a real contact channel. See the
    module docstring for the full behaviour contract.
    """

    def __init__(self, adapters: list[ContactEnrichmentService]) -> None:
        self._adapters = adapters

    async def reveal(self, hints: ContactRevealHints) -> Result[ContactFacts]:
        if not self._adapters:
            logger.warning('composite.contact_enrichment.no_adapters')
            return err(
                'CONTACTS.NO_ADAPTERS_CONFIGURED',
                detail='Composite contact enrichment received an empty adapter chain',
            )

        empty_ok: Ok[ContactFacts] | None = None
        last_err: Err | None = None
        unreachable: list[str] = []

        for index, adapter in enumerate(self._adapters):
            adapter_name = type(adapter).__name__
            logger.info(
                'composite.contact_enrichment.tried',
                adapter=adapter_name,
                index=index,
            )
            try:
                outcome: Result[ContactFacts] = await adapter.reveal(hints)
            except Exception as exc:  # port contract guard
                # Adapter violated the "never raise" port contract. Convert
                # to Err so the fallback chain continues; pin the offending
                # adapter class via Err.context so on-call can correlate.
                logger.warning(
                    'composite.contact_enrichment.adapter_exc',
                    adapter=adapter_name,
                    error_type=type(exc).__name__,
                )
                outcome = err(
                    'CONTACTS.ADAPTER_EXCEPTION',
                    detail=type(exc).__name__,
                    adapter=adapter_name,
                )

            if isinstance(outcome, Err):
                last_err = outcome
                failed_sources = _unreachable_sources(outcome, adapter_name)
                unreachable.extend(failed_sources)
                logger.info(
                    'composite.contact_enrichment.adapter_err',
                    adapter=adapter_name,
                    code=outcome.error.code,
                    unreachable_sources=failed_sources or None,
                )
                continue

            if serves(hints, outcome.value):
                logger.info(
                    'composite.contact_enrichment.short_circuit',
                    adapter=adapter_name,
                    source=outcome.value.source,
                )
                return outcome

            # Matched the person but revealed nothing usable. Keep the first
            # such result as the fallback answer and try the next provider.
            logger.info(
                'composite.contact_enrichment.matched_no_channel',
                adapter=adapter_name,
                source=outcome.value.source,
            )
            if empty_ok is None:
                empty_ok = outcome

        # Exhausted the chain. A source that failed outranks an empty
        # answer: the user has to hear "retry", not "this person has no
        # email". The refund is unaffected, both shapes end as
        # ``success: False`` in the handler.
        if unreachable:
            logger.warning(
                'composite.contact_enrichment.sources_unreachable',
                sources=tuple(unreachable),
                adapter_count=len(self._adapters),
                had_empty_match=empty_ok is not None,
            )
            if empty_ok is not None:
                return err(
                    'CONTACTS.SOURCES_UNREACHABLE',
                    detail='A contact data source failed instead of answering',
                    unreachable_sources=tuple(unreachable),
                    last_code=last_err.error.code if last_err is not None else None,
                )
            if last_err is not None:
                # Nothing to outrank, so the specific verdict survives and
                # only gains the names. Minting a generic code here would
                # erase Apollo's own codes on the Treg-gate-OFF chain.
                return with_unreachable_sources(last_err, unreachable)

        if empty_ok is not None:
            logger.info(
                'composite.contact_enrichment.all_empty',
                adapter_count=len(self._adapters),
            )
            return empty_ok

        if last_err is not None:
            logger.warning(
                'composite.contact_enrichment.all_failed',
                last_code=last_err.error.code,
                adapter_count=len(self._adapters),
            )
            return last_err

        # Defensive: unreachable (empty list handled above, and every loop
        # iteration sets either empty_ok or last_err or returns).
        return err(
            'CONTACTS.NO_ADAPTERS_CONFIGURED',
            detail='Composite contact enrichment chain produced no result',
        )


def _unreachable_sources(failure: Err, adapter_name: str) -> tuple[str, ...]:
    """Which sources behind this ``Err`` failed instead of answering.

    An adapter fronting several providers reports them itself on
    ``unreachable_sources`` (the Treg chain does, one entry per provider it
    dialled); anything else counts as the one source it is. A code meaning
    "answered and had nothing" or "never asked" reports none: that is the
    ordinary miss the chain exists for.
    """
    declared = failure.error.context.get('unreachable_sources')
    if isinstance(declared, tuple):
        return declared
    if failure.error.code in _ANSWERED_OR_SKIPPED_CODES:
        return ()
    return (adapter_name,)


def source_failed(failure: Err) -> bool:
    """A source behind this ``Err`` failed instead of answering, from the composite or from a lone adapter (which
    names no ``unreachable_sources``): the caller says "retry", never "nobody has this contact"."""
    return bool(_unreachable_sources(failure, 'the lone adapter'))


#: Kill-switch names (``vendor_disabled:<name>``) the reveal chain honours.
#: ``treg`` drops the whole Treg adapter; ``pdl`` / ``hunter`` one provider behind it.
CONTACT_PROVIDERS: tuple[str, ...] = ('apollo', 'aiark', 'treg', 'pdl', 'hunter')


def all_off(off: frozenset[str]) -> bool:
    """Every source of the chain is switched off."""
    return {'apollo', 'aiark'} <= off and ('treg' in off or {'pdl', 'hunter'} <= off)


def build_reveal_chain(
    apollo_client: ApolloClient,
    treg_client: TregClient | None,
    aiark_client: AiArkClient | None = None,
    off: frozenset[str] = frozenset(),
) -> ContactEnrichmentService:
    """Build the reveal chain for one call.

    Apollo alone unless a Treg client was provisioned AND configured, in
    which case Treg sits UNDER Apollo as a fallback: the composite only
    dials it when Apollo revealed no contact channel.

    An ``aiark_client`` (passed only when the AI Ark people-search flag is on)
    goes at the HEAD: with AI Ark searching, the row being revealed carries an
    AI Ark id Apollo cannot match, and AI Ark is the cheaper reveal besides.

    Lives here rather than in ``app/chat/tools/handlers/people.py``, where it
    was, because assembling this chain was never a fact about the chat tools:
    the rules above are this module's, and the handler was the only thing
    naming the two adapters that go into it (#2327, 5/n).

    Still constructed per call rather than through ``app/container.py``. The
    DI-wired ``CompositeCompanyEnrichment`` feeds the parked Knowledge-Graph
    sector pipeline and never the chat tools, and the second adapter is
    per-USER — it exists only when that user's ``treg_contact_enrichment``
    gate is on — so a singleton would have to be rebuilt per call anyway.

    ``off`` names the providers the admin kill switch turned off
    (:data:`CONTACT_PROVIDERS`); they are left out of the chain. With every
    one off the chain is empty and answers ``CONTACTS.NO_ADAPTERS_CONFIGURED``.
    """
    adapters: list[ContactEnrichmentService] = []
    if 'apollo' not in off:
        adapters.append(ApolloContactEnrichment(apollo_client))
    if treg_client is not None and treg_client.is_configured and 'treg' not in off:
        adapters.append(TregContactEnrichment(treg_client, skip=off))
    if aiark_client is not None and aiark_client.is_configured and 'aiark' not in off:
        adapters.insert(0, AiArkContactEnrichment(aiark_client))
    return adapters[0] if len(adapters) == 1 else CompositeContactEnrichment(adapters)


def reveal_contact(
    apollo_client_factory: Callable[[], ApolloClient],
    treg_client_factory: Callable[[], TregClient],
    *,
    treg_enabled: bool,
    hints: ContactRevealHints,
    build_chain: Callable[..., ContactEnrichmentService] = build_reveal_chain,
    aiark_client: AiArkClient | None = None,
) -> Awaitable[Result[ContactFacts]]:
    """Build and run the configured reveal chain for one chat request."""
    apollo_client = apollo_client_factory()
    treg_client = treg_client_factory() if treg_enabled else None
    if not any(client is not None and client.is_configured for client in (apollo_client, treg_client, aiark_client)):

        async def not_configured() -> Result[ContactFacts]:
            return err(
                'CONTACTS.NOT_CONFIGURED',
                detail='The B2B contact database is not configured for this environment',
            )

        return not_configured()
    return build_chain(apollo_client, treg_client, aiark_client).reveal(hints)
