"""Apollo.io data shapes, limits and taxonomy helpers shared by the client and decoders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# What the PROVIDER accepts on one page, and the only clamp this transport
# applies. Documented as 100 (100 per page x 500 pages = the endpoint's 50,000
# display limit) and the search costs 0 credits at Apollo either way, so
# asking for fewer rows than the caller wants buys nothing here — it only
# makes the CALLER pay for another page of its own.
MAX_PROVIDER_PER_PAGE = 100
#: The one page size of a people directory search. The vendor bills per row
#: (AI Ark 0.5 credits each), and a 100-row default bought ~57 rows the model
#: never read (the context budget admits ~43): 128 such pages were ~59% of the
#: AI Ark search spend on staging, 10-29 Sep. Apollo's free search (above) is
#: not what the default is sized for. 25 is a first sample the user can judge
#: and start a campaign on, and it divides the full page, so a balance-capped
#: call still maps onto whole ``_CAPPED_PAGE_SIZES`` slices of it. "Get more"
#: is the next page.
#:
#: FIXED, whatever count the user asks for: ``page`` is counted in pages of
#: this size, so a size that changes between turns moves the offset. Sizing
#: page 2 at 100 after a 25-row page 1 served rows 101-200 and skipped 26-100
#: (staging e2e, 30 Sep). N people is ceil(N / 25) pages.
DEFAULT_PAGE_ROWS = 25

# Rows one SHORTLIST people-search page may carry. Not a provider limit: a
# 100-row payload is too noisy for the LLM and burns prompt-cache locality on
# rows no human will read. Public because it is the ceiling a shortlist search
# is spread across: 25 people over every domain passed, not 25 per company,
# and the handler has to say so rather than let the model read a page slice as
# a finished sweep (#1756). ``people_directory_search`` is deliberately NOT
# bound by it — that tool exists to size a pool, and when it billed 5 credits
# a page, capping its page at a quarter of what the provider hands over made a
# 100-person pool cost 20 credits instead of 5. It now bills 0.4 a returned row.
MAX_SEARCH_PER_PAGE = 25

# Canonical Apollo seniority enum — single source of truth for both
# ``find_decision_maker`` and ``people_directory_search`` handlers so the
# two tools never drift from each other. ``junior`` is a valid Apollo
# label (confirmed via their public API docs) that was previously
# missing from the local ``_SENIORITY_VALUES`` tuple; it is included
# here. Handler descriptions and Pydantic enums should reference this
# constant rather than maintaining their own copy.
APOLLO_SENIORITIES: tuple[str, ...] = (
    'owner',
    'founder',
    'c_suite',
    'partner',
    'vp',
    'head',
    'director',
    'manager',
    'senior',
    'junior',
    'entry',
    'intern',
)

#: Spellings folded onto :data:`APOLLO_SENIORITIES` (#2082). Keys are compared
#: after :func:`_seniority_key` (lowered, spaces/hyphens to underscores);
#: values are enum members verbatim. An enum member maps to itself one branch
#: earlier, so only REAL translations live here.
#:
#: Every key carries its source on the line above it, and a key with no source
#: does not belong in this table: folding a level onto a different level is a
#: claim about someone's job, and the documented fallback for a token no map
#: knows is to pass it through verbatim and log it, not to guess. Tokens that
#: were dropped for want of a source rather than because they are wrong:
#: ``executive``, ``chief``, ``unpaid``, ``training``, ``clevel``,
#: ``vice_president``, ``cofounder``, ``co_founder``, ``internship``,
#: ``trainee``. Restore one only alongside the vendor page or the captured
#: payload that names the enum member it means. An ordinal argument is not
#: that page: PDL publishes the ORDER of its levels, not an equivalence, and
#: the same arithmetic that made ``training`` an intern makes ``unpaid`` one
#: too.
_SENIORITY_SYNONYMS: dict[str, str] = {
    # PDL ``job_title_levels``, the field the issue names. The canonical list
    # is published at https://docs.peopledatalabs.com/docs/job-title-levels,
    # where ``cxo`` is defined as the catch-all for "Chief __ Officer" roles
    # (CEO, CIO, CTO), which is exactly what Apollo calls ``c_suite``.
    'cxo': 'c_suite',
    # Not vendor tokens: the long spellings this product itself prints for two
    # enum members, which a model then writes back at us.
    # ``contact_rows.SENIORITY`` renders ``c_suite`` as "C-level" and ``entry``
    # as "Entry level", and the ``people`` tool descriptions spell "C-Level" in
    # their own examples.
    'c_level': 'c_suite',
    'entry_level': 'entry',
}

_APOLLO_SENIORITY_SET = frozenset(APOLLO_SENIORITIES)


def _seniority_key(raw: str) -> str:
    return raw.strip().lower().replace('-', '_').replace(' ', '_')


def normalize_seniority(raw: str | None, *, source: str) -> str | None:
    """``raw`` as a member of :data:`APOLLO_SENIORITIES`, verbatim otherwise.

    The seniority cell downstream (``contact_rows.SENIORITY``) maps ONLY
    the Apollo enum, and its stated rule for anything else is fall-through
    VERBATIM: dropping a field the user paid to reveal is the worse of the two
    failures, and an unmapped token is at least visibly ours to fix. This
    normalizer exists so a token another vendor spells differently (``cxo``)
    stops falling to that path AS IF it were data. Known spellings translate,
    enum members pass, and only the genuinely unknown, logged so the map can
    grow, keeps the old visible-verbatim behaviour.

    Lives beside the enum, not in one adapter, because five modules read this
    vocabulary and a private copy re-opens the drift one consumer along. Three
    of them fold through here; ``entity_columns`` renders the result and
    ``people.api`` deliberately stays strict, which is a decision each of them
    documents rather than a copy of the table.

    ``source`` says which boundary the token crossed and is required, not
    defaulted, because ``apollo.seniority_unmapped`` is the evidence the map
    grows on: a value spelled ``provider:*`` came off a vendor payload and is
    a candidate for a new key, while ``model:*`` is a spelling an LLM invented
    and is evidence about a prompt. Mixed into one bucket the log would
    propose synonyms for tokens no provider emits.
    """
    key = _seniority_key(raw or '')
    if not key:
        return None
    if key in _APOLLO_SENIORITY_SET:
        return key
    mapped = _SENIORITY_SYNONYMS.get(key)
    if mapped:
        return mapped
    logger.info(
        'apollo.seniority_unmapped',
        # NOT ``token``: that key name is in ``logging_redaction.
        # _SENSITIVE_KEYS``, so the one field this event exists to carry
        # would reach an operator as the redaction marker.
        seniority_token=(raw or '')[:40],
        source=source,
    )
    return raw


# Apollo's ``contact_email_status[]`` SEARCH-FILTER enum, MEASURED on
# /mixed_people/api_search rather than read off the reference — because the
# reference is wrong for this endpoint.
#
# Live probe 2026-09-04, person_locations=["Bergamo, Italy"], baseline 57,670:
#
#     verified      21,821      unverified          57,670  (= baseline)
#     guessed       12,641      likely to engage    57,670  (= baseline)
#     unavailable   23,208      likely_to_engage    57,670  (= baseline)
#     bounced            0      zzz  (garbage)      57,670  (= baseline)
#     ────────────────────                          ────────────────────
#     sum           57,670                          not filtering at all
#
# The four on the left partition the pool EXACTLY. The four on the right are
# dropped in silence, which is this endpoint's whole failure mode (#1349,
# ``q_organization_domains``) and is indistinguishable from a working filter
# at every level except a count.
#
# ``unverified`` and ``likely to engage`` shipped here from 2026-07 to
# 2026-09 and never filtered anything. Worse than inert: Apollo drops the
# unrecognised member and KEEPS the recognised ones, measured — ["verified",
# "zzz"] returns 21,821, exactly ["verified"]. So ["verified", "likely to
# engage"], which both tool descriptions actively recommended, silently
# behaved as ["verified"] and cut the pool roughly in half against what the
# caller asked for.
#
# Apollo's own docs list `verified, unverified, likely to engage, unavailable`
# — but under ``/v1/people/search``, a DIFFERENT endpoint. Same lesson as
# #1661, where the sector keys behaved on one endpoint and still had to be
# proved on the other. On this client the docs are not evidence; the probe in
# tests/contract/test_apollo_people_unwired_filters_live.py is.
#
# ``bounced`` is real and legal and returns 0 rows on every pool probed. It
# stays in the tuple because it is what the wire accepts and this constant
# describes the wire; the tool descriptions are where callers are told not to
# reach for it.
#
# Still NOT the vocabulary ``app.people.provenance`` maps: that grades the
# ``email_status`` Apollo returns on a REVEALED record. The overlap is now
# three members rather than two, which makes collapsing them more tempting
# and no less wrong — ``unavailable`` never appears on a reveal, and the two
# enums are free to diverge again the next time either endpoint moves.
APOLLO_CONTACT_EMAIL_STATUSES: tuple[str, ...] = (
    'verified',
    'guessed',
    'unavailable',
    'bounced',
)


# ── Trimmed payload shapes ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PersonPreview:
    """A row from search — no email yet, just enough for the LLM to triage.

    ``id`` is always returned by Apollo's *free* People Search endpoint,
    which lets the reveal step anchor on the exact Apollo ``id`` instead
    of re-matching by the obfuscated last name, the root cause of the
    "match returns null email yet still charged" flow (see
    docs/apollo-reveal-by-id.md).

    ``linkedin_url``, ``seniority``, ``organization_domain`` and
    ``person_location`` are
    OPTIONAL: the search response omits them, carrying ``has_*``
    presence booleans and the organisation name instead (live probe
    2026-07-20, #1349). They stay on the shape so a plan that does
    return them decodes for free, but nothing may assume a preview
    carries them; the reveal (:class:`PersonEnriched`) does.

    ``has_city`` / ``has_state`` / ``has_country`` and the ``organization_has_*``
    fields are the presence booleans that ARE the payload for the fields
    above, per the live ``/mixed_people/api_search`` OpenAPI schema
    (docs.apollo.io, checked 2026-08-21) — this endpoint never sends the
    values themselves at this plan tier, only whether Apollo holds one.
    """

    id: str
    first_name: str
    last_name_obfuscated: str
    title: str
    seniority: str | None
    organization_name: str
    has_email: bool
    has_direct_phone: bool
    linkedin_url: str | None = None
    organization_domain: str | None = None
    person_location: str | None = None
    last_refreshed_at: str | None = None
    organization_logo_url: str | None = None
    has_city: bool = False
    has_state: bool = False
    has_country: bool = False
    organization_has_industry: bool = False
    organization_has_phone: bool = False
    organization_has_city: bool = False
    organization_has_state: bool = False
    organization_has_country: bool = False
    organization_has_zip_code: bool = False
    organization_has_revenue: bool = False
    organization_has_employee_count: bool = False

    def to_dict(self) -> dict[str, Any]:
        row = {
            'id': self.id,
            'first_name': self.first_name,
            'last_name_obfuscated': self.last_name_obfuscated,
            'title': self.title,
            'seniority': self.seniority,
            'organization': self.organization_name,
            'organization_domain': self.organization_domain,
            # Ahead of ``linkedin_url`` and the availability flags because
            # ``relevance_rerank.row_digest`` serializes in THIS order and then
            # hard-truncates: a real preview overran the 200-char budget and the
            # location was the field that fell off the end, so every "in the US"
            # criterion came back unanswerable. Insertion order is the only thing
            # that decides which fields survive that cut.
            'person_location': self.person_location,
            'linkedin_url': self.linkedin_url,
            'has_email': self.has_email,
            'has_direct_phone': self.has_direct_phone,
            'last_refreshed_at': self.last_refreshed_at,
            'has_city': self.has_city,
            'has_state': self.has_state,
            'has_country': self.has_country,
            'organization_has_industry': self.organization_has_industry,
            'organization_has_phone': self.organization_has_phone,
            'organization_has_city': self.organization_has_city,
            'organization_has_state': self.organization_has_state,
            'organization_has_country': self.organization_has_country,
            'organization_has_zip_code': self.organization_has_zip_code,
            'organization_has_revenue': self.organization_has_revenue,
            'organization_has_employee_count': self.organization_has_employee_count,
        }
        if self.organization_logo_url:
            row['organization_logo_url'] = self.organization_logo_url
        return row


@dataclass(frozen=True, slots=True)
class PersonEnriched:
    """Full reveal — email + verified status. 1 Apollo credit consumed."""

    id: str
    first_name: str
    last_name: str
    title: str
    seniority: str | None
    email: str | None
    email_status: str | None  # 'verified', 'guessed', 'unverified', None
    linkedin_url: str | None
    organization_name: str
    organization_domain: str | None
    country: str | None
    organization_id: str | None = None
    organization_logo_url: str | None = None
    # Populated only when the paid people-match response carries these fields.
    # Never issue a second organization request per person-table row.
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
    # The taxonomy behind ``current_technologies``. ``organization_technologies``
    # already carries the flat vendor names from ``technology_names``, so this
    # keeps only what that list cannot say: which categories the stack spans.
    organization_technology_categories: tuple[str, ...] = ()
    # Round-by-round funding history, compacted the same way as
    # ``employment_history``. Distinct from the ``organization_latest_funding_*``
    # fields, which carry only the most recent round as sortable values.
    organization_funding_events: tuple[str, ...] = ()
    city: str | None = None
    state: str | None = None
    person_location: str | None = None
    photo_url: str | None = None
    headline: str | None = None
    twitter_url: str | None = None
    github_url: str | None = None
    facebook_url: str | None = None
    # Apollo's own 0..1 score for a *guessed* address; None on a verified email
    # and on plans that do not compute it. Not the same signal as
    # ``email_status``, which stays the authoritative verification label.
    extrapolated_email_confidence: float | None = None
    # Tri-state on purpose: False is Apollo saying "unlikely", None is Apollo
    # not answering. Collapsing the two would invent a negative signal.
    is_likely_to_engage: bool | None = None
    departments: tuple[str, ...] = ()
    subdepartments: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    # The source can carry a long raw history. The compact strings keep what a
    # reader can use (current/title/employer/period) and work as chips in the
    # table, while preventing one reveal from bloating a chat turn. Apollo's
    # internal organisation id is not among them — see ``_employment_history``.
    employment_history: tuple[str, ...] = ()
    # Populated only when a phone reveal was requested and resolved. ``phone``
    # is the E.164 sanitized number; ``phone_status`` carries the phone job's
    # status ('pending' from the sync body, 'revealed' once the poll lands a
    # number). Both stay None on a plain reveal.
    phone: str | None = None
    phone_status: str | None = None

    @property
    def has_contact_channel(self) -> bool:
        """True when the reveal surfaced a usable channel: an email, a
        LinkedIn URL, or a revealed phone number.

        Single source of truth for "is this reveal chargeable". Both the
        chat handler and the REST use case gate their refund-on-empty
        logic on this so the billing policy can't drift between the two
        surfaces. ``email`` is already normalised by ``_decode_match``
        (Apollo's ``email_not_unlocked@…`` sentinel decodes to ``None``),
        so a locked placeholder never counts as a usable channel here. A
        phone counts only once the async reveal actually landed a number.
        """
        return bool(self.email or self.linkedin_url or self.phone)

    def to_dict(self) -> dict[str, Any]:
        row = {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'title': self.title,
            'seniority': self.seniority,
            'email': self.email,
            'email_status': self.email_status,
            'linkedin_url': self.linkedin_url,
            'organization': self.organization_name,
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


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Result of a search call: trimmed previews + total + page metadata."""

    total_entries: int
    page: int
    per_page: int
    people: list[PersonPreview] = field(default_factory=list)
    #: Set only by the AI-Ark-supplemented path, only on a "get more" page
    #: whose floor came out lower than one a previous page of the same
    #: search already promised (see
    #: ``PeopleSearchPage.delivered_fewer_than_promised``). ``None`` on
    #: every other result, Apollo-alone included.
    delivered_fewer_than_promised: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'total_entries': self.total_entries,
            'page': self.page,
            'per_page': self.per_page,
            'people': [p.to_dict() for p in self.people],
        }
        if self.delivered_fewer_than_promised:
            payload['delivered_fewer_than_promised'] = self.delivered_fewer_than_promised
        return payload


@dataclass(frozen=True, slots=True)
class OrganizationSearchResult:
    """A trimmed company row from /v1/mixed_companies/search (the
    ``api_search`` variant this originally named is dead — it 404s, see
    :meth:`ApolloClient.search_organizations`). The row may have arrived
    in either of the response's two arrays — ``accounts`` (already saved,
    ``already_saved=True``) or ``organizations`` (net-new).

    ``id`` is always the ORGANIZATION id, whichever array carried the row
    (#1998): the two arrays do not identify a row the same way — an
    ``accounts`` record carries ``id`` = the CRM *account* id plus
    ``organization_id`` = the organization; an ``organizations`` record
    carries only ``id`` = the organization (live probe 2026-08-18: 7/7
    and 93/93 respectively). A saved row missing ``organization_id``
    yields ``id=''`` rather than silently substituting the CRM id from
    the wrong keyspace: the row stays usable for domain-driven paths
    (enrichment), and id-driven callers fail loudly and for free.

    Apollo's raw response is ~5 KB per match with nested employment
    history, technologies arrays, sic_codes, etc. We trim to the fields
    the enrichment use_case needs: a domain for the 1-credit enrich
    call, plus enough context (industry hint, location, employee count)
    to score whether the search match is the right company before
    spending the credit. The name score is not a field: Apollo returns none,
    and :mod:`app.people.company_resolution` scores each row against the
    requested name (#2307).
    """

    id: str
    name: str
    domain: str | None
    industry: str | None
    location: str | None  # primary HQ "City, Country"
    employee_count: int | None
    website: str | None
    already_saved: bool = False  # provenance: True = the ``accounts`` array

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'domain': self.domain,
            'industry': self.industry,
            'location': self.location,
            'employee_count': self.employee_count,
            'website': self.website,
            'already_saved': self.already_saved,
        }


@dataclass(frozen=True, slots=True)
class SuborganizationRef:
    """One subsidiary as the enrich response names it — a REFERENCE, not a
    profile.

    Apollo's ``suborganizations`` entries carry six keys (``id``, ``name``,
    ``website_url``, ``industries``, ``estimated_num_employees``,
    ``country``); this keeps the three that identify the company so a caller
    can look it up. The other three are deliberately not mirrored: half a
    profile per subsidiary invites reading it as the subsidiary's profile,
    and a subsidiary worth knowing about gets its own ``enrich_company``
    call, which returns all of it rather than the parent's summary of it.
    """

    id: str
    name: str
    website_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {'id': self.id, 'name': self.name}
        # Same absent-not-null rule as the enclosing payload.
        if self.website_url:
            row['website_url'] = self.website_url
        return row


@dataclass(frozen=True, slots=True)
class OrganizationEnriched:
    """Full company payload from /v1/organizations/enrich. 1 credit spent.

    The call is billed one credit whatever the payload size, so a vendor field
    left on the floor is a fact the user already paid for and never saw. What
    stays out is therefore what this product cannot read honestly or does not
    own, named here rather than merely absent:

    - the nested ``account`` object — Apollo's own CRM row for the company
      (owner id, Salesforce id, phone status, label ids). Vendor plumbing about
      OUR workspace, not data about the company.
    - ``org_chart_root_people_ids`` / ``org_chart_sector`` /
      ``org_chart_removed`` / ``org_chart_show_department_filter``, and
      ``industry_tag_id`` / ``industry_tag_hash`` — plumbing and internal ids.
    - ``show_intent`` / ``has_intent_signal_account`` / ``intent_signal_account``
      — Apollo's intent-data add-on, a separate paid signal this product does
      not buy, so on our plan they report a subscription's state rather than
      anything about the company. ``snippets_loaded`` is an internal flag.
    - the fields that would arrive twice: ``num_languages`` (``len(languages)``),
      ``retail_location_count``/``num_retail_locations``,
      ``estimated_num_employees`` (``employee_count``) and
      ``current_technologies``, which is ``technologies`` categorised — the
      categories reach the tool through the people-match record instead.
    """

    id: str
    name: str
    domain: str
    industry: str | None  # raw Apollo label, mapped to canonical by caller
    sic_codes: list[str]
    naics_codes: list[str]
    employee_count: int | None
    founded_year: int | None
    website: str | None
    technologies: list[str]
    short_description: str | None
    industries: list[str] = field(default_factory=list)
    logo_url: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None
    annual_revenue: int | None = None
    annual_revenue_printed: str | None = None
    headquarters_location: str | None = None
    funding_stage: str | None = None
    total_funding: int | None = None
    total_funding_printed: str | None = None
    latest_funding_amount: int | None = None
    latest_funding_amount_printed: str | None = None
    latest_funding_date: str | None = None
    parent_id: str | None = None
    suborganization_count: int | None = None
    # The rest of what the paid response already carries. Social and contact
    # links, the postal address broken into its parts, the round-by-round
    # funding history and the per-department headcount are all in the body the
    # credit bought; none of them costs a second call.
    blog_url: str | None = None
    angellist_url: str | None = None
    twitter_url: str | None = None
    facebook_url: str | None = None
    crunchbase_url: str | None = None
    # ``phone`` in E.164, i.e. dialer-ready. Kept beside ``phone`` rather than
    # instead of it: the human-formatted one is what a person reads.
    sanitized_phone: str | None = None
    languages: tuple[str, ...] = ()
    alexa_ranking: int | None = None
    # Apollo's own numeric id for the LinkedIn page, distinct from
    # ``linkedin_url``: it survives a company renaming its vanity slug.
    linkedin_uid: str | None = None
    # Both or neither, in practice ("8031" / "tyo"). Present on the listed
    # companies and null on everyone else, which makes the pair a public/private
    # flag as much as a ticker.
    publicly_traded_symbol: str | None = None
    publicly_traded_exchange: str | None = None
    keywords: tuple[str, ...] = ()
    secondary_industries: tuple[str, ...] = ()
    # The address in parts. ``headquarters_location`` above is one formatted
    # line built from these, and is ``raw_address`` verbatim whenever the vendor
    # sent one — which is exactly why ``raw_address`` is kept as its own field:
    # without it, a composed line and a vendor-authored one are indistinguishable.
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    raw_address: str | None = None
    # Apollo's meta description for the company site. Distinct from
    # ``short_description``: it is the sentence the company itself publishes.
    seo_description: str | None = None
    # Round-by-round history, compacted the way ``_funding_events`` compacts it
    # for the people-match record. Distinct from the ``latest_funding_*`` and
    # ``total_funding*`` fields above, which carry sortable single values.
    funding_events: tuple[str, ...] = ()
    # Headcount per department, e.g. ``{'engineering': 228, 'sales': 177}``.
    # A mapping rather than a flattened string: the numbers are the point, and
    # a caller that wants to compare or chart them should not have to re-parse.
    departmental_head_count: dict[str, int] = field(default_factory=dict)
    # The subsidiaries Apollo says this company directly owns. References, not
    # profiles — see ``SuborganizationRef``. ``suborganization_count`` above is
    # the vendor's own count and stays authoritative: this tuple is capped, so
    # a conglomerate can have more subsidiaries than it lists.
    suborganizations: tuple[SuborganizationRef, ...] = ()
    #: Apollo's headcount momentum over the trailing 6 / 12 / 24 months, as a
    #: percentage. CARRY THE CAVEAT WHEREVER THESE GO: Apollo states no base
    #: headcount for the window and no method, so "+24.6" is the vendor's
    #: estimate of a direction, not a figure derived from two counts this
    #: product can show. #2161 kept them off the discovery row for exactly that
    #: reason and this is the deliberate reversal of that call for the paid
    #: single-company read, where one company is being examined closely and the
    #: caveat can be stated in the same breath as the number. Anything that
    #: renders them owes the reader the word "estimated"; nothing may derive a
    #: headcount from them.
    headcount_six_month_growth: float | None = None
    headcount_twelve_month_growth: float | None = None
    headcount_twenty_four_month_growth: float | None = None

    def to_dict(self) -> dict[str, Any]:
        row = {
            'id': self.id,
            'name': self.name,
            'domain': self.domain,
            'industry': self.industry,
            'sic_codes': list(self.sic_codes),
            'naics_codes': list(self.naics_codes),
            'employee_count': self.employee_count,
            'founded_year': self.founded_year,
            'website': self.website,
            'technologies': list(self.technologies),
            'short_description': self.short_description,
        }
        # Absent, not null and not empty: a reader decides whether the vendor
        # answered by asking whether the key is there, and a key that is always
        # present with an empty value makes "Apollo knows nothing here"
        # indistinguishable from "nobody has looked yet". One shared filter for
        # all of them — a guard per field is how the next one added comes to
        # obey a slightly different rule than the one before it.
        enrichment = {
            'industries': list(self.industries),
            'logo_url': self.logo_url,
            'linkedin_url': self.linkedin_url,
            'phone': self.phone,
            'annual_revenue': self.annual_revenue,
            'annual_revenue_printed': self.annual_revenue_printed,
            'headquarters_location': self.headquarters_location,
            'funding_stage': self.funding_stage,
            'total_funding': self.total_funding,
            'total_funding_printed': self.total_funding_printed,
            'latest_funding_amount': self.latest_funding_amount,
            'latest_funding_amount_printed': self.latest_funding_amount_printed,
            'latest_funding_date': self.latest_funding_date,
            'parent_id': self.parent_id,
            'suborganization_count': self.suborganization_count,
            'blog_url': self.blog_url,
            'angellist_url': self.angellist_url,
            'twitter_url': self.twitter_url,
            'facebook_url': self.facebook_url,
            'crunchbase_url': self.crunchbase_url,
            'sanitized_phone': self.sanitized_phone,
            'languages': list(self.languages),
            'alexa_ranking': self.alexa_ranking,
            'linkedin_uid': self.linkedin_uid,
            'publicly_traded_symbol': self.publicly_traded_symbol,
            'publicly_traded_exchange': self.publicly_traded_exchange,
            'keywords': list(self.keywords),
            'secondary_industries': list(self.secondary_industries),
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'postal_code': self.postal_code,
            'country': self.country,
            'raw_address': self.raw_address,
            'seo_description': self.seo_description,
            'funding_events': list(self.funding_events),
            'departmental_head_count': dict(self.departmental_head_count),
            'suborganizations': [s.to_dict() for s in self.suborganizations],
            'headcount_six_month_growth': self.headcount_six_month_growth,
            'headcount_twelve_month_growth': self.headcount_twelve_month_growth,
            'headcount_twenty_four_month_growth': (self.headcount_twenty_four_month_growth),
        }
        return row | {key: value for key, value in enrichment.items() if value not in (None, '', [], {})}


@dataclass(frozen=True, slots=True)
class JobPosting:
    """A trimmed row from /organizations/{id}/job_postings.

    The raw response carries more fields per posting (department tags,
    seniority hints that not every plan populates); we keep the ones the
    LLM needs to answer "what is this company hiring for right now" —
    title, location, the listing URL, and when it was posted.
    """

    title: str
    city: str | None
    country: str | None
    url: str | None
    posted_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            'title': self.title,
            'city': self.city,
            'country': self.country,
            'url': self.url,
            'posted_at': self.posted_at,
        }


@dataclass(frozen=True, slots=True)
class JobPostingsResult:
    """One billed page of a single company's open job postings.

    ``org_name`` is not part of the postings response itself (Apollo's
    job_postings payload is just the array + pagination) — callers pass
    it through from whatever earlier lookup resolved ``org_id``, so the
    tool-result envelope still reads naturally without a second round-trip.
    """

    org_name: str
    total: int
    postings: list[JobPosting] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'org_name': self.org_name,
            'total': self.total,
            'postings': [p.to_dict() for p in self.postings],
        }


# Rows in one /mixed_companies/search page. One page is one Apollo credit
# whatever the row count (measured live 2026-07-13), so this is pinned at the
# maximum: fewer rows cost exactly the same. Exported because a caller that
# bounds how many rows it wants has to turn that number into a page budget, and
# doing it against its own copy of 100 is how the two drift apart.
DISCOVERY_PAGE_ROWS = 100

# Server-side sort fields accepted by /mixed_companies/search. Probed live
# 2026-07-13 (undocumented — the public docs list no sort for this endpoint):
# these two return re-ordered pages; every revenue-flavoured name is rejected
# with "Invalid sort criteria" (which bills 0 credits). "Top by revenue" is
# therefore a revenue_range[min] filter + client-side sort, not a sort field.
DISCOVERY_SORT_FIELDS: dict[str, str] = {
    'employees': 'organization_estimated_number_employees',
    'founded_year': 'organization_founded_year',
}

# Apollo industry-taxonomy tag IDs (``organization_industry_tag_ids``) for the
# sectors users ask for most. UNLIKE ``q_organization_keyword_tags`` (a loose
# full-text match — "software" surfaces Prada / Pirelli / Randstad ranked by
# size), these are Apollo's precise industry classification. Live-validated
# 2026-07-14 on "tech companies in Milan": keyword_tags returned 19,564 rows
# topped by non-tech conglomerates; ``organization_industry_tag_ids`` returned
# 420 genuine tech firms (Bending Spoons, MotorK, Kaleyra, Fabrick, …).
#
# Only VALIDATED ids ship here. Several plausible-looking hashes were probed
# and REJECTED — financial_services / internet returned 0 rows, and the
# "telecommunications" / "manufacturing" hashes resolved to pharma / hospitality
# — so expand this map ONLY after a live check, never from a guessed id.
APOLLO_INDUSTRY_TAGS: dict[str, list[str]] = {
    'software': ['5567cd4e7369643b70010000'],
    'computer_software': ['5567cd4e7369643b70010000'],
    'it_services': ['5567cd4773696439b10b0000'],
    'information_technology': ['5567cd4773696439b10b0000'],
    'marketing': ['5567cd467369644d39040000'],
    'advertising': ['5567cd467369644d39040000'],
    # "tech" maps to computer_software ONLY, NOT it_services. Live-checked on
    # prod session f8c7c810 (2026-07-14): "migliori aziende tech a Milano" with
    # tech=[software+it_services] sorted by size returned Saipem (oil & gas),
    # TIM (telecom), Deloitte, Fastweb — the it_services tag pulls in the giant
    # conglomerates' internal-IT and consulting/staffing arms, which then top a
    # headcount sort. Software-only returns actual software firms (Dedalus,
    # Lutech, Cedacri, …). Users who genuinely want IT services / consulting
    # ask for it by name via ``it_services`` / ``information_technology``.
    'tech': ['5567cd4e7369643b70010000'],
    'technology': ['5567cd4e7369643b70010000'],
}


def _normalize_industry(name: object) -> str:
    """The taxonomy lookup key for an industry name: lower-cased, trimmed, with
    spaces as underscores (``'IT Services'`` -> ``'it_services'``)."""
    return str(name).strip().lower().replace(' ', '_')


def resolve_industry_tag_ids(industries: list[str] | None) -> list[str]:
    """Map human-readable industry names to Apollo tag IDs, deduped and
    order-preserving. Unknown names are dropped here: the search handlers send
    them as keyword tags instead (``unknown_industries``), so a name never
    silently filters nothing (#892)."""
    out: list[str] = []
    for name in industries or []:
        for tid in APOLLO_INDUSTRY_TAGS.get(_normalize_industry(name), []):
            if tid not in out:
                out.append(tid)
    return out


def unknown_industries(industries: list[str] | None) -> list[str]:
    """The names ``resolve_industry_tag_ids`` drops, order-preserving and
    deduped on the normalised key. Checking per name, not "did anything
    resolve": in ``["tech", "accounting"]`` tech resolves while accounting
    still filters nothing."""
    out: list[str] = []
    seen: set[str] = set()
    for name in industries or []:
        key = _normalize_industry(name)
        if key not in APOLLO_INDUSTRY_TAGS and key not in seen:
            seen.add(key)
            out.append(name)
    return out


@dataclass(frozen=True, slots=True)
class CompanyDiscoveryRow:
    """A trimmed company row from /v1/mixed_companies/search.

    The raw row is ~3 KB. What is dropped is what this product cannot read
    honestly — the intent-signal block (a paid add-on we do not buy),
    headcount-growth percentages with no base to apply them to, NAICS/SIC code
    arrays — not "the fields nobody asked for yet": the page is billed once
    whatever we keep, so a vendor field left on the floor is a column the user
    paid for and never saw. ``already_saved`` marks rows Apollo returned from
    the team's saved accounts rather than the net-new organizations pool — same
    data, different array upstream.
    """

    id: str
    name: str
    domain: str | None
    linkedin_url: str | None
    revenue_printed: str | None  # Apollo's human string, e.g. "4.4B"
    founded_year: int | None
    phone: str | None
    already_saved: bool
    #: The CRM *account* id an ``accounts`` row arrived under (#2039). Kept in
    #: its own field, never in ``id``: the two are different keyspaces, and
    #: ``id`` is always the ORGANIZATION id, the same rule
    #: ``OrganizationSearchResult`` states and #2025 measured (7/7 accounts
    #: rows carry both, 93/93 organizations rows carry only the org id).
    crm_account_id: str | None = None
    # The three fields the stated-criteria fit score is checked against
    # (#1419): headcount, HQ, primary industry. Already in the raw row, so
    # keeping them costs no extra credit — without them a "50-200 in Milan,
    # tech" ask has nothing to verify against and every row is unscored.
    employee_count: int | None = None
    industry: str | None = None
    location: str | None = None
    # The company mark the results table renders next to the name (#1518).
    # Whatever the vendor gave us, nothing else: a logo we synthesised from the
    # domain would be a third-party request per row that 404s into the broken
    # image web app#488 rules out.
    logo_url: str | None = None
    # The rest of what the FREE row already carries. Every field below arrived
    # in the page the credit already bought — reading it costs nothing extra,
    # and the ones measured populated on this plan (live probe 2026-08-21:
    # website_url, twitter_url, facebook_url, linkedin_uid, languages,
    # alexa_ranking, sanitized_phone) are the ones the sparse
    # revenue/headcount/industry trio above cannot answer for.
    #
    # NOT decoded from the same row, on purpose: ``show_intent``,
    # ``has_intent_signal_account`` and ``intent_signal_account``. Those are
    # Apollo's intent-data add-on — a separate paid signal this product does not
    # buy — so on our plan they describe the ADD-ON's state, not the company.
    # Surfacing them would put a column in the table that promises a feature
    # nothing here has been built around.
    website_url: str | None = None
    twitter_url: str | None = None
    facebook_url: str | None = None
    blog_url: str | None = None
    angellist_url: str | None = None
    crunchbase_url: str | None = None
    alexa_ranking: int | None = None
    languages: tuple[str, ...] = ()
    # Apollo's own numeric id for the LinkedIn page, distinct from
    # ``linkedin_url``: it survives a company renaming its vanity slug.
    linkedin_uid: str | None = None
    # Both or neither, in practice ("8031" / "tyo"). Present on the listed
    # companies and null on everyone else, which makes the pair a public/private
    # flag as much as a ticker.
    publicly_traded_symbol: str | None = None
    publicly_traded_exchange: str | None = None
    # ``phone`` in E.164, i.e. dialer-ready. Kept beside ``phone`` rather than
    # instead of it: the human-formatted one is what a person reads.
    sanitized_phone: str | None = None
    # The parent company, when Apollo knows the row is a subsidiary.
    owned_by_organization_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            'id': self.id,
            'name': self.name,
            'domain': self.domain,
            'linkedin_url': self.linkedin_url,
            'revenue_printed': self.revenue_printed,
            'founded_year': self.founded_year,
            'phone': self.phone,
            'already_saved': self.already_saved,
            'employee_count': self.employee_count,
            'industry': self.industry,
            'location': self.location,
        }
        # Absent, not null and not '': the FE decides whether to render a mark by
        # asking whether the key is there. A key that is always present with an
        # empty value makes "the vendor gave us nothing" indistinguishable from
        # "nobody has looked yet". That rule is the same for all of these, so
        # they share ONE filter — a guard per field is how the next one added
        # comes to obey a slightly different rule than the one before it.
        optional: dict[str, Any] = {
            'logo_url': self.logo_url,
            'website_url': self.website_url,
            'twitter_url': self.twitter_url,
            'facebook_url': self.facebook_url,
            'blog_url': self.blog_url,
            'angellist_url': self.angellist_url,
            'crunchbase_url': self.crunchbase_url,
            'alexa_ranking': self.alexa_ranking,
            'languages': list(self.languages),
            'linkedin_uid': self.linkedin_uid,
            'publicly_traded_symbol': self.publicly_traded_symbol,
            'publicly_traded_exchange': self.publicly_traded_exchange,
            'sanitized_phone': self.sanitized_phone,
            'owned_by_organization_id': self.owned_by_organization_id,
            # Its own key, never folded into ``id`` (#2039): a consumer that
            # wants the CRM account must ask for the CRM account.
            'crm_account_id': self.crm_account_id,
        }
        return row | {k: v for k, v in optional.items() if v not in (None, '', [])}


@dataclass(frozen=True, slots=True)
class CompanyDiscoveryResult:
    """One billed page of company discovery (1 Apollo credit per page)."""

    total_entries: int
    page: int
    per_page: int
    companies: list[CompanyDiscoveryRow]

    def to_dict(self) -> dict[str, Any]:
        return {
            'total_entries': self.total_entries,
            'page': self.page,
            'per_page': self.per_page,
            'companies': [c.to_dict() for c in self.companies],
        }
