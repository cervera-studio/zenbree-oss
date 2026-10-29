"""AI Ark implementation of :class:`ContactEnrichmentService`.

The single-contact twin of the bulk path in the earlier implementation's ``app.people.reveal_dispatch``.
When people search runs on AI Ark, the rows the user picks from carry AI Ark
UUIDs and a LinkedIn URL; ``enrich_email`` used to hand that UUID to Apollo's
``match_person``, which cannot match it, and fall through to Treg. This adapter
asks the provider that owns the id first.

Anchors, in order: the row's own AI Ark id (a dashed UUID), else its LinkedIn
URL — ``/v1/people/export/single`` accepts only those two. Anything else (an
Apollo ObjectId with no LinkedIn, a bare name + company) is not something AI
Ark can reveal, so the adapter answers ``MATCH_INSUFFICIENT_INPUT`` without a
call and the chain moves on.

Only an email is a result here. The LinkedIn URL on the row was free at search
time, so an AI Ark record with no deliverable address stays
``Err(CONTACTS.NO_MATCH)`` rather than an ``Ok`` whose LinkedIn would make it
chargeable and stop the chain before Apollo / Treg are asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.core.errors import Err, Result, err, ok
from app.people.aiark_client import normalize_ark_email_status
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
    from app.people.aiark_client import AiArkClient

logger = structlog.get_logger()


def is_aiark_id(pid: str) -> bool:
    # From the earlier implementation reveal_dispatch: AI Ark ids are dashed UUIDs, Apollo ObjectIds have no dash.
    return bool(pid) and '-' in pid


class AiArkContactEnrichment(ContactEnrichmentService):
    """Reveal one person's email through ``AiArkClient.find_email``."""

    def __init__(self, client: AiArkClient) -> None:
        self._client = client

    async def reveal(self, hints: ContactRevealHints) -> Result[ContactFacts]:
        if not self._client.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        pid = hints.directory_person_id
        anchor = pid if pid and is_aiark_id(pid) else hints.linkedin_url
        if not anchor:
            return err(
                'CONTACTS.MATCH_INSUFFICIENT_INPUT',
                detail='this contact lookup needs its own person id or a LinkedIn URL',
            )

        result = await self._client.find_email(anchor)
        if isinstance(result, Err):
            logger.info('aiark.contact_enrichment.err', aiark_code=result.error.code)
            await record_lookup('aiark', hints, None)
            return result

        found = result.value
        status = normalize_ark_email_status(found.status)
        facts = ContactFacts(
            id=contact_row_key(hints, found.person_id, found.address, hints.linkedin_url),
            source=ContactSource.aiark,
            first_name=hints.first_name,
            last_name=hints.last_name,
            email=found.address,
            email_status=status,
            email_confidence=apollo_confidence(status),
            linkedin_url=hints.linkedin_url,
            organization=hints.organization_name,
            organization_domain=hints.organization_domain,
        )
        await record_lookup('aiark', hints, facts)
        return ok(facts)
