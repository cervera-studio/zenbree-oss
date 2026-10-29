"""Apollo.io HTTP client — narrow surface, trimmed output.

Two methods power #473:

- :meth:`ApolloClient.search_people` → POST /v1/mixed_people/api_search
  Free of Apollo credits. Returns preview records (obfuscated last name,
  ``has_email``/``has_direct_phone`` booleans) so the LLM can pick which
  rows are worth revealing before we spend credits.
- :meth:`ApolloClient.match_person` → POST /v1/people/match
  1 Apollo credit per call. Returns the full enriched record including
  verified email and phone.

The raw responses are large (~30 KB per match) and contain a lot of
nested noise — organisation metadata, languages and sic_codes. We trim
aggressively before returning so the LLM's tool-result payload stays well
under the 8 KB cache-friendly threshold (see
``feedback_listing_tool_llm_truncation``), while retaining the person and
employment fields a user can select in the results table.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.billing.paid_call import paid_call
from app.core.config import settings
from app.core.errors import Ok, Result, err
from app.people.apollo_decode import (
    _decode_company_discovery,
    _decode_job_postings,
    _decode_match,
    _decode_organization_enrich,
    _decode_organization_search,
    _decode_search,
    _normalise_domain,
)
from app.people.apollo_models import (
    DISCOVERY_PAGE_ROWS,
    DISCOVERY_SORT_FIELDS,
    MAX_PROVIDER_PER_PAGE,
    CompanyDiscoveryResult,
    JobPostingsResult,
    OrganizationEnriched,
    OrganizationSearchResult,
    PersonEnriched,
    SearchResult,
)
from app.people.apollo_phone import resolve_phone
from app.people.geo_city import expand_macro_regions

logger = structlog.get_logger()

# Apollo's published timeouts: median ~300 ms, p99 ~3 s on search;
# match is ~1 s p99. 15 s leaves slack for the slow tail without
# parking a chat turn for too long.
_TIMEOUT_SECONDS = 15.0

# Phone reveal is asynchronous on Apollo's side. /people/match with
# reveal_phone_number returns the email synchronously plus a pending phone job;
# the number itself arrives out of band. Apollo requires a webhook_url to
# accept the reveal, but we never serve a callback. We poll /webhook_result
# with the top-level integer request_id instead. This placeholder satisfies
# the required param and is intentionally never served.
_PHONE_REVEAL_WEBHOOK_URL = 'https://zenbree.invalid/apollo/phone-webhook'


# ── Client ──────────────────────────────────────────────────────────────────


class ApolloClient:
    """HTTPX-backed Apollo wrapper. Stateless except for the api key + base URL."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.apollo_api_key
        self._base_url = (base_url or settings.apollo_base_url).rstrip('/')
        self._timeout = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
            'X-Api-Key': self._api_key,
        }

    async def search_people(
        self,
        *,
        organization_name: str | None = None,
        organization_domains: list[str] | None = None,
        person_titles: list[str] | None = None,
        person_seniorities: list[str] | None = None,
        person_functions: list[str] | None = None,
        person_locations: list[str] | None = None,
        organization_technologies: list[str] | None = None,
        organization_technologies_any: list[str] | None = None,
        organization_technologies_excluded: list[str] | None = None,
        employee_ranges: list[str] | None = None,
        industry_tag_ids: list[str] | None = None,
        keyword_tags: list[str] | None = None,
        organization_locations: list[str] | None = None,
        organization_ids: list[str] | None = None,
        q_keywords: str | None = None,
        contact_email_status: list[str] | None = None,
        include_similar_titles: bool | None = None,
        organization_job_titles: list[str] | None = None,
        organization_job_locations: list[str] | None = None,
        organization_num_jobs_min: int | None = None,
        organization_num_jobs_max: int | None = None,
        organization_job_posted_at_min: str | None = None,
        organization_job_posted_at_max: str | None = None,
        revenue_min: int | None = None,
        revenue_max: int | None = None,
        page: int = 1,
        per_page: int = 10,
    ) -> Result[SearchResult]:
        """Search Apollo for people matching the filters.

        Returns previews (no email reveal). Apollo charges 0 credits for
        search. We cap ``per_page`` at 25 here even though Apollo allows
        up to 100 — a 100-row payload is too noisy for the LLM and
        burns prompt-cache locality on payloads no human will read.

        ``organization_technologies``, ``employee_ranges``,
        ``industry_tag_ids``, ``keyword_tags`` and
        ``revenue_min``/``revenue_max`` are the employer-level filters
        (tech stack, headcount bracket, SECTOR, revenue bracket) — all
        free on this endpoint, same as the rest of the search body. See
        :meth:`_build_search_body` for the exact wire keys, in
        particular the NESTED ``revenue_range`` object (mirrors the fix
        applied to :meth:`discover_organizations` — a flat
        ``revenue_range[min]`` key is silently ignored in a JSON body).
        Live staging proof on 2026-09-24 (#3417): with the AI Ark flag on,
        ``revenue_min=10^12`` reached this endpoint and returned zero rows;
        ``10^13`` is outside Apollo's accepted range and returns HTTP 422.

        The two SECTOR filters were live-probed on THIS endpoint on
        2026-08-05 (#1661), because a key this endpoint does not know is
        dropped in silence rather than refused — the same failure mode
        that already cost ``q_organization_domains`` (missing ``_list``)
        and the flat ``revenue_range[min]``. "Founder/CEO in Poland"
        returns 27,068; with ``keyword_tags=['executive search']`` it
        returns 181, with the validated software ``industry_tag_ids``
        2,014, and with a syntactically valid but non-existent tag id
        **0**. That zero is the control: a filter Apollo had ignored
        could not produce it. Repeat the check before trusting either
        key with a value it has not been probed with.

        ``contact_email_status`` is the only filter on this method that
        saves MONEY rather than tokens, and it is the one gap a live
        staging session priced exactly. Search is free; a reveal is not.
        A ``reveal_all_pending`` batch spent 50 credits on 10 people and
        two of them came back with no address at all
        (``email_status: 'unavailable'``) — a fifth of the spend on rows
        Apollo already knew it could not deliver, and would have
        withheld from the search for free had anyone been able to ask.
        Nobody could: until now the model had no way to say "only
        people with a verified address". The accepted values are
        Apollo's own and one of them contains spaces; see
        :data:`APOLLO_CONTACT_EMAIL_STATUSES`, which also explains why
        this enum is deliberately not the one
        ``app.people.provenance`` maps.

        ``include_similar_titles`` is the only TRI-STATE argument here.
        Apollo widens ``person_titles`` to titles it scores as similar
        **by default**, so ``None`` (omit the key) and ``False`` (send
        it) are different requests, and ``False`` must reach the wire —
        it is the whole point of the parameter. The live symptom: a
        "Senior Product Manager" search surfacing "Senior Product
        Producer, Creative Apps", a neighbour by Apollo's scoring and
        not by any recruiter's.

        ``organization_locations`` filters on the EMPLOYER's location,
        where ``person_locations`` filters on the person's. The two
        answer different questions and the gap was being papered over
        with the wrong one: "B2C companies in San Francisco" had to be
        asked as ``person_locations=['San Francisco']``, which finds
        people who live there — including remote staff of employers
        headquartered anywhere else — and misses the SF employer's
        people who do not. Wire key reused verbatim from
        :meth:`discover_organizations`, where Apollo already honours it.

        ``organization_ids`` takes Apollo's own employer primary keys
        (the ``organization_id`` on a ``discover_companies`` /
        :meth:`search_organizations` row). Same exactness as a domain
        list, one resolution step shorter.

        ``q_keywords`` is Apollo's generic free-text filter and is a
        **string**, not a list — the one argument here whose spec type
        differs from its neighbours'. It is not
        ``q_organization_keyword_tags`` (that is ``keyword_tags``
        above, and it matches the employer's own sector wording); what
        this one matches against, the docs do not say beyond "the
        results", and this method does not guess on Apollo's behalf.

        The hiring-signal group —
        ``organization_job_titles``/``organization_job_locations`` and
        the two nested ranges ``organization_num_jobs_*`` /
        ``organization_job_posted_at_*`` — filters people by what their
        EMPLOYER is currently advertising. Two of those keys are the
        ones :meth:`discover_organizations` live-verified on the
        companies endpoint on 2026-07-14, including the nested-object
        shape of ``organization_num_jobs_range``; that is evidence about
        ``/mixed_companies/search`` and is treated here as a working
        hypothesis, not a result. Both nested ranges, and every key in
        this paragraph, are probed by
        ``tests/contract/test_apollo_people_unwired_filters_live.py``,
        which is written around the same zero-row control as the sector
        probe above. Run it before quoting any of them as verified.

        Domain filtering works and is authoritative: the employer is
        pinned server-side, so a domain-filtered search cannot return a
        homonym. #585 read the zero-row symptom as Basic-plan gating and
        added a retry that dropped the domain and searched by a name
        inferred from the domain root. The real cause was the request
        shape (see :meth:`_build_search_body`), and with that fixed the
        retry only ever fired on a genuine no-match, silently answering
        with a different company's people. It is gone: zero rows for a
        domain now means zero, and callers who want a name search must
        ask for one explicitly via ``organization_name``.
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        body = self._build_search_body(
            page=page,
            per_page=per_page,
            organization_name=organization_name,
            organization_domains=organization_domains,
            person_titles=person_titles,
            person_seniorities=person_seniorities,
            person_functions=person_functions,
            person_locations=person_locations,
            organization_technologies=organization_technologies,
            organization_technologies_any=organization_technologies_any,
            organization_technologies_excluded=organization_technologies_excluded,
            employee_ranges=employee_ranges,
            industry_tag_ids=industry_tag_ids,
            keyword_tags=keyword_tags,
            organization_locations=organization_locations,
            organization_ids=organization_ids,
            q_keywords=q_keywords,
            contact_email_status=contact_email_status,
            include_similar_titles=include_similar_titles,
            organization_job_titles=organization_job_titles,
            organization_job_locations=organization_job_locations,
            organization_num_jobs_min=organization_num_jobs_min,
            organization_num_jobs_max=organization_num_jobs_max,
            organization_job_posted_at_min=organization_job_posted_at_min,
            organization_job_posted_at_max=organization_job_posted_at_max,
            revenue_min=revenue_min,
            revenue_max=revenue_max,
        )
        return await paid_call('apollo.search_people', lambda: self._post_search(body))

    def _build_search_body(
        self,
        *,
        page: int,
        per_page: int,
        organization_name: str | None,
        organization_domains: list[str] | None,
        person_titles: list[str] | None,
        person_seniorities: list[str] | None,
        person_functions: list[str] | None,
        person_locations: list[str] | None,
        organization_technologies: list[str] | None = None,
        organization_technologies_any: list[str] | None = None,
        organization_technologies_excluded: list[str] | None = None,
        employee_ranges: list[str] | None = None,
        industry_tag_ids: list[str] | None = None,
        keyword_tags: list[str] | None = None,
        organization_locations: list[str] | None = None,
        organization_ids: list[str] | None = None,
        q_keywords: str | None = None,
        contact_email_status: list[str] | None = None,
        include_similar_titles: bool | None = None,
        organization_job_titles: list[str] | None = None,
        organization_job_locations: list[str] | None = None,
        organization_num_jobs_min: int | None = None,
        organization_num_jobs_max: int | None = None,
        organization_job_posted_at_min: str | None = None,
        organization_job_posted_at_max: str | None = None,
        revenue_min: int | None = None,
        revenue_max: int | None = None,
    ) -> dict[str, Any]:
        # ── How to read the comments below ──────────────────────────────────
        #
        # This method mixes filters with two very different pedigrees, and the
        # difference is the most important thing in the file.
        #
        # MEASURED: the comment carries live figures and a date (the domain
        # _list key, the sector pair, revenue_range). Someone called the real
        # endpoint and watched the count move.
        #
        # UNCONFIRMED (#2167): the wire key and its shape come from Apollo's
        # published OpenAPI spec for /mixed_people/api_search and have NOT been
        # confirmed against the live endpoint — no Apollo credential was
        # available when they were wired, and the user accepted shipping them
        # unverified. Every one of them is tagged inline. They are a
        # reasonable reading of the spec, not a result.
        #
        # This distinction matters here more than it would anywhere else,
        # because Apollo does not refuse a key it does not know: it DROPS it
        # and answers cheerfully with an unfiltered pool. A filter that never
        # filtered is indistinguishable from a working one at every level
        # except a count. Two incidents in this file's history are exactly
        # that (see q_organization_domains_list and revenue_range below).
        #
        # The probes are written and committed:
        # tests/contract/test_apollo_people_unwired_filters_live.py. Run them
        # the day a key exists, then move the tags from UNCONFIRMED to a
        # measured figure. Until then, an UNCONFIRMED filter returning
        # suspiciously many rows is the first thing to suspect.
        body: dict[str, Any] = {
            'page': max(1, page),
            'per_page': min(MAX_PROVIDER_PER_PAGE, max(1, per_page)),
        }
        if organization_name:
            body['q_organization_name'] = organization_name
        if organization_domains:
            # MUST be the _list key: a JSON array under the bare
            # q_organization_domains is silently ignored and returns 0 rows
            # (live probe 2026-07-20, stripe.com: 0 vs 10815, #1349).
            body['q_organization_domains_list'] = list(organization_domains)
        if person_titles:
            body['person_titles'] = person_titles
        if person_seniorities:
            body['person_seniorities'] = person_seniorities
        if person_functions:
            body['person_functions'] = person_functions
        if person_locations:
            body['person_locations'] = expand_macro_regions(person_locations)
        # Whether Apollo may widen person_titles to titles it scores as
        # SIMILAR. The one tri-state key in this body, and the one place the
        # `if value:` idiom every other filter uses would be a bug: Apollo
        # widens BY DEFAULT, so `False` is a request ("strict matches only")
        # and absence is a different request ("use your default, which is
        # wide"). Collapsing False into absence the way an empty list is
        # collapsed would delete the only thing this parameter exists to say,
        # and would do it silently — the search still returns rows, just the
        # loose ones the caller asked to be rid of. Hence `is not None`.
        #
        # The symptom that motivated wiring it: a "Senior Product Manager"
        # search returning "Senior Product Producer, Creative Apps", which is
        # a neighbour by Apollo's scoring and by no recruiter's.
        #
        # UNCONFIRMED: key and boolean type from the OpenAPI spec; the claim
        # that Apollo's default is to widen is Apollo's own documented
        # behaviour, not something measured here. If false stops narrowing,
        # the probe's strict-vs-wide assertion is what catches it.
        if include_similar_titles is not None:
            body['include_similar_titles'] = include_similar_titles
        # The filter that decides what a later reveal costs: it drops people
        # whose address Apollo already knows it cannot hand over, during the
        # FREE search, before a paid reveal exists to be wasted on them. Live
        # staging batch: reveal_all_pending, 50 credits for 10 people, 2 of
        # them with email_status 'unavailable' — a fifth of the spend on rows
        # this key would have withheld for nothing.
        #
        # It saves money only when a reveal is what comes next, and that is
        # the caveat the tool descriptions carry: the SEARCH costs 0 at Apollo,
        # so narrowing here buys nothing on a pool the user only wants to LOOK
        # at, and ['verified'] alone withholds the 12,641 'guessed' rows that
        # do have an address. Measured on the same Bergamo pool as the enum
        # above: 27 matches → 12 with ['verified'], → 20 with both.
        #
        # MEASURED 2026-09-04, member by member: see
        # APOLLO_CONTACT_EMAIL_STATUSES, which carries the counts, the two
        # spellings that shipped inert for two months, and why this is still
        # not the enum app.people.provenance maps.
        if contact_email_status:
            body['contact_email_status'] = list(contact_email_status)
        # Apollo's generic free-text filter. A STRING, not an array — the one
        # key in this body whose spec type differs from its neighbours', so
        # the shape mistake to guard against here is passing a list. NOT
        # q_organization_keyword_tags (that is `keyword_tags` below, which
        # matches the employer's own sector wording).
        #
        # UNCONFIRMED: key and the string (not array) type from the OpenAPI
        # spec. What it actually matches against is undocumented, so the probe
        # can only show that a nonsense value empties the pool — that proves
        # the key is READ, not what it reads.
        if q_keywords:
            body['q_keywords'] = q_keywords
        if organization_technologies:
            body['currently_using_all_of_technology_uids'] = list(organization_technologies)
        # The other two arms of the technology filter. The key above is
        # ALL-of: every uid must be present, so a three-item list is a
        # near-empty result set and callers reach for it by accident. ANY-of
        # is what an ICP usually means ("Shopify or WooCommerce or Magento"),
        # and the exclusion arm is how "not already on our competitor" is
        # stated. Same uid vocabulary across all three.
        #
        # UNCONFIRMED: both keys from the OpenAPI spec. The ALL-of key beside
        # them is the shipped one, so the naming pattern is at least real —
        # which is exactly the kind of partial evidence that made
        # q_organization_domains look safe before it turned out to need _list.
        if organization_technologies_any:
            body['currently_using_any_of_technology_uids'] = list(organization_technologies_any)
        if organization_technologies_excluded:
            body['currently_not_using_any_of_technology_uids'] = list(
                organization_technologies_excluded,
            )
        if employee_ranges:
            body['organization_num_employees_ranges'] = list(employee_ranges)
        # SECTOR, the two keys the company endpoint already uses, live-probed
        # on THIS one before shipping (see :meth:`search_people`). Without
        # them an ICP stated as a sector was not expressible here at all, so
        # the model fell back to one paid find_decision_maker per company —
        # the anti-pattern its own guardrail forbids and the 60-call, 33-empty
        # loop of #1661. Same precedence as on the company side: the taxonomy
        # is precise, the keyword is loose and covers the sectors the taxonomy
        # has no validated id for.
        if industry_tag_ids:
            body['organization_industry_tag_ids'] = list(industry_tag_ids)
        if keyword_tags:
            body['q_organization_keyword_tags'] = list(keyword_tags)
        # WHERE THE EMPLOYER IS, as opposed to person_locations above, which is
        # where the PERSON is. Same wire key discover_organizations already
        # sends and Apollo already honours on the companies endpoint, reused
        # verbatim rather than re-derived. Without it the only way to say
        # "companies in San Francisco" was person_locations=['San Francisco'],
        # which answers a different question in both directions: it catches
        # remote staff of employers headquartered anywhere else, and misses the
        # SF employer's people who live outside the metro.
        #
        # UNCONFIRMED ON THIS ENDPOINT. The key is measured — on the COMPANIES
        # endpoint, where discover_organizations and search_organizations both
        # send it and Apollo honours it. #1661 is the precedent for why that
        # does not transfer: the sector keys behaved on one endpoint and still
        # had to be proved on the other.
        if organization_locations:
            body['organization_locations'] = expand_macro_regions(organization_locations)
        # Apollo's own employer primary keys, off a discover_companies /
        # search_organizations row's organization_id. Exactly as unambiguous as
        # q_organization_domains_list and one resolution step shorter — an id
        # IS the employer, where a domain is a string Apollo still has to
        # resolve to one, and survives a company whose primary domain moved.
        #
        # UNCONFIRMED: key and array shape from the OpenAPI spec.
        if organization_ids:
            body['organization_ids'] = list(organization_ids)
        # ── Hiring signal: what the EMPLOYER is currently advertising ────────
        # q_organization_job_titles and the nested organization_num_jobs_range
        # are the same two keys discover_organizations sends, and both were
        # live-verified THERE on 2026-07-14 (Milan 56,557 → 402 with min 10
        # jobs, → 76 with job title "software engineer"). That is evidence
        # about /mixed_companies/search and is carried here as a hypothesis,
        # not a result: this file's history is a list of keys that were in the
        # docs, looked right, and silently did nothing on the endpoint they
        # were sent to (q_organization_domains without _list; flat
        # revenue_range[min]). The discriminating probe for all four keys is
        # tests/contract/test_apollo_people_unwired_filters_live.py — run it
        # before quoting any of them as verified on THIS endpoint.
        if organization_job_titles:
            body['q_organization_job_titles'] = list(organization_job_titles)
        if organization_job_locations:
            body['organization_job_locations'] = list(organization_job_locations)
        # ⚠ THE TWO HIGHEST-RISK GUESSES IN THIS METHOD. If a filter here ever
        # silently returns unfiltered results, LOOK HERE FIRST.
        #
        # Apollo's docs spell both of these as BRACKETED query params —
        # 'organization_num_jobs_range[min]',
        # 'organization_job_posted_at_range[min]'. That spelling has been
        # silently ignored in a JSON body twice in this file's history:
        # q_organization_domains (needed the _list suffix; 0 rows vs 10,815,
        # #1349) and flat revenue_range[min] (filter simply did not filter).
        # Both times the working shape turned out to be what is written here.
        #
        # So this is a GUESS, made the way the two previous fixes went, not a
        # measurement: we send nested objects. discover_organizations DID
        # live-confirm the nested form for organization_num_jobs_range — on
        # /mixed_companies/search (2026-07-14, the bracket form stayed pinned
        # at the 56,557 baseline while nested moved it to 402). That is a
        # different endpoint, and #1661 is the standing precedent that a key
        # proven on one is not proven on the other.
        #
        # organization_job_posted_at_range has NO sibling evidence at all —
        # nothing in this repo has ever sent it anywhere. It is the single
        # least-supported line in this method.
        #
        # The failure mode to watch for: a hiring filter that returns the same
        # count with and without it. The probe asserts exactly that
        # (organization_num_jobs_min=1_000_000 and a 2099 date must both
        # return 0); until it runs, neither range is proven.
        num_jobs_range: dict[str, int] = {}
        if organization_num_jobs_min is not None:
            num_jobs_range['min'] = organization_num_jobs_min
        if organization_num_jobs_max is not None:
            num_jobs_range['max'] = organization_num_jobs_max
        if num_jobs_range:
            body['organization_num_jobs_range'] = num_jobs_range
        # Dates stay strings: the spec types these 'date' (ISO 'YYYY-MM-DD'),
        # and parsing them here would only add a second place for the format
        # to be wrong without adding a check Apollo does not already make.
        job_posted_at_range: dict[str, str] = {}
        if organization_job_posted_at_min:
            job_posted_at_range['min'] = organization_job_posted_at_min
        if organization_job_posted_at_max:
            job_posted_at_range['max'] = organization_job_posted_at_max
        if job_posted_at_range:
            body['organization_job_posted_at_range'] = job_posted_at_range
        # NESTED object, not the bracketed key: 'revenue_range[min]' is the
        # docs' QUERY-STRING spelling, and in a JSON body Apollo silently
        # ignores it (same bug fixed in discover_organizations — see its
        # comment for the live-probe evidence). Build the nested dict here
        # so this filter never regresses to the flat/bracketed form.
        revenue_range: dict[str, int] = {}
        if revenue_min is not None:
            revenue_range['min'] = revenue_min
        if revenue_max is not None:
            revenue_range['max'] = revenue_max
        if revenue_range:
            body['revenue_range'] = revenue_range
        return body

    async def _post_search(self, body: dict[str, Any]) -> Result[SearchResult]:
        url = f'{self._base_url}/mixed_people/api_search'
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            logger.warning('apollo.search.transport_error', error=type(exc).__name__)
            return err(
                'CONTACTS.TRANSPORT',
                detail='the contact database could not be reached',
                cause=type(exc).__name__,
            )
        return _decode_search(resp, page=body['page'], per_page=body['per_page'])

    async def match_person(
        self,
        *,
        apollo_id: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        organization_name: str | None = None,
        organization_domain: str | None = None,
        linkedin_url: str | None = None,
        email: str | None = None,
        reveal_personal_emails: bool = False,
        reveal_phone_number: bool = False,
    ) -> Result[PersonEnriched]:
        """Look up one person, returning the full enriched record.

        Apollo bills 1 credit for a successful match. ``reveal_personal_emails`` left
        off by default: personal emails (gmail/yahoo) are noisy for B2B
        outreach and consume extra credits on some plans.

        ``reveal_phone_number`` triggers Apollo's async phone job: the sync
        body still returns email, and this method then polls
        /webhook_result for the number before returning. It costs extra
        Apollo credits and the number is not guaranteed to exist, so a
        no-phone reveal leaves ``phone`` as ``None``.

        Anchor priority — strongest match key first:

        1. ``apollo_id`` — the ``id`` returned by :meth:`search_people`.
           Apollo's People Enrichment endpoint accepts it for an exact
           1:1 reveal. This is the canonical "search → reveal the row I
           picked" path and the only one that works when the preview
           ``last_name`` is obfuscated (Basic plan). Prefer it.
        2. ``linkedin_url`` — also returned (un-gated) by search.
        3. name + org / email — the legacy attribute match, which fails
           on obfuscated last names and is the reason the old flow
           returned null emails while still consuming a credit.
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        body: dict[str, Any] = {
            'reveal_personal_emails': reveal_personal_emails,
        }
        if apollo_id:
            body['id'] = apollo_id
        if first_name:
            body['first_name'] = first_name
        if last_name:
            body['last_name'] = last_name
        if organization_name:
            body['organization_name'] = organization_name
        if organization_domain:
            body['domain'] = organization_domain
        if linkedin_url:
            body['linkedin_url'] = linkedin_url
        if email:
            body['email'] = email

        # At least one identifying field must be present, otherwise
        # Apollo returns 400 and we waste a round-trip.
        if not any(k in body for k in ('id', 'first_name', 'linkedin_url', 'email')):
            return err(
                'CONTACTS.MATCH_INSUFFICIENT_INPUT',
                detail='Provide at least one of: apollo_id, linkedin_url, email, '
                'or (first_name + last_name + organization_name/domain)',
            )

        # Apollo rejects reveal_phone_number unless webhook_url is present, even
        # though we poll for the result rather than serving the callback.
        if reveal_phone_number:
            body['reveal_phone_number'] = True
            body['webhook_url'] = _PHONE_REVEAL_WEBHOOK_URL

        url = f'{self._base_url}/people/match'
        # The phone reveal needs the raw match response (the request id rides
        # on it, not on the decoded person), so the closure hands it back out.
        match_resp: httpx.Response | None = None

        async def _match() -> Result[PersonEnriched]:
            nonlocal match_resp
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    match_resp = await client.post(
                        url,
                        headers=self._headers(),
                        json=body,
                    )
            except httpx.HTTPError as exc:
                logger.warning('apollo.match.transport_error', error=type(exc).__name__)
                return err(
                    'CONTACTS.TRANSPORT',
                    detail='the contact database could not be reached',
                    cause=type(exc).__name__,
                )
            return _decode_match(match_resp)

        result = await paid_call('apollo.match_person', _match)
        if reveal_phone_number and isinstance(result, Ok) and match_resp is not None:
            return await resolve_phone(match_resp, result.value, self._base_url, self._headers())
        return result

    async def search_organizations(
        self,
        name: str,
        *,
        location: str | None = None,
        per_page: int = 5,
    ) -> Result[list[OrganizationSearchResult]]:
        """Search Apollo's company database by name + optional location.

        Bills 1 Apollo credit per page (the ``/mixed_companies/search``
        endpoint — the previously-used ``/mixed_companies/api_search`` was
        free only because it 404s). Used to resolve a company NAME to an id
        before company_job_postings, and by the enrichment adapter to hydrate
        a domain from a KG stub (display_name only). Rows are the fusion of
        the response's two arrays (``accounts`` + ``organizations``, #1998)
        on relative rank — vendor relevance WITHIN each array, no relevance
        order across them (see ``_merge_discovery_arrays``).

        The ``location`` hint accepts free-text city/country ("Milano",
        "Milan, Italy", "New York, NY") and is passed as
        ``organization_locations[]``. Even partial matches help filter
        generic-name false positives — "Contech" alone returns ~20
        unrelated companies; "Contech + Milano" narrows to one or two.

        Per-page is capped at 25 (Apollo allows 100 but we don't need
        more and the trimmed payload bloats response size). Empty list
        → ``Ok([])``. Returns ``Err`` with the same code taxonomy as
        :meth:`search_people` on transport/auth/rate-limit failures.
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        body: dict[str, Any] = {
            'q_organization_name': name,
            'page': 1,
            'per_page': min(25, max(1, per_page)),
        }
        if location:
            body['organization_locations'] = [location]

        logger.info(
            'apollo.search_organizations.start',
            name=name,
            location=location,
            per_page=body['per_page'],
        )

        # ``/mixed_companies/api_search`` is DEAD — it 404s (verified live
        # 2026-07-14), which silently broke every name→id resolve: the
        # company_job_postings tool and the KG enrichment adapter both fell
        # through to web_search grinding. The live org-search endpoint is
        # ``/mixed_companies/search`` (same one discovery uses); it accepts the
        # same ``q_organization_name`` body and splits its rows across the
        # same two arrays, ``accounts`` + ``organizations`` (#1998).
        # Trade-off: it bills 1 Apollo credit/page (the dead endpoint was
        # "free" only by always failing) — still far cheaper than the 404 →
        # web_search fallback it replaces.
        url = f'{self._base_url}/mixed_companies/search'

        async def _search() -> Result[list[OrganizationSearchResult]]:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, headers=self._headers(), json=body)
            except httpx.TimeoutException as exc:
                logger.warning(
                    'apollo.search_organizations.timeout',
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.TIMEOUT',
                    detail='the company search did not answer in time',
                    cause=type(exc).__name__,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    'apollo.search_organizations.transport_error',
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.NETWORK_ERROR',
                    detail='the contact database could not be reached',
                    cause=type(exc).__name__,
                )
            return _decode_organization_search(resp)

        return await paid_call('apollo.search_organizations', _search, name=name)

    async def discover_organizations(
        self,
        *,
        locations: list[str] | None = None,
        employee_ranges: list[str] | None = None,
        keyword_tags: list[str] | None = None,
        industry_tag_ids: list[str] | None = None,
        job_titles: list[str] | None = None,
        min_active_jobs: int | None = None,
        latest_funding_min: int | None = None,
        total_funding_min: int | None = None,
        revenue_min: int | None = None,
        revenue_max: int | None = None,
        sort_by: str | None = None,
        sort_descending: bool = True,
        page: int = 1,
    ) -> Result[CompanyDiscoveryResult]:
        """Discover companies via /mixed_companies/search — 1 Apollo credit
        per PAGE regardless of row count (measured live 2026-07-13), so
        ``per_page`` is pinned at :data:`DISCOVERY_PAGE_ROWS`: fewer rows cost
        exactly the same.
        Callers slice the page for LLM payloads; they must not re-fetch to
        paginate within it.

        ``sort_by`` takes a :data:`DISCOVERY_SORT_FIELDS` key ('employees',
        'founded_year'); anything else is the caller's validation problem —
        Apollo rejects unknown fields with "Invalid sort criteria" (0 credits).
        Error taxonomy matches :meth:`search_organizations` (``CONTACTS.*``).
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        body: dict[str, Any] = {
            'page': max(1, page),
            'per_page': DISCOVERY_PAGE_ROWS,
        }
        if locations:
            body['organization_locations'] = expand_macro_regions(locations)
        if employee_ranges:
            body['organization_num_employees_ranges'] = list(employee_ranges)
        if keyword_tags:
            body['q_organization_keyword_tags'] = list(keyword_tags)
        # Precise Apollo industry taxonomy — the fix for keyword_tags' loose
        # match surfacing off-sector conglomerates (see APOLLO_INDUSTRY_TAGS).
        if industry_tag_ids:
            body['organization_industry_tag_ids'] = list(industry_tag_ids)
        # Hiring signal — job titles in the company's ACTIVE postings, and the
        # count of active postings. Both live-verified to filter on this
        # endpoint 2026-07-14 (Milan 56,557 → 402 with min 10 jobs; → 76 with
        # job title "software engineer"). This is the direct filter for the
        # "aziende che assumono / che cercano <ruolo>" recruiting asks that
        # otherwise fan out over web_search + search_jobs.
        if job_titles:
            body['q_organization_job_titles'] = list(job_titles)
        if min_active_jobs is not None:
            # NESTED object — the bracket form organization_num_jobs_range[min]
            # is silently ignored (live-verified: stayed at the 56,557 baseline),
            # same trap as revenue_range below.
            body['organization_num_jobs_range'] = {'min': min_active_jobs}
        # Funding filters (live-verified: total_funding min 50M → 113 rows,
        # latest_funding min 10M → 161). Nested object, min-only (the common
        # ask is "raised at least X" / "well-funded startups").
        if latest_funding_min is not None:
            body['latest_funding_amount_range'] = {'min': latest_funding_min}
        if total_funding_min is not None:
            body['total_funding_range'] = {'min': total_funding_min}
        # NESTED object, not the bracketed key: 'revenue_range[min]' is the
        # docs' QUERY-STRING spelling, and in a JSON body Apollo silently
        # ignores it — the filter simply doesn't filter (caught by live
        # small-batch tests 2026-07-13: min=1B still returned sub-1B rows;
        # nested form returns 127 all-≥1B rows for Milan).
        revenue_range: dict[str, int] = {}
        if revenue_min is not None:
            revenue_range['min'] = revenue_min
        if revenue_max is not None:
            revenue_range['max'] = revenue_max
        if revenue_range:
            body['revenue_range'] = revenue_range
        if sort_by:
            body['sort_by_field'] = DISCOVERY_SORT_FIELDS.get(sort_by, sort_by)
            body['sort_ascending'] = not sort_descending

        logger.info(
            'apollo.discover_organizations.start',
            locations=locations,
            employee_ranges=employee_ranges,
            keyword_tags=keyword_tags,
            page=body['page'],
            sort_by=sort_by,
        )

        url = f'{self._base_url}/mixed_companies/search'

        async def _discover() -> Result[CompanyDiscoveryResult]:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, headers=self._headers(), json=body)
            except httpx.TimeoutException as exc:
                logger.warning(
                    'apollo.discover_organizations.timeout',
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.TIMEOUT',
                    detail='the company discovery did not answer in time',
                    cause=type(exc).__name__,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    'apollo.discover_organizations.transport_error',
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.NETWORK_ERROR',
                    detail='the contact database could not be reached',
                    cause=type(exc).__name__,
                )
            return _decode_company_discovery(resp)

        return await paid_call('apollo.discover_organizations', _discover)

    async def enrich_organization(
        self,
        domain: str,
    ) -> Result[OrganizationEnriched]:
        """Enrich a company by domain — costs 1 Apollo credit.

        Use this AFTER :meth:`search_organizations` confirms the domain;
        never call it speculatively. ``domain`` MUST be a bare hostname
        ("acme.com", not "https://acme.com/" — the client strips schemes
        defensively but caller-side normalisation is preferred).

        Returns ``Err`` with code ``"CONTACTS.ORG_NOT_FOUND"`` when the
        domain is unknown — distinguishable from network errors so the
        adapter can decide to fall back to LLM classification cleanly.
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        normalised = _normalise_domain(domain)
        if not normalised:
            return err(
                'CONTACTS.INVALID_INPUT',
                detail='domain must be a non-empty hostname',
            )

        logger.info('apollo.enrich_organization.start', domain=normalised)

        url = f'{self._base_url}/organizations/enrich'

        async def _enrich() -> Result[OrganizationEnriched]:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(
                        url,
                        headers=self._headers(),
                        params={'domain': normalised},
                    )
            except httpx.TimeoutException as exc:
                logger.warning(
                    'apollo.enrich_organization.timeout',
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.TIMEOUT',
                    detail='the company enrichment did not answer in time',
                    cause=type(exc).__name__,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    'apollo.enrich_organization.transport_error',
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.NETWORK_ERROR',
                    detail='the contact database could not be reached',
                    cause=type(exc).__name__,
                )
            return _decode_organization_enrich(resp)

        result = await paid_call(
            'apollo.enrich_organization',
            _enrich,
            domain=normalised,
        )
        return result

    async def get_job_postings(
        self,
        *,
        org_id: str,
        org_name: str = '',
        page: int = 1,
    ) -> Result[JobPostingsResult]:
        """Fetch one page of a company's open job postings.

        Costs ~1 Apollo credit per page regardless of row count (same
        cost shape as :meth:`discover_organizations`). Call this AFTER
        resolving ``org_id`` — via :meth:`search_organizations` or
        :meth:`enrich_organization` — this method does no name resolution
        of its own. ``org_name`` is optional context threaded straight
        into the result envelope (see :class:`JobPostingsResult`).
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        if not org_id:
            return err(
                'CONTACTS.INVALID_INPUT',
                detail='org_id must be a non-empty string',
            )

        logger.info('apollo.get_job_postings.start', org_id=org_id, page=page)

        url = f'{self._base_url}/organizations/{org_id}/job_postings'

        async def _postings() -> Result[JobPostingsResult]:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(
                        url,
                        headers=self._headers(),
                        params={'page': max(1, page)},
                    )
            except httpx.TimeoutException as exc:
                logger.warning('apollo.get_job_postings.timeout', error=type(exc).__name__)
                return err(
                    'CONTACTS.TIMEOUT',
                    detail='the job postings lookup did not answer in time',
                    cause=type(exc).__name__,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    'apollo.get_job_postings.transport_error',
                    error=type(exc).__name__,
                )
                return err(
                    'CONTACTS.NETWORK_ERROR',
                    detail='the contact database could not be reached',
                    cause=type(exc).__name__,
                )
            return _decode_job_postings(resp, org_name=org_name)

        return await paid_call('apollo.get_job_postings', _postings, org_id=org_id)
