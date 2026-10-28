"""One registration object per vendor (#598): its name, its meter, its unit price and its tariffs.

A new vendor is one ``register(Vendor(...))`` here. ``paid_call`` reads the tariff and the price from it,
``record_vendor_call`` takes the ``Vendor`` itself, so a vendor that bills with no price is a type error
rather than a silent zero. The vendor's ``name`` is also its admin kill switch ``vendor_disabled:<name>``;
that namespace is wider than this table (Treg's ``pdl`` and ``hunter``, the mailbox providers ``google``
and ``microsoft`` have a switch and no meter), so the switch stays a name in ``app.admin.settings``.

Each client still reads its own API key from ``Settings``: one reader per key, so a copy here would be a
second place to keep in step, not a fact taken out of one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def _no_fields(value: Any) -> dict[str, Any]:
    return {}


@dataclass(frozen=True, slots=True)
class Tariff:
    """What one SUCCESSFUL call costs, and what its ``.end`` line says.

    ``credits`` and ``fields`` both read the unwrapped ``Ok`` value: the row
    count that decides the price is the same row count the cost dashboard
    needs next to it, and computing it twice is how the two drift.

    ``credits`` is a float because not every vendor sells whole ones: AI Ark
    prices company rows at 0.1 credit each. The alternative — a smaller
    integer unit — would put two units under one provider's name, which is
    the same wrong-unit sum from the other side.
    """

    credits: Callable[[Any], float]
    fields: Callable[[Any], dict[str, Any]] = _no_fields


@dataclass(frozen=True, slots=True)
class Vendor:
    """A provider we call: ``name`` is the ``vendor_calls.vendor`` value and the ``<name>.<op>`` prefix.

    ``usd_per_unit`` prices one unit of the vendor's meter, the number a ``Tariff`` returns.
    ``meter`` names that unit on the ``.end`` log line (``<name>_<meter>``): Treg bills a prepaid
    balance in micro-dollars, and a field called ``treg_credits`` holding micro-dollars is a number
    that gets summed as the wrong unit at 3am.
    ``tariffs`` maps an operation to its price for ``paid_call``; a vendor that writes its
    ``vendor_calls`` row itself (``record_vendor_call``) has none.
    """

    name: str
    usd_per_unit: float
    tariffs: Mapping[str, Tariff] = field(default_factory=dict)
    meter: str = 'credits'

    @property
    def meter_key(self) -> str:
        return f'{self.name}_{self.meter}'


#: name → vendor, filled by :func:`register`.
VENDORS: dict[str, Vendor] = {}


def register(vendor: Vendor) -> Vendor:
    if vendor.name in VENDORS:
        raise ValueError(f'vendor {vendor.name!r} is registered twice')
    VENDORS[vendor.name] = vendor
    return vendor


def _treg_micro(response: Any) -> int:
    """What Treg says this call cost, read off its own meter.

    Treg is a pass-through proxy that prices per call and answers with
    ``X-Treg-Cost-Micro``, so the price is the vendor's number rather than
    one of ours — the tariff here is where to read it, not what it is.

    A replay is the vendor's idempotency doing exactly what #2306 asks of
    ours: Treg recognised the ``Idempotency-Key``, did NOT charge again, and
    repeats the ORIGINAL cost in the header. It is attributed at zero, so
    summing the lines gives what was spent and not what was quoted.

    A missing or unreadable header reads 0: the call happened and its line
    has to be written either way. The raw header rides on that line too —
    a zero we guessed must not read like a zero the vendor quoted.
    """
    if response.headers.get('X-Treg-Idempotent-Replay') == 'true':
        return 0
    try:
        return int(response.headers.get('X-Treg-Cost-Micro'))
    except TypeError, ValueError:
        return 0


# ponytail: static list prices; correct ``usd_per_unit`` here when an invoice disagrees.

APOLLO = register(
    Vendor(
        name='apollo',
        # 1 credit: add-on pack 2,500 for $50 on Zenbree's Apollo receipt of 2026-09-30 (seat $65/mo apart)
        usd_per_unit=0.02,
        tariffs={
            # Search returns previews, no reveal — free on every Apollo plan.
            'search_people': Tariff(credits=lambda _: 0, fields=lambda v: {'count': len(v.people)}),
            'match_person': Tariff(credits=lambda _: 1),
            # Metered by the vendor: the poll answers with ``credits_consumed``, so
            # the call hands back ``(phone, credits, finished)`` and the tariff reads it.
            'phone_reveal': Tariff(credits=lambda v: v[1]),
            # Billed per page whatever came back — a zero-row page still costs one.
            'search_organizations': Tariff(credits=lambda _: 1, fields=lambda v: {'count': len(v)}),
            # Same endpoint as above, same rule (#259): Apollo meters the request,
            # not the rows, so the zero-row page every no-results funnel ends on is
            # one credit too. Pay-if-found (#1423) is our promise to the user, not
            # Apollo's to us: the search has no ``Price``, so user credits stay 0; vendor cost not.
            'discover_organizations': Tariff(
                credits=lambda _: 1,
                fields=lambda v: {'total_entries': v.total_entries, 'rows': len(v.companies), 'page': v.page},
            ),
            'enrich_organization': Tariff(credits=lambda _: 1, fields=lambda v: {'org_id': v.id}),
            'get_job_postings': Tariff(
                credits=lambda v: 1 if v.postings else 0,
                fields=lambda v: {'total': v.total, 'rows': len(v.postings)},
            ),
        },
    )
)

# AI Ark sells a prepaid balance (its 402 is "balance exhausted"), and
# until now spent it with no attribution line at all — the 3.6% of #2049
# at zero, for a provider that was simply never on the list. It meters
# nothing on the wire: no cost header, no counter in the body, so these
# three numbers are the vendor's prices written down where a wrong one is
# a one-line diff instead of a silence.
AIARK = register(
    Vendor(
        name='aiark',
        usd_per_unit=0.01,  # estimate: AI Ark prepaid credit; verify against the first top-up
        tariffs={
            # Previews, no email — but NOT free, unlike Apollo's preview search. The
            # vendor publishes 0.5 credit per "person profile" row, and a live
            # balance-delta measurement confirmed it: ``POST /v1/people`` with
            # size=100 took the staging balance from 9818.2 to 9768.2 (2026-09-28),
            # 50 credits for 100 rows. Priced per row returned, so an empty page is 0.
            # Divided rather than multiplied, for the same float reason as below.
            'search_people': Tariff(credits=lambda v: len(v.people) / 2, fields=lambda v: {'count': len(v.people)}),
            # The reveal: one email, one credit, the paid half of the split.
            #
            # A reveal that matches nothing (``found: false`` → ``CONTACTS.NO_MATCH``)
            # reads zero here, because the decoder returns ``Err`` and an ``Err`` never
            # bills. That is OUR pricing promise (#1423), not a vendor statement: AI
            # Ark answered 200 and may well have taken the reveal off the prepaid
            # balance anyway. So the sum of these lines is a FLOOR on what AI Ark
            # charged, and the balance draining faster than it is the expected shape
            # of that gap, not a new bug. Pricing the dial instead of the decode —
            # what ``treg.call`` below does — is the fix if it ever matters.
            'find_email': Tariff(credits=lambda _: 1, fields=lambda v: {'found': v.found}),
            # Billed per ROW, not per call: 0.1 credit each. An empty page costs
            # nothing because it is priced by what came back, not by the request.
            # Divided rather than multiplied: ``0.1 * 3`` logs 0.30000000000000004.
            'search_companies': Tariff(
                credits=lambda v: len(v.companies) / 10, fields=lambda v: {'count': len(v.companies)}
            ),
        },
    )
)

TREG = register(
    Vendor(
        name='treg',
        usd_per_unit=1e-6,
        meter='cost_micro',
        tariffs={
            # One operation for the three Treg clients (people / commerce / seo):
            # they are three bounded contexts speaking one protocol, and the thing
            # that costs money is the round trip, whatever the proxy forwards it to.
            # The paid call is therefore the DIAL, not the decode — a 402, a provider
            # 404 or an unreadable body are all answers the meter has a number for,
            # and only a transport failure (an Err here) dialled nothing.
            #
            # One operation also means the event name no longer says which context
            # paid, the way ``commerce.treg.ok`` and ``seo.treg.ok`` did before it
            # replaced them: the three clients pass ``bounded_context`` instead.
            # ``endpoint_id`` would not do — its values are constructor arguments of
            # the adapters, so splitting the spend by them means keeping a copy of
            # that mapping wherever the lines are summed.
            'call': Tariff(
                credits=_treg_micro,
                fields=lambda r: {
                    'status': r.status_code,
                    'idempotent_replay': r.headers.get('X-Treg-Idempotent-Replay') == 'true',
                    # The meter verbatim, because the attributed number cannot say
                    # which zero it is: a call the vendor gave away, or one whose
                    # header never arrived. Absent reads None, garbled reads itself.
                    'cost_micro_header': r.headers.get('X-Treg-Cost-Micro'),
                },
            ),
        },
    )
)

# Instantly API calls are the customer's own workspace and key: free for Zenbree, counted on /admin/costs only (#929).
INSTANTLY = register(
    Vendor(
        name='instantly',
        usd_per_unit=0.0,
        tariffs={
            'workspace': Tariff(credits=lambda _: 0),
            'list_campaigns': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v)}),
            'list_accounts': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v)}),
            'get_campaign': Tariff(credits=lambda _: 0),
            'add_leads': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v.created)}),
            'find_leads': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v)}),
            # #930: the webhook Zenbree registers, and the reconciliation reads
            'create_webhook': Tariff(credits=lambda _: 0),
            'get_webhook': Tariff(credits=lambda _: 0),
            'delete_webhook': Tariff(credits=lambda _: 0),
            'list_emails': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v.items)}),
            'list_leads': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v.items)}),
            'get_lead': Tariff(credits=lambda _: 0),
            'delete_lead': Tariff(credits=lambda _: 0),
        },
    )
)

# HubSpot API calls are free (rate limited, not billed): the customer's own HubSpot, counted on /admin/costs only.
HUBSPOT = register(
    Vendor(
        name='hubspot',
        usd_per_unit=0.0,
        tariffs={
            'read_contacts': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v)}),
            'read_companies': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v)}),
            'find_owner': Tariff(credits=lambda _: 0),
            'deal_companies': Tariff(credits=lambda _: 0, fields=lambda v: {'found': len(v)}),
            'find_company': Tariff(credits=lambda _: 0),
            'write': Tariff(credits=lambda _: 0),  # a create, an update or an association (#527)
        },
    )
)

SERPER = register(Vendor(name='serper', usd_per_unit=0.001))  # per billed /places page ($50 / 50k)
# per verification ($37 / 10k, credits never expire)
MILLIONVERIFIER = register(Vendor(name='millionverifier', usd_per_unit=0.0037))
UNIPILE = register(Vendor(name='unipile', usd_per_unit=0.0))  # flat fee per connected account, not per call
# estimate: Hobby $16 / 3k credits, 1 credit per search call
FIRECRAWL = register(Vendor(name='firecrawl', usd_per_unit=0.0053))
EXA = register(Vendor(name='exa', usd_per_unit=0.007))  # Exa Auto Search: $7 / 1k requests
