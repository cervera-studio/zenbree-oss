"""Treg-backed implementation of :class:`ContactEnrichmentService`.

Treg is a pay-per-call proxy in front of many providers (see
:mod:`app.people.treg_client`). For person enrichment it fronts
People Data Labs, Hunter and LeadMagic. A reveal dials the two of them
that can return a contact channel, in the order
:data:`_CHAIN_BY_CAPABILITY` declares: PDL first (long tail,
people-by-profile lookups), then Hunter, cheap ($0.0049 to $0.0245) but
narrower. LeadMagic is not dialled at all; the capability declaration says
why. Lusha is not dialled either: on Treg's own key it costs ~$4.99 a call
(2026-10-01), out of proportion with what one reveal is worth.

Endpoint ids and request shapes here are the REAL ones from Treg's
catalog (``treg catalog get <id>``), verified 2026-08-12. Every provider
speaks its own dialect through the proxy, so each gets its own request
builder and its own response parser — there is no shared shape to factor
out, and pretending otherwise is what produced the previous best-effort
guesses.

Idempotency
-----------
Every provider call carries its OWN ``Idempotency-Key``, derived from one
key minted per reveal. Treg's key identifies a REQUEST, not an operation:
the first request that completes with a key binds it, and any DIFFERENT
request sent with that key is refused with HTTP 422 by the proxy, before
it reaches the provider. One key shared across the chain therefore
disabled the fallback outright (#1923): the first provider bound the key,
the rest came back 422, and the user read the refusal as "this person has no
email". Verified live 2026-08-20 in both directions: distinct per-attempt
keys run the whole chain, and repeating one attempt with its own key still
answers ``X-Treg-Idempotent-Replay: true``.

Never raises. Every failure — no key, transport error, out-of-balance,
empty match, unparseable payload — comes back as an ``Err``. A payload
missing the fields we expect is logged at warning level and treated as a
miss, never as a crash: this runs behind a chat tool, and a provider
quietly changing a field name must degrade to "Apollo answered and Treg
did not", not to a 500.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import structlog

from app.core.errors import Err, Result, err, ok
from app.people.apollo_models import normalize_seniority
from app.people.ports.contact_enrichment import (
    ContactEnrichmentService,
    ContactFacts,
    ContactRevealHints,
    ContactSource,
    contact_row_key,
    record_lookup,
    serves,
    with_unreachable_sources,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.people.treg_client import TregClient

logger = structlog.get_logger()

# ── Real Treg catalog endpoint ids (treg catalog get <id>) ──────────────
#
# pdl      GET  /person/enrich                  $0.38/success (404 free)
# hunter   GET  /combined/find                  $0.0049/success — email in
# hunter   GET  /email-finder                   $0.0245/success — name+org in
#
# Hunter is TWO ids, not one: neither Hunter endpoint accepts every anchor
# shape, so the adapter picks one at call time from the hints it has.
_PDL_PERSON_ENRICH_ENDPOINT_ID: Final[str] = 'pdl.people.enrich'
_HUNTER_COMBINED_FIND_ENDPOINT_ID: Final[str] = 'hunter.x.combined-find'
_HUNTER_EMAIL_FIND_ENDPOINT_ID: Final[str] = 'hunter.people.email.find'

# ── Chain composition, per capability ───────────────────────────────────
#
# Which providers can serve which capability, written down rather than left
# to be inferred from the order of :meth:`_plan` (#1923). A reveal delivers
# a CHANNEL (email or phone); ``company`` is context a row carries, and no
# caller asks this adapter for it on its own today.
#
# LeadMagic's absence from the channel chains is the point of the table.
# ``leadmagic.people.enrich`` (``POST /v1/people/profile-search``) takes
# ONLY a LinkedIn profile URL and returns ONLY profile data: name, title,
# employer, tenure, location, work history. The full catalog example
# carries no email and no phone (checked field by field against
# ``https://treg.superdesign.dev/catalog/examples/leadmagic.people.enrich``),
# and the ``profile_url`` it echoes back is the anchor we passed IN, so
# surfacing it as a revealed LinkedIn would bill the user for data they
# already had. A LeadMagic call can therefore never satisfy
# ``has_contact_channel``: dialling it spent $0.025 on a payload
# :meth:`reveal` was always going to discard as a miss.
_CHAIN_BY_CAPABILITY: Final[dict[str, tuple[str, ...]]] = {
    'email': ('pdl', 'hunter'),
    'phone': ('pdl', 'hunter'),
    'company': ('pdl', 'hunter', 'leadmagic'),
}

# The providers a reveal may dial: the ones that can return a channel.
_CHANNEL_CHAIN: Final[tuple[str, ...]] = _CHAIN_BY_CAPABILITY['email']

# Verification signals these providers publish alongside an email, mapped
# onto the FE chip's 0..1 band. Mirrors `people/provenance.py`'s Apollo
# mapping including its rule that an absent / unknown signal is None ("no
# chip"), never 0.0 — a red chip the user reads as a real low score is
# worse than no chip.
#
# Hunter's email-finder is the only provider in this chain that returns a
# verification status at all (`data.verification.status`). Its vocabulary
# is valid / accept_all / webmail / invalid / disposable / unknown — the
# first two already map here; the rest deliberately fall through to None
# so a "we could not tell" never renders as a confident low score.
_EMAIL_CONFIDENCE_BY_STATUS: Final[dict[str, float]] = {
    'valid': 1.0,
    'verified': 1.0,
    'deliverable': 1.0,
    'probable': 0.7,
    'accept_all': 0.7,
    'catch_all': 0.7,
    'webmail': 0.4,
    'risky': 0.4,
    'guessed': 0.4,
}


class _IdentityUnconfirmed:
    """Sentinel: channel found, no name, on a fuzzy-matched anchored row.

    Returned when ``hints.directory_person_id`` is set (the row is anchored
    to a directory person) but the lookup was not pinned by ``hints.email``
    or ``hints.linkedin_url``, and the sub-provider answered with a contact
    channel but no first or last name.  A pinned lookup matched on a 1:1
    identifier, so the name absence does not cast doubt on the identity.
    """

    __slots__ = ()


def _identity_unconfirmed(
    hints: ContactRevealHints,
    *,
    email: str | None,
    phone: str | None,
    linkedin_url: str | None,
    first_name: str | None,
    last_name: str | None,
) -> bool:
    """True when the identity guard should refuse this record."""
    return bool(
        hints.directory_person_id
        and not hints.email
        and not hints.linkedin_url
        and (email or phone or linkedin_url)
        and not first_name
        and not last_name
    )


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One provider call: which endpoint, how to dial it, how to read it.

    Providers disagree on method, on where the inputs go (query vs JSON
    body) and on response shape, so the chain carries a parser per attempt
    rather than routing everything through one guessy parser.
    """

    provider: str
    endpoint_id: str
    parse: Callable[
        [dict[str, Any], ContactRevealHints],
        ContactFacts | _IdentityUnconfirmed | None,
    ]
    method: str = 'GET'
    params: dict[str, str] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None


class TregContactEnrichment(ContactEnrichmentService):
    """Reveals a contact through Treg's PDL → Hunter chain.

    Returns:
      - ``Ok(ContactFacts)`` — a provider returned a record with at least
        one usable channel (email, LinkedIn URL or phone).
      - ``Err('CONTACTS.TREG_NOT_CONFIGURED')`` — no token.
      - ``Err('CONTACTS.MATCH_INSUFFICIENT_INPUT')`` — the hints carry no
        identifier any provider can anchor on.
      - ``Err('CONTACTS.TREG_OUT_OF_BALANCE', ..., topup_url=...)`` — HTTP
        402 on every provider that could have answered. A 402 on ONE
        provider does not end the chain: Treg refuses per CALL COST
        against the balance, not per balance-is-zero, so the cheap
        providers are still affordable when the expensive one is not.
      - ``Err('CONTACTS.TREG_NO_MATCH')`` — providers answered, nothing
        usable came back. That includes a provider whose "no record" comes
        back as an HTTP 404 rather than an empty body: the client marks
        those ``CONTACTS.TREG_PROVIDER_NO_MATCH`` and they are a miss here,
        never an unreachable source.
      - ``Err('CONTACTS.TREG_IDENTITY_UNCONFIRMED')`` — a provider returned
        a contact channel but no name on an anchored row whose lookup was
        not pinned by ``hints.email`` or ``hints.linkedin_url``.  The
        preview's name would sit above somebody else's address, so the
        record is refused and the chain continues.  Pinned lookups are
        exempt: the sub-provider matched on a 1:1 identifier.
      - ``Err('CONTACTS.TRANSPORT_ERROR', ..., treg_status=...)`` — the
        last transport failure, when every provider failed that way.

    Any ``Err`` returned after at least one provider failed to answer also
    carries ``unreachable_sources`` (the provider names, in call order) on
    its context. That is what lets a caller say "N sources failed" instead
    of "nobody has this contact", two sentences the user must not have to
    tell apart by guessing (#1923).

    Endpoint ids are constructor arguments so a caller (and the tests) can
    override them without editing the module constants.
    """

    def __init__(
        self,
        client: TregClient,
        *,
        pdl_endpoint_id: str = _PDL_PERSON_ENRICH_ENDPOINT_ID,
        hunter_combined_find_endpoint_id: str = _HUNTER_COMBINED_FIND_ENDPOINT_ID,
        hunter_email_find_endpoint_id: str = _HUNTER_EMAIL_FIND_ENDPOINT_ID,
        skip: frozenset[str] = frozenset(),
    ) -> None:
        self._client = client
        self._skip = skip  # providers switched off by the admin kill switch
        self._pdl_endpoint_id = pdl_endpoint_id
        self._hunter_combined_find_endpoint_id = hunter_combined_find_endpoint_id
        self._hunter_email_find_endpoint_id = hunter_email_find_endpoint_id

    async def reveal(self, hints: ContactRevealHints) -> Result[ContactFacts]:
        if not self._client.is_configured:
            return err(
                'CONTACTS.TREG_NOT_CONFIGURED',
                detail='the data provider is not configured',
            )

        attempts = self._plan(hints)
        if not attempts:
            return err(
                'CONTACTS.MATCH_INSUFFICIENT_INPUT',
                detail='contact enrichment needs email or linkedin_url; otherwise '
                'first_name, last_name, and either organization_name or '
                'organization_domain',
            )

        # One key per PROVIDER CALL, each derived from a single key minted
        # for this reveal. A Treg key identifies a REQUEST: sharing one
        # across the chain made the proxy refuse every provider after the
        # first with HTTP 422. See the module docstring.
        reveal_key = str(uuid.uuid4())
        last_err: Err | None = None
        balance_err: Err | None = None
        unreachable: list[str] = []

        for index, attempt in enumerate(attempts):
            outcome = await self._client.call_endpoint(
                attempt.endpoint_id,
                attempt.params,
                method=attempt.method,
                json_body=attempt.json_body,
                idempotency_key=f'{reveal_key}-{index}-{attempt.provider}',
            )
            if isinstance(outcome, Err):
                if outcome.error.code != 'CONTACTS.TREG_OUT_OF_BALANCE':  # refused before dialling
                    await record_lookup(attempt.provider, hints, None)
                if outcome.error.code == 'CONTACTS.TREG_PROVIDER_NO_MATCH':
                    # The provider was reached, has no record, and charged
                    # nothing for saying so. On this transport that answer
                    # arrives as an HTTP 404, which reads like a failure and
                    # is not one: naming it unreachable would sell the user
                    # a retry for a lookup whose answer cannot change, and
                    # bury the honest "nobody has this person" the chain
                    # exists to produce (#1923).
                    logger.info(
                        'treg.contact_enrichment.no_record',
                        provider=attempt.provider,
                        endpoint_id=attempt.endpoint_id,
                    )
                    last_err = err(
                        'CONTACTS.TREG_NO_MATCH',
                        detail='the data provider has no record for the supplied hints',
                        provider=attempt.provider,
                    )
                    continue

                # Every other Err the client returns here is a call that did
                # not reach a verdict: transport, HTTP status, empty
                # balance. "The provider answered and had nothing" is an Ok
                # with no channel, handled below.
                unreachable.append(attempt.provider)
                if outcome.error.code == 'CONTACTS.TREG_OUT_OF_BALANCE':
                    # A 402 prices ONE call against the balance: Treg
                    # answers it with ``balance_micro`` and
                    # ``estimated_cost_micro``, and refuses before dialling
                    # the provider, so the attempt costs nothing. Treating
                    # it as "the balance is empty, stop" let the most
                    # expensive provider in the chain switch off the cheap
                    # ones behind it, which is the #1923 failure again in a
                    # second costume: the user reads a billing state as
                    # "this person has no email". Kept aside rather than
                    # dropped, because if nothing else answers either it is
                    # the verdict on-call can act on (it carries the
                    # top-up URL).
                    balance_err = outcome
                    logger.warning(
                        'treg.contact_enrichment.provider_unaffordable',
                        provider=attempt.provider,
                        endpoint_id=attempt.endpoint_id,
                        balance_micro=outcome.error.context.get('balance_micro'),
                        estimated_cost_micro=outcome.error.context.get(
                            'estimated_cost_micro',
                        ),
                    )
                    continue
                last_err = outcome
                logger.info(
                    'treg.contact_enrichment.provider_err',
                    provider=attempt.provider,
                    endpoint_id=attempt.endpoint_id,
                    code=outcome.error.code,
                )
                continue

            parsed = _safe_parse(attempt, outcome.value, hints)
            await record_lookup(attempt.provider, hints, parsed if isinstance(parsed, ContactFacts) else None)
            if isinstance(parsed, _IdentityUnconfirmed):
                last_err = err(
                    'CONTACTS.TREG_IDENTITY_UNCONFIRMED',
                    detail='The contact database found a record but the '
                    'response carried no name, so the identity could not '
                    'be confirmed. Not charged.',
                    provider=attempt.provider,
                )
                continue
            if parsed is None or not serves(hints, parsed):
                logger.info(
                    'treg.contact_enrichment.no_channel',
                    provider=attempt.provider,
                    endpoint_id=attempt.endpoint_id,
                )
                last_err = err(
                    'CONTACTS.TREG_NO_MATCH',
                    detail='the data provider returned no usable contact channel',
                    provider=attempt.provider,
                )
                continue

            logger.info(
                'treg.contact_enrichment.ok',
                provider=attempt.provider,
                endpoint_id=attempt.endpoint_id,
                has_email=bool(parsed.email),
                has_linkedin=bool(parsed.linkedin_url),
                has_phone=bool(parsed.phone),
            )
            return ok(parsed)

        # An unaffordable provider outranks the rest: "add funds" is the
        # one verdict here that somebody can act on, and it would be lost
        # behind a generic transport error or a no-match.
        exhausted = (
            balance_err
            or last_err
            or err(
                'CONTACTS.TREG_NO_MATCH',
                detail='no contact matched the supplied hints',
            )
        )
        if unreachable:
            logger.warning(
                'treg.contact_enrichment.sources_unreachable',
                providers=tuple(unreachable),
                attempted=len(attempts),
            )
        return with_unreachable_sources(exhausted, unreachable)

    def _plan(self, hints: ContactRevealHints) -> list[_Attempt]:
        """The provider calls worth making for these hints, in order.

        The order is :data:`_CHANNEL_CHAIN`, so the composition of the
        chain is read off the capability declaration instead of the shape
        of this function. Each provider then gates on the anchors ITS
        endpoint actually accepts, not on a single shared predicate:
        Hunter's two endpoints split on email-vs-name. A provider whose
        anchor is missing is dropped from the plan rather than dialled and
        billed for a request it cannot answer.
        """
        builders: dict[str, Callable[[ContactRevealHints], _Attempt | None]] = {
            'pdl': self._pdl_attempt,
            'hunter': self._hunter_attempt,
        }
        attempts: list[_Attempt] = []
        for provider in _CHANNEL_CHAIN:
            if provider in self._skip:
                continue
            build = builders.get(provider)
            if build is None:
                # Declared for the capability but not wired here: a bug in
                # this module, not a runtime condition. Log it and run the
                # rest of the chain rather than break a chat turn.
                logger.warning(
                    'treg.contact_enrichment.provider_not_wired',
                    provider=provider,
                )
                continue
            attempt = build(hints)
            if attempt is not None:
                attempts.append(attempt)
        return attempts

    def _pdl_attempt(self, hints: ContactRevealHints) -> _Attempt | None:
        params = _pdl_params(hints)
        if not params:
            return None
        return _Attempt(
            provider='pdl',
            endpoint_id=self._pdl_endpoint_id,
            params=params,
            parse=_pdl_facts,
        )

    def _hunter_attempt(self, hints: ContactRevealHints) -> _Attempt | None:
        """Pick ONE of Hunter's two endpoints from the anchors available.

        ``/combined/find`` takes an email and nothing else; ``/email-finder``
        takes a name plus an organisation (or a bare LinkedIn handle) and
        finds the email. They are different products at different prices,
        so this is a branch, not a merged parameter builder — and when
        neither anchor shape is present, Hunter is skipped entirely.
        """
        if hints.email:
            return _Attempt(
                provider='hunter',
                endpoint_id=self._hunter_combined_find_endpoint_id,
                params={'email': hints.email},
                parse=_hunter_combined_facts,
            )

        params = _hunter_email_find_params(hints)
        if params:
            return _Attempt(
                provider='hunter',
                endpoint_id=self._hunter_email_find_endpoint_id,
                params=params,
                parse=_hunter_email_find_facts,
            )
        return None


def _safe_parse(
    attempt: _Attempt,
    payload: dict[str, Any],
    hints: ContactRevealHints,
) -> ContactFacts | _IdentityUnconfirmed | None:
    """Run a provider parser without letting it escape as an exception.

    The parsers only ever probe dict keys, so this should never fire — but
    "a provider changed shape" must degrade to a miss, and this adapter's
    contract with the composite is that it never raises.
    """
    try:
        return attempt.parse(payload, hints)
    except Exception as exc:  # port contract guard
        logger.warning(
            'treg.contact_enrichment.parse_exc',
            provider=attempt.provider,
            endpoint_id=attempt.endpoint_id,
            error_type=type(exc).__name__,
            error_message=type(exc).__name__,
        )
        return None


# ── Request building ────────────────────────────────────────────────────
#
# Treg passes method, query and body through unchanged, so everything
# below is the PROVIDER's own vocabulary, taken from `treg catalog get`.


def _pdl_params(hints: ContactRevealHints) -> dict[str, str]:
    """``GET /person/enrich`` query params.

    PDL's minimum-input rule, verbatim from the catalog: "profile OR email
    OR phone OR email_hash OR lid OR ((first_name AND last_name, or name)
    AND (locality OR region OR company OR school OR location OR
    postal_code))".

    ``company`` is ONE param that takes an employer name **or** a domain —
    not two separate params. Domain is preferred when both are known,
    because it disambiguates far better than a name.
    """
    params: dict[str, str] = {}
    if hints.email:
        params['email'] = hints.email
    if hints.linkedin_url:
        params['profile'] = hints.linkedin_url
    if hints.first_name:
        params['first_name'] = hints.first_name
    if hints.last_name:
        params['last_name'] = hints.last_name
    company = hints.organization_domain or hints.organization_name
    if company:
        params['company'] = company

    satisfies_minimum = bool(
        hints.email or hints.linkedin_url or (hints.first_name and hints.last_name and company),
    )
    return params if satisfies_minimum else {}


def _hunter_email_find_params(hints: ContactRevealHints) -> dict[str, str]:
    """``GET /email-finder`` query params.

    Catalog rule: "one of domain | company | linkedin_handle, plus either
    full_name or first_name + last_name (or linkedin_handle alone)".
    ``domain`` is preferred over ``company`` when both are known.
    """
    params: dict[str, str] = {}
    if hints.organization_domain:
        params['domain'] = hints.organization_domain
    elif hints.organization_name:
        params['company'] = hints.organization_name
    if hints.first_name:
        params['first_name'] = hints.first_name
    if hints.last_name:
        params['last_name'] = hints.last_name

    handle = _linkedin_handle(hints.linkedin_url)
    if handle:
        params['linkedin_handle'] = handle

    named = bool(hints.first_name and hints.last_name)
    organised = bool(hints.organization_domain or hints.organization_name)
    if (named and organised) or handle:
        return params
    return {}


def _linkedin_handle(linkedin_url: str | None) -> str | None:
    """``https://www.linkedin.com/in/satyanadella/`` → ``satyanadella``.

    Hunter's ``linkedin_handle`` is the bare slug, not the URL.
    """
    if not linkedin_url:
        return None
    slug = linkedin_url.strip().rstrip('/').rsplit('/', 1)[-1]
    return slug or None


# ── Response parsing ────────────────────────────────────────────────────
#
# One parser per provider, written against that provider's real response.
# Where a shape is not fully confirmed the parser probes several spellings
# and logs a warning on a shape it does not recognise, so a silent field
# rename degrades to a miss instead of a crash.


def _pdl_facts(
    payload: dict[str, Any],
    hints: ContactRevealHints,
) -> ContactFacts | _IdentityUnconfirmed | None:
    """Parse ``GET /person/enrich``.

    Envelope confirmed live: ``{"status": 200, "likelihood": 1-10,
    "data": {...}}``.

    **The booleans trap.** PDL's published example renders every PII field
    as a bare ``true``/``false`` presence flag (``"work_email": false``,
    ``"emails": true``, ``"mobile_phone": true``) — that is how PDL redacts
    samples, and the live response carries real strings/lists in those same
    keys. Reading one of those flags into ``email`` would put the literal
    string ``"True"`` in front of a user, so every accessor below accepts
    ONLY ``str`` (and, for the list fields, only an actual ``list``);
    a bool simply does not match and falls through to the next candidate.

    ``personal_emails`` is deliberately NOT read: this is a B2B work-email
    tool, and a personal address is a different consent question.
    """
    data = payload.get('data')
    if not isinstance(data, dict):
        logger.warning(
            'treg.contact_enrichment.unexpected_payload',
            provider='pdl',
            keys=sorted(payload.keys())[:10],
        )
        return None

    email = _first_str(data, 'work_email') or _first_from_list(
        data,
        'emails',
        keys=('address', 'email'),
    )
    phone = _first_str(data, 'mobile_phone') or _first_from_list(
        data,
        'phone_numbers',
        keys=('number', 'phone'),
    )
    # PDL returns a scheme-less profile URL ("linkedin.com/in/seanthorne").
    linkedin_url = _absolute_url(_first_str(data, 'linkedin_url'))
    first_name = _first_str(data, 'first_name')
    last_name = _first_str(data, 'last_name')

    if not any((email, phone, linkedin_url, first_name, last_name)):
        logger.warning(
            'treg.contact_enrichment.missing_expected_fields',
            provider='pdl',
            record_keys=sorted(data.keys())[:15],
        )
        return None

    if _identity_unconfirmed(
        hints,
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        first_name=first_name,
        last_name=last_name,
    ):
        logger.warning(
            'treg.contact_enrichment.identity_unconfirmed',
            provider='pdl',
        )
        return _IdentityUnconfirmed()

    return ContactFacts(
        id=contact_row_key(hints, _first_str(data, 'id'), email, linkedin_url),
        source=ContactSource.treg,
        first_name=first_name or hints.first_name,
        last_name=last_name or hints.last_name,
        title=_first_str(data, 'job_title'),
        # job_title_levels is a list like ["cxo"]; _first_str takes its head.
        seniority=normalize_seniority(
            _first_str(data, 'job_title_levels'),
            source='provider:pdl',
        ),
        email=email,
        # PDL publishes no deliverability status. `likelihood` is a MATCH
        # confidence, not an email one — mapping it onto the email chip
        # would mislabel "we are sure this is the right person" as "we are
        # sure this address works".
        email_status=None,
        email_confidence=None,
        linkedin_url=linkedin_url,
        organization=_first_str(data, 'job_company_name') or hints.organization_name,
        organization_domain=_first_str(data, 'job_company_website') or hints.organization_domain,
        country=_first_str(data, 'location_country', 'job_company_location_country'),
        phone=phone,
        phone_status='revealed' if phone else None,
    )


def _hunter_combined_facts(
    payload: dict[str, Any],
    hints: ContactRevealHints,
) -> ContactFacts | _IdentityUnconfirmed | None:
    """Parse ``GET /combined/find``.

    Shape confirmed by a live call on 2026-08-12 (cost $0.0049). It is
    Clearbit-format and NESTED — nothing is flat::

        data.person.name       {fullName, givenName, familyName}
        data.person.email      str
        data.person.employment {domain, name, title, role, subRole, seniority}
        data.person.linkedin   {handle: "in/steli"}   ← handle, not a URL
        data.person.geo        {country, countryCode, ...}
        data.person.phone      str | null
        data.company           {name, domain, foundedYear, ...}

    No verification status is returned on this endpoint, so no email chip.
    """
    data = payload.get('data')
    person = data.get('person') if isinstance(data, dict) else None
    if not isinstance(person, dict):
        logger.warning(
            'treg.contact_enrichment.unexpected_payload',
            provider='hunter',
            keys=sorted(payload.keys())[:10],
        )
        return None

    company = data.get('company') if isinstance(data, dict) else None
    name = _dict_at(person, 'name')
    employment = _dict_at(person, 'employment')
    geo = _dict_at(person, 'geo')

    email = _first_str(person, 'email')
    phone = _first_str(person, 'phone')
    linkedin_url = _linkedin_url_from_handle(
        _first_str(_dict_at(person, 'linkedin'), 'handle'),
    )
    first_name = _first_str(name, 'givenName')
    last_name = _first_str(name, 'familyName')

    if not any((email, phone, linkedin_url, first_name, last_name)):
        logger.warning(
            'treg.contact_enrichment.missing_expected_fields',
            provider='hunter',
            record_keys=sorted(person.keys())[:15],
        )
        return None

    if _identity_unconfirmed(
        hints,
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        first_name=first_name,
        last_name=last_name,
    ):
        logger.warning(
            'treg.contact_enrichment.identity_unconfirmed',
            provider='hunter',
        )
        return _IdentityUnconfirmed()

    return ContactFacts(
        id=contact_row_key(hints, _first_str(person, 'id'), email, linkedin_url),
        source=ContactSource.treg,
        first_name=first_name or hints.first_name,
        last_name=last_name or hints.last_name,
        title=_first_str(employment, 'title'),
        seniority=normalize_seniority(
            _first_str(employment, 'seniority'),
            source='provider:hunter',
        ),
        email=email,
        email_status=None,
        email_confidence=None,
        linkedin_url=linkedin_url,
        organization=_first_str(employment, 'name') or _first_str(company, 'name') or hints.organization_name,
        organization_domain=_first_str(employment, 'domain')
        or _first_str(company, 'domain')
        or hints.organization_domain,
        country=_first_str(geo, 'countryCode', 'country'),
        phone=phone,
        phone_status='revealed' if phone else None,
    )


def _hunter_email_find_facts(
    payload: dict[str, Any],
    hints: ContactRevealHints,
) -> ContactFacts | _IdentityUnconfirmed | None:
    """Parse ``GET /email-finder``.

    Shape confirmed from the catalog example — flat under ``data``::

        {"data": {"first_name", "last_name", "email", "score", "domain",
                  "accept_all", "position", "linkedin_url", "phone_number",
                  "company", "verification": {"date", "status"}}}

    ``verification.status`` is the one deliverability signal in this whole
    chain; it feeds ``email_status`` and, through
    :data:`_EMAIL_CONFIDENCE_BY_STATUS`, the FE chip.
    """
    data = payload.get('data')
    if not isinstance(data, dict):
        logger.warning(
            'treg.contact_enrichment.unexpected_payload',
            provider='hunter',
            keys=sorted(payload.keys())[:10],
        )
        return None

    email = _first_str(data, 'email')
    phone = _first_str(data, 'phone_number')
    linkedin_url = _absolute_url(_first_str(data, 'linkedin_url'))
    first_name = _first_str(data, 'first_name')
    last_name = _first_str(data, 'last_name')

    if not any((email, phone, linkedin_url, first_name, last_name)):
        logger.warning(
            'treg.contact_enrichment.missing_expected_fields',
            provider='hunter',
            record_keys=sorted(data.keys())[:15],
        )
        return None

    if _identity_unconfirmed(
        hints,
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        first_name=first_name,
        last_name=last_name,
    ):
        logger.warning(
            'treg.contact_enrichment.identity_unconfirmed',
            provider='hunter',
        )
        return _IdentityUnconfirmed()

    email_status = _first_str(_dict_at(data, 'verification'), 'status')
    return ContactFacts(
        id=contact_row_key(hints, None, email, linkedin_url),
        source=ContactSource.treg,
        first_name=first_name or hints.first_name,
        last_name=last_name or hints.last_name,
        title=_first_str(data, 'position'),
        seniority=None,
        email=email,
        email_status=email_status,
        email_confidence=_email_confidence(email_status),
        linkedin_url=linkedin_url,
        organization=_first_str(data, 'company') or hints.organization_name,
        organization_domain=_first_str(data, 'domain') or hints.organization_domain,
        country=None,
        phone=phone,
        phone_status='revealed' if phone else None,
    )


# ── Shared field accessors ──────────────────────────────────────────────


def _dict_at(record: dict[str, Any] | None, *keys: str) -> dict[str, Any] | None:
    """First key holding a nested object."""
    if not record:
        return None
    for key in keys:
        nested = record.get(key)
        if isinstance(nested, dict):
            return nested
    return None


def _first_str(record: dict[str, Any] | None, *keys: str) -> str | None:
    """First key present with a non-empty string value, trimmed.

    Accepts ONLY ``str`` (or a list whose head is a ``str``). A ``bool`` is
    never a match — PDL's samples use bare booleans as PII presence flags
    and ``str(True)`` in an email column would be a user-visible lie.
    """
    if not record:
        return None
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value and isinstance(value[0], str) and value[0].strip():
            return value[0].strip()
    return None


def _first_from_list(
    record: dict[str, Any],
    *list_keys: str,
    keys: tuple[str, ...],
) -> str | None:
    """First usable value out of a list-of-objects field (``emails[]``…).

    A non-list value under the key (PDL's ``"emails": true`` presence flag)
    is skipped, not coerced.
    """
    for list_key in list_keys:
        entries = record.get(list_key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
            if isinstance(entry, dict):
                found = _first_str(entry, *keys)
                if found:
                    return found
    return None


def _absolute_url(url: str | None) -> str | None:
    """Prefix a scheme-less profile URL (PDL's ``linkedin.com/in/x``)."""
    if not url:
        return None
    if url.startswith(('http://', 'https://')):
        return url
    return f'https://{url}'


def _linkedin_url_from_handle(handle: str | None) -> str | None:
    """Clearbit-format handle → a real URL.

    Hunter's ``combined/find`` returns ``{"linkedin": {"handle": "in/steli"}}``
    — the handle already carries the ``in/`` segment, but tolerate a bare
    slug too.
    """
    if not handle:
        return None
    slug = handle.strip().strip('/')
    if not slug:
        return None
    if not slug.startswith('in/'):
        slug = f'in/{slug}'
    return f'https://www.linkedin.com/{slug}'


def _email_confidence(email_status: str | None) -> float | None:
    """Provider verification status → the FE chip's 0..1 band.

    ``None`` for an absent or unrecognised status: no chip beats a chip the
    user would misread. Same rule as ``apollo_confidence``.
    """
    if not isinstance(email_status, str):
        return None
    return _EMAIL_CONFIDENCE_BY_STATUS.get(email_status.strip().lower())
