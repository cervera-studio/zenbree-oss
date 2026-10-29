"""enrich_email: work-email waterfall Apollo → Treg (PDL/Hunter) → verifier.

Pay-if-found: the store bills each email the verifier did not reject. Vendor
spend is logged per call by ``paid_call`` either way.
"""

from __future__ import annotations

import asyncio

from app.agent.tools.handlers._rows import (
    DISABLED,
    UNDELIVERED,
    no_row_match,
    nothing_to_search,
    providers_off,
    row_uuid,
    unmatched_data,
    unmatched_ids,
)
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.billing.paid_call import current_user_id
from app.billing.price import Price
from app.core.db import get_conn
from app.core.errors import is_err
from app.exclusions.rules import is_excluded, list_rules
from app.lists import lookup_outcome
from app.lists.lookups import (
    FAILED,
    FAILED_EMAIL_LAST_SQL,
    NOT_FOUND,
    OPEN_EMAIL_SQL,
    deliverable,
    email_lookup,
    phone_lookup,
)
from app.people.adapters.aiark_contact_enrichment import is_aiark_id
from app.people.adapters.composite_contact_enrichment import (
    CONTACT_PROVIDERS,
    all_off,
    build_reveal_chain,
    source_failed,
)
from app.people.aiark_client import AiArkClient
from app.people.apollo_client import ApolloClient
from app.people.email_verification import email_status
from app.people.entity_match import normalise_domain
from app.people.person_name import first_and_last, masked_parts
from app.people.ports.contact_enrichment import ContactRevealHints
from app.people.treg_client import TregClient

MAX_PEOPLE = 50
_CONCURRENCY = 5
# 1 credit = 1 email (~€0.05); Apollo match ~€0.017 + verifier, billed even with no email. The name and profile
# cells of the reveal are free on purpose (#476, docs/contracts-m1.md).
PRICE = Price(
    credits=1,
    keys=('email', 'email_status', 'email_source'),
    found=lambda cells: deliverable(email_lookup(cells, {})),
    batch=MAX_PEOPLE,  # no ids: the next batch of the list
)


def load_rows(list_id: str, row_ids: list[str]) -> list[dict]:
    """The job's List rows by id, cells flattened. Scoped by list_id: that is the tenant guard.
    Rows excluded in the list or by its project's exclusions are left out."""
    row_ids = [u for u in map(row_uuid, row_ids) if u]
    if not row_ids:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, cells FROM list_rows WHERE list_id = %s AND id = ANY(%s::uuid[]) AND NOT excluded',
            (list_id, row_ids),
        ).fetchall()
        rules = list_rules(conn, list_id)
    return [{**(r['cells'] or {}), 'row_id': str(r['id'])} for r in rows if not is_excluded(rules, r['cells'] or {})]


def next_unenriched(list_id: str) -> list[str]:
    """Person rows never searched, then those whose search failed, oldest first: the batch when no row_ids are
    given, e.g. a list uploaded from CSV whose row ids the model never saw. Rows its project's exclusions match are
    passed over, so a rule added after the upload cannot stall the batches. A row refunded at the end of a turn
    (#45) is asked for again by row id only, so the vendor is not paid every turn."""
    with get_conn() as conn:
        rules = list_rules(conn, list_id)
        # ponytail: scans every untried row of the list; fine at upload size (5000 rows)
        rows = conn.execute(
            "SELECT r.id, r.cells FROM list_rows r JOIN lists l ON l.id = r.list_id AND l.entity = 'person'"
            f' WHERE r.list_id = %s AND NOT r.excluded AND {OPEN_EMAIL_SQL} ORDER BY {FAILED_EMAIL_LAST_SQL}, r.seq',
            (list_id,),
        ).fetchall()
    return [str(r['id']) for r in rows if not is_excluded(rules, r['cells'] or {})][:MAX_PEOPLE]


def _hints(p: dict) -> ContactRevealHints:
    # A CSV row often has only «Nome completo»: without a first name Apollo cannot match it (#509).
    first, last = first_and_last(p)
    if masked_parts(last) is not None:  # Apollo-masked surname: the person id is the anchor
        last = ''
    return ContactRevealHints(
        directory_person_id=p.get('person_id') or None,
        first_name=first or None,
        last_name=last or None,
        organization_name=p.get('company_name') or None,
        organization_domain=normalise_domain(p.get('company_domain') or '') or None,
        linkedin_url=p.get('linkedin_url') or None,
        email=p.get('email') or None,  # only a phone search has one: enrich_email takes rows with no email
    )


def failed_person(p: dict) -> dict:
    """A person the lookup left with no verdict, as the tool data names them: the chat says "retry", never "none"."""
    return {'row_id': p['row_id'], 'name': ' '.join(filter(None, first_and_last(p)))}


def has_source(p: dict, off: frozenset[str]) -> bool:
    """Can a source that is on and has a key search this person? AI Ark reveals only its own ids, so with Apollo
    and Treg out a row from anywhere else has nobody to ask. Asking anyway gets ``NOT_CONFIGURED`` or an empty
    chain, which both read as a miss and would store a final ``not_found`` nobody looked for (#520)."""
    treg = 'treg' not in off and not {'pdl', 'hunter'} <= off
    return 'apollo' not in off or treg or ('aiark' not in off and is_aiark_id(p.get('person_id') or ''))


async def _enrich_one(p: dict, apollo: ApolloClient, treg: TregClient, aiark: AiArkClient, off: frozenset[str]) -> dict:
    """The row_update for one person. ``cells.email`` is None when nothing was found. A source that failed instead of
    answering stores ``failed``, so ``next_unenriched`` takes the row again (#506); so does a person no source could
    be asked about."""
    if not has_source(p, off):
        return {'row_id': p['row_id'], 'cells': {'email': None, 'email_status': FAILED}}
    chain = build_reveal_chain(apollo, treg, aiark if is_aiark_id(p.get('person_id') or '') else None, off)
    found = await chain.reveal(_hints(p))
    if is_err(found) and source_failed(found):
        return {'row_id': p['row_id'], 'cells': {'email': None, 'email_status': FAILED}}
    cells: dict = {'email': None, 'email_status': NOT_FOUND}
    if not is_err(found) and (email := found.value.email):
        f = found.value
        cells = {
            'email': email,
            'email_status': await email_status(email),
            'email_source': str(f.source),
        }
        # A reveal also un-hides the Apollo surname and fills gaps.
        for key, value in (
            ('first_name', f.first_name),
            ('last_name', f.last_name),
            ('title', f.title),
            ('linkedin_url', f.linkedin_url),
        ):
            if value and (key == 'last_name' or not p.get(key)):
                cells[key] = value
        if f.first_name and f.last_name:
            cells['full_name'] = f'{f.first_name} {f.last_name}'
        # The new email can anchor a phone search that found nothing before: offer it again (#504).
        # ponytail: a turn that refunds this email keeps the search open on the old anchor: one free retry for
        # the customer, one vendor lookup for us.
        if phone_lookup(p, {}).state == 'not_found':
            cells['phone_status'] = None
    return {'row_id': p['row_id'], 'cells': cells}


async def handle(args: dict, ctx: ToolContext) -> ToolResult:
    current_user_id.set(ctx.user_id)
    if not ctx.list_id:
        return ToolResult(ok=False, error='there is no list yet: find people first, then enrich them')
    people_args = args.get('people') or []
    row_ids = [str(i) for i in args.get('row_ids') or [] if i]
    row_ids.extend(str(p['row_id']) for p in people_args if isinstance(p, dict) and p.get('row_id'))
    people: list[dict] = []
    unmatched: list[str] = []
    # An empty id list names nobody: the next batch, as with no ids. A non-empty one names people, and a person
    # without a row id matches no row: never a batch nobody asked for (#140).
    if args.get('row_ids') or people_args:
        people = await asyncio.to_thread(load_rows, ctx.list_id, row_ids) if row_ids else []
        if not people:
            return no_row_match('email')
        unmatched = unmatched_ids(row_ids, people)
    elif batch := await asyncio.to_thread(next_unenriched, ctx.list_id):
        people = await asyncio.to_thread(load_rows, ctx.list_id, batch)
    limit = min(MAX_PEOPLE, ctx.max_units or MAX_PEOPLE)
    people = [p for p in people if p.get('row_id') and not p.get('email')][:limit]
    if not people:
        return nothing_to_search('email', unmatched)

    apollo, treg, aiark = ApolloClient(), TregClient(), AiArkClient()
    if not (apollo.is_configured or treg.is_configured or aiark.is_configured):
        return ToolResult(ok=False, error='email finding is not configured')
    off = await asyncio.to_thread(providers_off, CONTACT_PROVIDERS)
    # A source with no key is out of the chain like one switched off: its NOT_CONFIGURED is no answer (#520).
    off |= {name for name, client in (('apollo', apollo), ('treg', treg), ('aiark', aiark)) if not client.is_configured}
    if all_off(off):
        return ToolResult(ok=False, error=DISABLED)
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(p: dict) -> dict:
        async with gate:
            return await _enrich_one(p, apollo, treg, aiark, off)

    await ctx.claim('email', [p['row_id'] for p in people])  # before the first vendor call: the rows read as searching
    updates = await asyncio.gather(*(one(p) for p in people))
    outcome = lookup_outcome.searched(
        'email',
        updates,
        no_source={p['row_id'] for p in people if not has_source(p, off)},
        unmatched=len(unmatched),
    )
    found = sum(PRICE.found(u['cells']) for u in updates)
    statuses = [u['cells']['email_status'] for u in updates]
    revealed = [{'row_id': u['row_id'], 'name': u['cells']['full_name']} for u in updates if 'full_name' in u['cells']]
    # Left with no verdict by a source that failed. A person nobody could be asked about is not here: a retry
    # finds the same sources.
    failed = [
        failed_person(p)
        for p, u in zip(people, updates, strict=True)
        if u['cells']['email_status'] == FAILED and has_source(p, off)
    ]
    return ToolResult(
        ok=True,
        row_updates=list(updates),
        entity='person',
        outcome=outcome,
        data={
            'searched': len(people),
            'found': found,
            'failed': failed,
            'statuses': statuses,
            **({'revealed': revealed} if revealed else {}),
            **unmatched_data(unmatched),
        },
    )


register(
    ToolDef(
        name='enrich_email',
        description=(
            'Find and verify work emails for people already in the list. Pass row_ids (or people objects '
            'containing row_id); the row data is loaded from the current List. A person without row_id matches '
            'no row: nobody is searched. With neither, it takes the '
            f'next {MAX_PEOPLE} rows with no verdict, rows never tried first (e.g. an uploaded list); call again for '
            'the next batch. '
            'Tries Apollo, then other contact databases, then verifies the mailbox. '
            f'Charged only per email found. Max {MAX_PEOPLE} people per call. '
            'email_status: verified, catch_all (domain accepts all, risky), mx_ok (domain only), '
            'unknown, invalid, not_found, failed (no answer yet). data.revealed names people as the source found '
            'them: use those names, not a surname the user guessed. data.failed names the people a source failed to '
            'answer for: they have no verdict yet, so say the lookup can be retried later, never that they have no '
            'email. A later turn takes them again: when every person searched is in failed, do not call again in '
            'this turn. A person with status failed and not in data.failed could not be searched by any source that '
            'is on: say so, never that they have no email.'
            ' ' + UNDELIVERED
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'row_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': MAX_PEOPLE},
                'people': {
                    'type': 'array',
                    'maxItems': MAX_PEOPLE,
                    'items': {
                        'type': 'object',
                        'properties': {
                            'row_id': {'type': 'string'},
                        },
                        'required': ['row_id'],
                    },
                },
            },
        },
        handler=handle,
        found_noun='email',
        price=PRICE,
        # No tool-level provider: each source has its own kill switch (CONTACT_PROVIDERS).
    )
)
