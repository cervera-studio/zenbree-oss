"""enrich_phone: direct-phone waterfall Apollo (async phone job) → Treg (PDL/Hunter).

Pay-if-found, like enrich_email: the store bills each phone found (the earlier implementation
``reveal_person.py`` charges its ``PHONE_SURCHARGE`` only when a number came
back). Vendor spend is logged per call by ``paid_call`` either way.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from app.agent.tools.handlers._rows import (
    DISABLED,
    UNDELIVERED,
    no_row_match,
    nothing_to_search,
    providers_off,
    unmatched_data,
    unmatched_ids,
)
from app.agent.tools.handlers.enrich_email import _hints, failed_person, load_rows
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.billing.paid_call import current_user_id
from app.billing.price import Price
from app.core.errors import is_err
from app.lists import lookup_outcome
from app.lists.lookups import FAILED, NOT_FOUND, REVEALED
from app.people.adapters.aiark_contact_enrichment import is_aiark_id
from app.people.adapters.composite_contact_enrichment import (
    CONTACT_PROVIDERS,
    all_off,
    build_reveal_chain,
    source_failed,
)
from app.people.apollo_client import ApolloClient
from app.people.ports.contact_enrichment import ContactRevealHints
from app.people.treg_client import TregClient

NAME = 'enrich_phone'
# Apollo polls its phone job for up to 24s per person, so a smaller batch than enrich_email.
MAX_PEOPLE = 25
_CONCURRENCY = 5
# Apollo bills 8 credits per phone + 1 for the match (~€0.155 at $0.02 a credit, measured 2026-10-07)
PRICE = Price(credits=8, keys=('phone', 'phone_status', 'phone_source'), found=lambda cells: bool(cells.get('phone')))
# AI Ark finds emails only: no phone source to switch.
_PHONE_PROVIDERS = tuple(p for p in CONTACT_PROVIDERS if p != 'aiark')


def _phone_hints(p: dict) -> ContactRevealHints:
    """The row's search hints without an AI Ark id: Apollo cannot match one, and its miss would read as final."""
    hints = _hints(p)
    if is_aiark_id(hints.directory_person_id or ''):
        return replace(hints, directory_person_id=None)
    return hints


def searchable(cells: dict) -> bool:
    """Can a source be asked about this person? Apollo's minimum: its own id, a LinkedIn URL, an email, or a first
    and last name with a company; without them Treg has nothing to match either. A row without them is not searched
    and gets no verdict: it can be searched once it gains one."""
    hints = _phone_hints(cells)
    named = hints.first_name and hints.last_name and (hints.organization_name or hints.organization_domain)
    return bool(hints.directory_person_id or hints.linkedin_url or hints.email or named)


def _miss(row_id: str, cells: dict) -> dict:
    """A row_update for a person with no number found: it lands only while the row still has no phone, so it never
    erases one a «Cerca il telefono» click bought meanwhile (#507)."""
    return {'row_id': row_id, 'cells': {'phone': None, **cells}, 'if_empty': 'phone'}


async def _phone_one(p: dict, apollo: ApolloClient, treg: TregClient, off: frozenset[str]) -> dict:
    """The row_update for one person. ``cells.phone`` is None when nothing was found. ``phone_status: not_found`` is
    a final verdict (the web then offers no paid retry), so it is stored only when Apollo, the main phone source,
    was in the chain and no source failed; otherwise it is ``failed`` and the row can be searched again. A row no
    source can match gets no status."""
    if not searchable(p):
        return _miss(p['row_id'], {})
    chain = build_reveal_chain(apollo, treg, None, off)
    found = await chain.reveal(replace(_phone_hints(p), reveal_phone=True))
    if not is_err(found) and (phone := found.value.phone):
        return {
            'row_id': p['row_id'],
            'cells': {'phone': phone, 'phone_status': REVEALED, 'phone_source': str(found.value.source)},
        }
    if 'apollo' in off or (is_err(found) and source_failed(found)):
        return _miss(p['row_id'], {'phone_status': FAILED})
    return _miss(p['row_id'], {'phone_status': NOT_FOUND})


async def handle(args: dict, ctx: ToolContext) -> ToolResult:
    current_user_id.set(ctx.user_id)
    row_ids = [str(r) for r in args.get('row_ids') or [] if r]
    if not ctx.list_id:
        return ToolResult(ok=False, error='there is no list yet: find people first, then look up their phones')
    loaded = await asyncio.to_thread(load_rows, ctx.list_id, row_ids) if row_ids else []
    if not loaded:
        return no_row_match('phone')
    unmatched = unmatched_ids(row_ids, loaded)
    people = [p for p in loaded if not p.get('phone')][: min(MAX_PEOPLE, ctx.max_units or MAX_PEOPLE)]
    if not people:
        return nothing_to_search('phone', unmatched)

    apollo, treg = ApolloClient(), TregClient()
    if not (apollo.is_configured or treg.is_configured):
        return ToolResult(ok=False, error='phone lookup is not configured')
    off = await asyncio.to_thread(providers_off, _PHONE_PROVIDERS)
    # A source with no key answers NOT_CONFIGURED, or an empty chain NO_ADAPTERS_CONFIGURED: both read as a miss.
    off |= {name for name, client in (('apollo', apollo), ('treg', treg)) if not client.is_configured}
    if all_off(off | {'aiark'}):
        return ToolResult(ok=False, error=DISABLED)
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(p: dict) -> dict:
        async with gate:
            return await _phone_one(p, apollo, treg, off)

    await ctx.claim(
        'phone', [p['row_id'] for p in people if searchable(p)]
    )  # before the first vendor call: the rows read as searching
    updates = await asyncio.gather(*(one(p) for p in people))
    outcome = lookup_outcome.searched('phone', updates, unmatched=len(unmatched))
    found = sum(PRICE.found(u['cells']) for u in updates)
    # A source failed, or Apollo was off.
    failed = [
        failed_person(p) for p, u in zip(people, updates, strict=True) if u['cells'].get('phone_status') == FAILED
    ]
    return ToolResult(
        ok=True,
        row_updates=list(updates),
        entity='person',
        outcome=outcome,
        data={'searched': len(people), 'found': found, 'failed': failed, **unmatched_data(unmatched)},
    )


register(
    ToolDef(
        name=NAME,
        description=(
            'Find direct phone numbers for people already in the list. Pass row_ids from the current List. '
            'Tries Apollo, then other contact databases. Slow (up to ~25s per person). '
            f'Charged only per phone found. Max {MAX_PEOPLE} people per call. '
            'Use only when the user asks for phone numbers. data.failed names the people left with no verdict '
            'because a source failed or Apollo is switched off: say the lookup can be retried later, never that '
            'they have no phone.'
            ' ' + UNDELIVERED
        ),
        input_schema={
            'type': 'object',
            'properties': {'row_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': MAX_PEOPLE}},
            'required': ['row_ids'],
        },
        handler=handle,
        found_noun='phone',
        price=PRICE,
    )
)
