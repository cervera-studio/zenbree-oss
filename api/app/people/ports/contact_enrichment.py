"""Contact-enrichment port — reveal one person's contact channels.

The people-side twin of
:mod:`app.knowledge.ports.company_enrichment`: same Protocol-plus-frozen-
dataclasses shape, same Result discipline, one fallback chain.

Adapters under ``app.people.adapters.*`` implement this protocol:

- ``ApolloContactEnrichment`` — wraps ``ApolloClient.match_person``; the
  incumbent and the only adapter live today; ``ContactSource.apollo``.
- ``TregContactEnrichment`` — wraps ``TregClient``, which fronts
  People Data Labs / Hunter behind one credential;
  ``ContactSource.treg``. Off unless a Treg key AND the
  ``treg_contact_enrichment`` gate are both present.
- ``CompositeContactEnrichment`` — chains adapters in priority order and
  short-circuits on the first ``Ok`` that carries a real contact channel.

Result discipline
-----------------
Implementations MUST be idempotent and MUST return
``Result[ContactFacts]`` (``Ok[ContactFacts]`` | ``Err``) — never raise.
The composite composes adapters via Result, so a single raised exception
in an adapter would break the fallback chain and is treated as a bug, not
a recoverable miss. Transport / adapter exceptions are converted to
``Err`` at the adapter boundary, matching the project-wide rule from
``CLAUDE.md`` (see "Result pattern" + "Ports are non-bypassable").

Why ``ContactFacts`` carries the whole row
------------------------------------------
It is deliberately a superset of the plain "reveal payload": it also
carries ``id``, ``title``, ``seniority``, ``country``, ``email_status``
and ``phone_status``. Those are not decoration — the chat handler's
downstream machinery keys on them:

- ``id`` is the provider person id the session contact accumulator dedupes
  on and the reveal-idempotency cache (#1509) replays on. A row without it
  is dropped by ``_pending_rows`` and can never be replayed, so a reveal
  that loses the id silently double-charges on the next attempt.
- ``email_status`` is the raw provider status string. ``email_confidence``
  is the normalised 0..1 chip value derived from it, but the raw string
  stays on the row because the LLM and the export both read it.
- ``title`` / ``seniority`` / ``country`` are what makes the row useful to
  the model at all.

:meth:`ContactFacts.to_dict` therefore reproduces
``PersonEnriched.to_dict`` key-for-key — the Apollo path through this port
must be byte-identical to the pre-port handler, which is the regression
gate for shipping this behind a default-OFF flag.

Why ``has_contact_channel`` is a property, not a field
------------------------------------------------------
It is the chargeable predicate: ``_handle_enrich_email`` returns
``CONTACTS.NO_CONTACT_REVEALED`` (which makes ``credit_guard`` refund) when
it is False. Mirroring ``PersonEnriched.has_contact_channel`` as a computed
property means no adapter can claim a channel it did not reveal, so the
money-leak guard cannot drift per-provider.

Error code namespace
--------------------
Stable ``AppError.code`` values for adapter implementers (do not embed
variable metadata in the code string; put it in the context kwargs):

- the Apollo adapter mints **no codes of its own**. It passes
  ``ApolloClient``'s ``CONTACTS.*`` codes through verbatim
  (``CONTACTS.NO_MATCH``, ``CONTACTS.RATE_LIMITED``,
  ``CONTACTS.PAYMENT_REQUIRED``, ``CONTACTS.TRANSPORT``, …), because with
  the Treg gate OFF that adapter IS the service and the handler must
  surface exactly the code it surfaced before this port existed.
- ``CONTACTS.TREG_NO_MATCH`` — Treg's providers matched nothing, or
  matched a record with no usable channel.
- ``CONTACTS.TREG_IDENTITY_UNCONFIRMED`` — a Treg sub-provider returned a
  contact channel but no name on an anchored row whose lookup was not
  pinned by an email or a LinkedIn URL.  The preview's name would sit
  above somebody else's contact details, so the record is refused.
- ``CONTACTS.TREG_OUT_OF_BALANCE`` — Treg returned HTTP 402: the org's
  prepaid balance cannot cover the call. Distinct from a transport error
  on purpose — the fix is "add funds", not "page on-call". The top-up URL
  rides on ``Err.context['topup_url']``.
- ``CONTACTS.TREG_NOT_CONFIGURED`` — no Treg key. Note this does NOT cover
  "the endpoint's provider has no credential connected to the Treg org",
  which Treg answers with a 404 carrying an actionable message and which
  therefore surfaces as ``CONTACTS.TRANSPORT_ERROR`` with the "connect a
  provider" text in ``detail``.
- ``CONTACTS.TRANSPORT_ERROR`` — network / 4xx / 5xx / timeout upstream.
  The upstream status rides on ``Err.context['treg_status']`` (Treg) or
  ``Err.context['apollo_code']`` (Apollo).
- ``CONTACTS.MATCH_INSUFFICIENT_INPUT`` — reuse from ``apollo_client``: the
  hints carry no identifier the provider can anchor on.
- ``CONTACTS.NO_CONTACT_REVEALED`` — reuse: matched, but no channel came
  back. Raised by the handler, not by adapters (adapters use the
  per-provider ``*_NO_MATCH`` codes so the composite can tell them apart).
- ``CONTACTS.ADAPTER_EXCEPTION`` — the composite caught a child adapter
  violating the "never raise" contract; the class name is on
  ``Err.context['adapter']``.
- ``CONTACTS.NO_ADAPTERS_CONFIGURED`` — composite built with an empty chain.
- ``CONTACTS.SOURCES_UNREACHABLE`` — minted by the composite when a source
  that failed instead of answering has to outrank a remembered empty
  ``Ok``. That is the whole point: an empty match must not bury a broken
  channel (#1923). When there is no empty ``Ok`` to outrank, the last
  ``Err`` propagates with its own code, unchanged.

``Err.context['unreachable_sources']`` is the companion key, and rides on
ANY failure that had a source fail on the way: one entry per source, in
call order, written by :func:`with_unreachable_sources`. An adapter
fronting several providers (the Treg chain) reports each of them by name.
A caller reads it to tell "the channel is broken, retry" from "nobody has
this contact", which no error code alone can say.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.billing import paid_call
from app.core.errors import AppError, Err

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.core.errors import Result


class ContactSource(StrEnum):
    """Which provider produced a reveal.

    Wire values feed the provenance ``extraction_method`` tag (see
    ``app/people/provenance.py``), which the FE chip renders a label from.

    ``treg`` is one generic tag rather than one per sub-provider: Treg's
    ``/call/<endpoint-id>`` proxy returns the upstream provider's response
    verbatim with no envelope, so the response body itself does not say
    which of PDL / Hunter answered. The adapter knows
    which endpoint id it dialled and logs it, but that stays in logs — the
    row-level tag would be a lie the moment the endpoint mapping changes.

    ``aiark`` is a reveal ``AiArkClient.find_email`` answered, the head of the
    chain whenever the people-search flag hands the chain an AI Ark client.
    """

    apollo = 'apollo'
    treg = 'treg'
    aiark = 'aiark'
    unresolved = 'unresolved'


@dataclass(frozen=True, slots=True)
class ContactRevealHints:
    """Everything ``_handle_enrich_email`` collects from the tool input.

    Anchor priority is the adapter's business, but in practice it mirrors
    Apollo's: ``directory_person_id`` (an exact 1:1 provider id) beats
    ``linkedin_url`` beats ``email`` beats name + organisation.

    ``reveal_phone`` is a request, not a promise — a provider that has no
    phone for the person leaves ``ContactFacts.phone`` at ``None`` and the
    reveal is still chargeable if an email or LinkedIn came back.
    """

    directory_person_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    organization_name: str | None = None
    organization_domain: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    reveal_phone: bool = False


@dataclass(frozen=True, slots=True)
class ContactFacts:
    """The reveal payload an adapter returns on success.

    ``id`` is a join key, not provenance. Whenever the caller passed a
    ``directory_person_id``, an adapter must return that id here, whatever
    id its own provider minted: the table sync, the session accumulator and
    the reveal cache all address the revealed contact by it, and they were
    filled from the preview the anchor came from. An adapter is free to key
    on its own id only when the reveal carried no anchor to begin with. The
    provider that answered stays readable on ``source``. Every adapter maps
    back through :func:`contact_row_key`, Apollo included: an AI Ark uuid
    anchor is not an id Apollo can match 1:1, so its answer carries its own.

    ``email_confidence`` is the normalised 0..1 chip value (Apollo:
    ``apollo_confidence(email_status)``; Treg: derived from whatever
    verification signal the sub-provider returned, ``None`` when it returns
    none). ``None`` means "no chip" by FE contract — never 0.0, which the
    user would read as a meaningful low score.
    """

    id: str
    source: ContactSource
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    seniority: str | None = None
    email: str | None = None
    email_status: str | None = None
    email_confidence: float | None = None
    linkedin_url: str | None = None
    organization: str | None = None
    organization_domain: str | None = None
    country: str | None = None
    organization_id: str | None = None
    organization_logo_url: str | None = None
    organization_industry: str | None = None
    organization_industries: tuple[str, ...] = ()
    organization_description: str | None = None
    organization_employee_count: int | None = None
    organization_annual_revenue: int | None = None
    organization_annual_revenue_printed: str | None = None
    organization_founded_year: int | None = None
    organization_headquarters_location: str | None = None
    organization_linkedin_url: str | None = None
    organization_technologies: tuple[str, ...] = ()
    organization_funding_stage: str | None = None
    organization_total_funding: int | None = None
    organization_total_funding_printed: str | None = None
    organization_latest_funding_amount: int | None = None
    organization_latest_funding_amount_printed: str | None = None
    organization_latest_funding_date: str | None = None
    organization_phone: str | None = None
    organization_parent_id: str | None = None
    organization_suborganization_count: int | None = None
    organization_website_url: str | None = None
    organization_blog_url: str | None = None
    organization_angellist_url: str | None = None
    organization_crunchbase_url: str | None = None
    organization_twitter_url: str | None = None
    organization_facebook_url: str | None = None
    organization_alexa_ranking: int | None = None
    organization_keywords: tuple[str, ...] = ()
    organization_secondary_industries: tuple[str, ...] = ()
    organization_street_address: str | None = None
    organization_postal_code: str | None = None
    organization_technology_categories: tuple[str, ...] = ()
    organization_funding_events: tuple[str, ...] = ()
    city: str | None = None
    state: str | None = None
    person_location: str | None = None
    photo_url: str | None = None
    headline: str | None = None
    twitter_url: str | None = None
    github_url: str | None = None
    facebook_url: str | None = None
    extrapolated_email_confidence: float | None = None
    is_likely_to_engage: bool | None = None
    departments: tuple[str, ...] = ()
    subdepartments: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    employment_history: tuple[str, ...] = ()
    phone: str | None = None
    phone_status: str | None = None

    @property
    def has_contact_channel(self) -> bool:
        """True when the reveal surfaced a usable channel: an email, a
        LinkedIn URL, or a revealed phone number.

        The chargeable predicate — see the module docstring. Mirrors
        ``PersonEnriched.has_contact_channel`` exactly so the Apollo path
        keeps its existing billing behaviour through this port.
        """
        return bool(self.email or self.linkedin_url or self.phone)

    def to_dict(self) -> dict[str, Any]:
        """The row shape the chat handler returns and accumulates.

        Key-for-key identical to ``PersonEnriched.to_dict`` — the session
        accumulator, the export writer and the reveal cache all read these
        exact keys, and the FE chip renders from them.
        """
        row = {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'title': self.title,
            'seniority': self.seniority,
            'email': self.email,
            'email_status': self.email_status,
            'linkedin_url': self.linkedin_url,
            'organization': self.organization,
            'organization_domain': self.organization_domain,
            'organization_id': self.organization_id,
            'organization_logo_url': self.organization_logo_url,
            'city': self.city,
            'state': self.state,
            'country': self.country,
            'person_location': self.person_location,
            'employment_history': list(self.employment_history),
            'phone': self.phone,
            'phone_status': self.phone_status,
        }
        enrichment = {
            'organization_industry': self.organization_industry,
            'organization_industries': list(self.organization_industries),
            'organization_description': self.organization_description,
            'organization_employee_count': self.organization_employee_count,
            'organization_annual_revenue': self.organization_annual_revenue,
            'organization_annual_revenue_printed': self.organization_annual_revenue_printed,
            'organization_founded_year': self.organization_founded_year,
            'organization_headquarters_location': self.organization_headquarters_location,
            'organization_linkedin_url': self.organization_linkedin_url,
            'organization_technologies': list(self.organization_technologies),
            'organization_funding_stage': self.organization_funding_stage,
            'organization_total_funding': self.organization_total_funding,
            'organization_total_funding_printed': self.organization_total_funding_printed,
            'organization_latest_funding_amount': self.organization_latest_funding_amount,
            'organization_latest_funding_amount_printed': (self.organization_latest_funding_amount_printed),
            'organization_latest_funding_date': self.organization_latest_funding_date,
            'organization_phone': self.organization_phone,
            'organization_parent_id': self.organization_parent_id,
            'organization_suborganization_count': self.organization_suborganization_count,
            'organization_website_url': self.organization_website_url,
            'organization_blog_url': self.organization_blog_url,
            'organization_angellist_url': self.organization_angellist_url,
            'organization_crunchbase_url': self.organization_crunchbase_url,
            'organization_twitter_url': self.organization_twitter_url,
            'organization_facebook_url': self.organization_facebook_url,
            'organization_alexa_ranking': self.organization_alexa_ranking,
            'organization_keywords': list(self.organization_keywords),
            'organization_secondary_industries': list(self.organization_secondary_industries),
            'organization_street_address': self.organization_street_address,
            'organization_postal_code': self.organization_postal_code,
            'organization_technology_categories': list(self.organization_technology_categories),
            'organization_funding_events': list(self.organization_funding_events),
            'photo_url': self.photo_url,
            'headline': self.headline,
            'twitter_url': self.twitter_url,
            'github_url': self.github_url,
            'facebook_url': self.facebook_url,
            'extrapolated_email_confidence': self.extrapolated_email_confidence,
            'is_likely_to_engage': self.is_likely_to_engage,
            'departments': list(self.departments),
            'subdepartments': list(self.subdepartments),
            'functions': list(self.functions),
        }
        return row | {key: value for key, value in enrichment.items() if value not in (None, [])}


@runtime_checkable
class ContactEnrichmentService(Protocol):
    """Port. Adapters: ApolloContactEnrichment, TregContactEnrichment,
    CompositeContactEnrichment (chains them).

    Implementations MUST be idempotent and MUST return
    ``Result[ContactFacts]`` (never raise) so the composite can chain them.
    """

    async def reveal(self, hints: ContactRevealHints) -> Result[ContactFacts]: ...


def with_unreachable_sources(failure: Err, sources: Sequence[str]) -> Err:
    """Re-issue ``failure`` with the sources that did not answer attached.

    Code, detail and existing context are preserved: on-call still reads
    the original verdict, and the caller gains what it needs to tell a
    broken channel from an honest "no such contact" (#1923). An empty
    ``sources`` returns the failure untouched, so the key is absent rather
    than present-and-empty and a reader cannot mistake "nothing failed"
    for "we did not check".

    Lives here, next to the docstring that defines the key, because both
    the Treg adapter and the composite write it and a drift in the name
    would silently downgrade the message the user reads.
    """
    if not sources:
        return failure
    return Err(
        AppError(
            code=failure.error.code,
            detail=failure.error.detail,
            context={**failure.error.context, 'unreachable_sources': tuple(sources)},
        )
    )


def serves(hints: ContactRevealHints, facts: ContactFacts) -> bool:
    """Whether a reveal ends the chain: a phone lookup needs the phone, any other a channel."""
    return bool(facts.phone) if hints.reveal_phone else facts.has_contact_channel


async def record_lookup(provider: str, hints: ContactRevealHints, facts: ContactFacts | None) -> None:
    """One ``contact_lookups`` row for the per-provider hit rate: did ``provider`` find the asked channel."""
    channel = 'phone' if hints.reveal_phone else 'email'
    found = bool(facts and (facts.phone if hints.reveal_phone else facts.email))
    await asyncio.to_thread(paid_call.record_lookup, provider, channel, found)


def contact_row_key(
    hints: ContactRevealHints,
    provider_id: str | None,
    email: str | None,
    linkedin_url: str | None,
) -> str:
    """The id every reader downstream joins the revealed row on (#2341).

    The one place a provider answer becomes a row key: every adapter that
    builds ``ContactFacts`` calls this, and
    ``tests/architecture/test_single_contact_key_column.py`` fails on a
    second copy of the fallback.

    ``ContactFacts.id`` is a join key, not provenance. The table sync
    (#1911) addresses rows by the hidden ``_contact_key`` column, the
    session accumulator dedupes on it and the reveal-idempotency cache
    (#1509) replays on it, and all three were filled with the directory
    person id the preview row was rendered under. So when the caller came
    from a directory row, that anchor wins over the id the sub-provider
    minted for itself: a PDL or Hunter id matches none of those
    three, and the paid row would be appended beside the preview instead
    of landing in it. The preview row then stayed pending, and the replay
    cache, which looks the session rows up by the anchor it was given, no
    longer found the contact it had just paid for, so the next ask for the
    same person paid the fallback all over again.

    The provider id stays as the next-best key for a reveal that carries no
    anchor (an address or a LinkedIn URL alone), which is where it was the
    only id available all along. The source stays readable on
    ``ContactSource.treg``, and the endpoint that answered is already
    logged on every call.

    What this costs, since it is a trade and not a free repair: Treg
    matches on name plus organisation domain, not on a 1:1 id the way
    ``match_person`` does, so a sub-provider answer about a DIFFERENT human
    now lands inside the anchored row instead of beside it, and is replayed
    as that row's cached reveal. Apollo carries it only on an anchor that
    is not its own id (an AI Ark uuid), which is why ``enrich_email`` runs the
    LinkedIn contradiction check there too. Beyond that check nothing sees the swap: the
    disclosure that compares a paid row against its preview reads title
    and seniority, never the name, which the preview holds obfuscated
    anyway. What the user does get is the name cell, since the decoders
    in the Treg adapter take the sub-provider's name over the hints and the sync writes
    it into the row: a swap that arrives WITH a name rewrites the cell in
    front of them. The one that passes unseen is a sub-provider answer
    with no name fields, where ``or hints.first_name`` keeps the preview's
    name sitting above somebody else's address and phone. Anyone tightening
    this should tighten the MATCH, not the id.
    """
    return hints.directory_person_id or provider_id or email or linkedin_url or ''
