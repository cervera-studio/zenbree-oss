"""Apollo-backed implementation of :class:`ContactEnrichmentService`.

A thin trim of ``ApolloClient.match_person`` into the port's
``ContactFacts`` shape. Deliberately thin: this adapter exists so the
Apollo path and the Treg path are interchangeable behind one interface,
NOT to add behaviour. Every value it produces is the same value
``_handle_enrich_email`` read off ``PersonEnriched`` before the port
existed — including ``email_confidence``, which is
``apollo_confidence(email_status)`` computed here instead of in the
handler so the Treg path can supply its own equivalent.

Idempotent and never raises: transport / 4xx / 5xx errors already arrive
as ``Err`` from the client and are passed through **verbatim**, code and
all. That is deliberate and is the reason this adapter does not mint an
``APOLLO_NO_MATCH``-style code of its own: with the Treg flag OFF the
service IS this adapter, so the handler must surface exactly the
``CONTACTS.*`` code it surfaced before the port existed
(``CONTACTS.NO_MATCH``, ``CONTACTS.RATE_LIMITED``,
``CONTACTS.PAYMENT_REQUIRED``, …). Remapping them here would silently
rewrite an error taxonomy the handler, the FE and the tests already
depend on, for no gain — the composite only needs to know Err-vs-Ok to
fall through.

Anchor priority is the client's (apollo_id → linkedin_url → email → name +
org); the adapter just forwards the hints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.core.errors import Err, Result, err, ok
from app.people.ports.contact_enrichment import (
    ContactEnrichmentService,
    ContactFacts,
    ContactRevealHints,
    ContactSource,
    contact_row_key,
    record_lookup,
)
from app.people.provenance import apollo_confidence

if TYPE_CHECKING:
    from app.people.apollo_client import ApolloClient
    from app.people.apollo_models import PersonEnriched

logger = structlog.get_logger()


class ApolloContactEnrichment(ContactEnrichmentService):
    """Wraps ``ApolloClient.match_person`` → ``Result[ContactFacts]``.

    Returns:
      - ``Ok(ContactFacts)`` — Apollo matched. Note this includes a match
        with NO usable channel: whether that is chargeable is the
        composite's / handler's call via ``has_contact_channel``, not this
        adapter's, so the fallback chain can see "matched but empty" and
        try the next provider.
      - ``Err('CONTACTS.NOT_CONFIGURED')`` — no Apollo key.
      - the client's own ``Err``, verbatim — ``CONTACTS.NO_MATCH``,
        ``CONTACTS.RATE_LIMITED``, ``CONTACTS.TRANSPORT``, … See the module
        docstring for why these are not remapped.
    """

    def __init__(self, client: ApolloClient) -> None:
        self._client = client

    async def reveal(self, hints: ContactRevealHints) -> Result[ContactFacts]:
        if not self._client.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        result = await self._client.match_person(
            apollo_id=hints.directory_person_id,
            first_name=hints.first_name,
            last_name=hints.last_name,
            organization_name=hints.organization_name,
            organization_domain=hints.organization_domain,
            linkedin_url=hints.linkedin_url,
            email=hints.email,
            reveal_phone_number=hints.reveal_phone,
        )

        if isinstance(result, Err):
            logger.info('apollo.contact_enrichment.err', apollo_code=result.error.code)
            if result.error.code != 'CONTACTS.MATCH_INSUFFICIENT_INPUT':  # never dialled
                await record_lookup('apollo', hints, None)
            return result

        facts = _facts_from_person(hints, result.value)
        await record_lookup('apollo', hints, facts)
        return ok(facts)


# ── Module-private helpers ──────────────────────────────────────────────


def _facts_from_person(hints: ContactRevealHints, person: PersonEnriched) -> ContactFacts:
    """Trim Apollo's enriched record into the port's ``ContactFacts``.

    Field-for-field: ``ContactFacts.to_dict()`` must reproduce
    ``PersonEnriched.to_dict()`` exactly, because the chat handler feeds
    that dict straight into the session accumulator and the tool result.
    The one exception is ``id``: the anchor whenever the reveal carried one
    (:func:`contact_row_key`).
    """
    return ContactFacts(
        id=contact_row_key(hints, person.id, person.email, person.linkedin_url),
        source=ContactSource.apollo,
        first_name=person.first_name,
        last_name=person.last_name,
        title=person.title,
        seniority=person.seniority,
        email=person.email,
        email_status=person.email_status,
        email_confidence=apollo_confidence(person.email_status),
        linkedin_url=person.linkedin_url,
        organization=person.organization_name,
        organization_domain=person.organization_domain,
        organization_id=person.organization_id,
        organization_logo_url=person.organization_logo_url,
        organization_industry=person.organization_industry,
        organization_industries=person.organization_industries,
        organization_description=person.organization_description,
        organization_employee_count=person.organization_employee_count,
        organization_annual_revenue=person.organization_annual_revenue,
        organization_annual_revenue_printed=person.organization_annual_revenue_printed,
        organization_founded_year=person.organization_founded_year,
        organization_headquarters_location=person.organization_headquarters_location,
        organization_linkedin_url=person.organization_linkedin_url,
        organization_technologies=person.organization_technologies,
        organization_funding_stage=person.organization_funding_stage,
        organization_total_funding=person.organization_total_funding,
        organization_total_funding_printed=person.organization_total_funding_printed,
        organization_latest_funding_amount=person.organization_latest_funding_amount,
        organization_latest_funding_amount_printed=(person.organization_latest_funding_amount_printed),
        organization_latest_funding_date=person.organization_latest_funding_date,
        organization_phone=person.organization_phone,
        organization_parent_id=person.organization_parent_id,
        organization_suborganization_count=person.organization_suborganization_count,
        organization_website_url=person.organization_website_url,
        organization_blog_url=person.organization_blog_url,
        organization_angellist_url=person.organization_angellist_url,
        organization_crunchbase_url=person.organization_crunchbase_url,
        organization_twitter_url=person.organization_twitter_url,
        organization_facebook_url=person.organization_facebook_url,
        organization_alexa_ranking=person.organization_alexa_ranking,
        organization_keywords=person.organization_keywords,
        organization_secondary_industries=person.organization_secondary_industries,
        organization_street_address=person.organization_street_address,
        organization_postal_code=person.organization_postal_code,
        organization_technology_categories=person.organization_technology_categories,
        organization_funding_events=person.organization_funding_events,
        city=person.city,
        state=person.state,
        country=person.country,
        person_location=person.person_location,
        photo_url=person.photo_url,
        headline=person.headline,
        twitter_url=person.twitter_url,
        github_url=person.github_url,
        facebook_url=person.facebook_url,
        extrapolated_email_confidence=person.extrapolated_email_confidence,
        is_likely_to_engage=person.is_likely_to_engage,
        departments=person.departments,
        subdepartments=person.subdepartments,
        functions=person.functions,
        employment_history=person.employment_history,
        phone=person.phone,
        phone_status=person.phone_status,
    )
