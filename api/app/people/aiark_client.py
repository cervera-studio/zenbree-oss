"""AI Ark people-search HTTP client — narrow surface, trimmed output.

The people-side twin of :mod:`app.people.apollo_client`, wired to AI Ark's
developer portal. Two methods power the same two-step flow Apollo does:

- :meth:`AiArkClient.search_people` → POST /v1/people
  Returns preview rows (no email) so the LLM can triage which people are
  worth a reveal. Pagination is ``page``/``size`` only — AI Ark IGNORES a
  ``max_results`` field, so there is nothing here that mirrors Apollo's
  ``MAX_SEARCH_PER_PAGE`` shortlist ceiling.
- :meth:`AiArkClient.find_email` → POST /v1/people/export/single
  Real-time synchronous email reveal for one person, anchored on the AI
  Ark person ``id`` or a LinkedIn URL.

Like :class:`ApolloClient`, this is a pure transport: it converts every
``httpx`` exception into an ``Err`` at the boundary (never raises across
it), trims the large nested payloads to the fields the results table
needs, and reuses the ``CONTACTS.`` error taxonomy so a later adapter can
surface AI Ark's failures with the same codes the Apollo path already
does.

Two AI Ark quirks are load-bearing and easy to lose:

- The auth header is ``X-TOKEN`` — not ``Authorization``/``Bearer`` and not
  ``X-Api-Key`` (which Apollo uses).
- Cloudflare fronts the API and 403s any request whose ``User-Agent`` does
  not look like a browser (``python-urllib`` is blocked outright). We send an
  explicit browser UA on every request; without it the whole client 403s.
  See :data:`_BROWSER_USER_AGENT`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import structlog

from app.billing.paid_call import paid_call
from app.core.config import settings
from app.core.errors import Result, err, ok

logger = structlog.get_logger()

# Matches ApolloClient's 15 s budget: long enough for the slow tail of a
# synchronous reveal, short enough not to park a chat turn indefinitely.
_TIMEOUT_SECONDS = 15.0

# ``export/single`` verifies the mailbox live, so its tail is longer than a
# search: measured 0.8 s, 12.6 s and 23.2 s on ids a 15 s read had cut
# (session 8c595eae, #3666). Only the READ wait grows; connect stays short.
_EXPORT_READ_TIMEOUT_SECONDS = 30.0

# ai-ark: Cloudflare blocks non-browser UA (verified live 2026-09). A
# python-urllib / httpx-default UA gets a 403 error-1010 from the edge before
# the request ever reaches the API, so every call MUST carry a browser-like UA.
_BROWSER_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/128.0.0.0 Safari/537.36'
)

# AI Ark's page size ceiling (docs: max 100). ``page`` is zero-based. This is
# the only clamp the transport applies; unlike Apollo there is no separate
# shortlist cap, because AI Ark has no free "size a pool" tier to protect.
MAX_PROVIDER_SIZE = 100

# AI Ark's company-search ``lookalikeDomains`` anchor takes at most 5 domains
# (docs). We clamp to this rather than let the edge 400 the whole call.
MAX_LOOKALIKE_DOMAINS = 5

# The ``account.keyword`` sources AI Ark matches free text against. SMART mode
# (synonyms) on every source is the broad default a keyword ICP usually means.
_ACCOUNT_KEYWORD_SOURCES = ('NAME', 'DESCRIPTION', 'KEYWORD', 'SEO')

# The bound sent for the open side of a one-sided revenue ask. AI Ark's
# ``account.revenue`` is a closed RANGE, so "more than 10M" needs an explicit
# ceiling: one trillion USD, above the largest company's annual revenue
# (~0.7T), so the ceiling never excludes a real row. The floor of a
# "less than" ask is 0.
_REVENUE_OPEN_MAX = 1_000_000_000_000


def _revenue_filter(revenue_min: int | None, revenue_max: int | None) -> dict[str, Any] | None:
    """``account.revenue`` for a USD bracket, or ``None`` when neither bound is set."""
    if revenue_min is None and revenue_max is None:
        return None
    start = revenue_min if revenue_min is not None else 0
    end = revenue_max if revenue_max is not None else _REVENUE_OPEN_MAX
    return {'type': 'RANGE', 'range': [{'start': start, 'end': end}]}


def _technologies_filter(
    all_of: list[str] | None,
    any_of: list[str] | None,
    excluded: list[str] | None,
) -> dict[str, Any] | None:
    """``account.technologies`` from the three Apollo-shaped tech sets.

    WORD mode on the slugs verbatim (verified live 2026-09-30: ``hubspot``
    narrows 4.56M people to 166k). ``any`` may carry ``include`` and
    ``exclude`` together; ``all`` carries the uses-every-one set.
    """
    tech: dict[str, Any] = {}
    any_block: dict[str, Any] = {}
    if any_of:
        any_block['include'] = {'mode': 'WORD', 'content': list(any_of)}
    if excluded:
        any_block['exclude'] = {'mode': 'WORD', 'content': list(excluded)}
    if any_block:
        tech['any'] = any_block
    if all_of:
        tech['all'] = {'include': {'mode': 'WORD', 'content': list(all_of)}}
    return tech or None


# ── Trimmed payload shapes ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AiArkPerson:
    """A row from search — no email yet, just enough for the LLM to triage.

    ``id`` is AI Ark's uuid for the person and is PRESERVED verbatim: it is
    the join / dedupe / reveal key (single-use ``trackId`` aside, the reveal
    step anchors on this id). Mirrors the role Apollo's ``id`` plays on
    :class:`app.people.apollo_models.PersonPreview`.

    ``position_evidence`` is the description text off the person's employment
    history — the early-career signal AI Ark carries that Apollo does not.
    It is ``None`` when every position description is empty or missing.

    ``skills`` is the person's self-declared skill list — the other density
    signal Apollo's search tier withholds. Empty tuple when AI Ark sent none.

    The remaining fields are AI Ark's richer per-person signal that Apollo's
    search tier does not carry: the VERIFIED ``seniority_verified`` +
    ``functions`` (Apollo only sends ``has_*`` presence flags), the
    ``open_to_work`` / ``hiring`` / ``creator`` member badges, the top
    education, the ``headline`` + ``picture_url``, the search-tier company
    funding (type + amount, no per-round investors), and the social links.
    Each is null-safe: any may be missing/empty in a real response and then
    stays at its default.
    """

    id: str
    first_name: str
    last_name: str
    title: str
    full_name: str | None = None
    linkedin_url: str | None = None
    organization_name: str | None = None
    organization_domain: str | None = None
    organization_website_url: str | None = None
    person_location: str | None = None
    position_evidence: str | None = None
    skills: tuple[str, ...] = ()
    seniority_verified: str | None = None
    functions: tuple[str, ...] = ()
    open_to_work: bool = False
    hiring: bool = False
    creator: bool = False
    top_education_school: str | None = None
    top_education_degree: str | None = None
    headline: str | None = None
    picture_url: str | None = None
    company_funding_type: str | None = None
    company_funding_amount: int | None = None
    twitter_url: str | None = None
    github_url: str | None = None
    facebook_url: str | None = None
    # The dense arrays AI Ark already ships on every search row but the trimmed
    # shape used to collapse. ``employment_history`` and ``educations`` are the
    # FULL lists (each a small dict), where ``position_evidence`` /
    # ``top_education_*`` above keep only a one-line summary; ``certifications``
    # is "name — authority" strings; the ``organization_*`` tuples are the
    # employer's industries / stack / keywords; ``months_in_role`` is the tenure
    # in the current position; and ``company_funding_date`` /
    # ``company_funding_investors`` extend the scalar funding above with the
    # round date and its named investors when the payload carries them. Each is
    # null-safe: any may be missing/empty and then stays at its default.
    employment_history: tuple[dict[str, Any], ...] = ()
    # ``(title, employer, own_span)`` of every position the record says is
    # CURRENT — the positions the ``experience.current.title`` filter matches
    # against, which ``title`` (``profile.title``) is not bound to be one
    # of (#3459). Read by the search adapter's title check and nowhere else, so
    # it is not in ``to_dict``. See :func:`_current_positions`.
    current_positions: tuple[tuple[str, str | None, bool], ...] = ()
    educations: tuple[dict[str, Any], ...] = ()
    certifications: tuple[str, ...] = ()
    organization_industries: tuple[str, ...] = ()
    organization_technologies: tuple[str, ...] = ()
    organization_keywords: tuple[str, ...] = ()
    months_in_role: int | None = None
    company_funding_date: str | None = None
    company_funding_investors: tuple[str, ...] = ()
    # The employer's headcount, from ``company.summary.staff``: ``total`` is the
    # people LinkedIn lists at the company, ``range`` the size band the company
    # declares (``"11-50"``). The band is what AI Ark's ``employeeSize`` filter
    # matches, so it is the one a "fewer than 50 employees" check should read.
    organization_employee_count: int | None = None
    organization_employee_range: str | None = None
    # The provider's whole person payload minus the never-store list
    # (:func:`_stored_payload`). Not in ``to_dict``: it rides to the session's
    # raw-payload slot and from there to the table rows it lands in (#3652).
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False, hash=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': self.full_name,
            'title': self.title,
            'organization': self.organization_name,
            'organization_domain': self.organization_domain,
            'organization_website_url': self.organization_website_url,
            'person_location': self.person_location,
            'linkedin_url': self.linkedin_url,
            'position_evidence': self.position_evidence,
            'skills': list(self.skills),
            'seniority_verified': self.seniority_verified,
            'functions': list(self.functions),
            'open_to_work': self.open_to_work,
            'hiring': self.hiring,
            'creator': self.creator,
            'top_education_school': self.top_education_school,
            'top_education_degree': self.top_education_degree,
            'headline': self.headline,
            'picture_url': self.picture_url,
            'company_funding_type': self.company_funding_type,
            'company_funding_amount': self.company_funding_amount,
            'twitter_url': self.twitter_url,
            'github_url': self.github_url,
            'facebook_url': self.facebook_url,
        }


@dataclass(frozen=True, slots=True)
class AiArkSearchResult:
    """Result of a search call: trimmed rows + totals + page metadata.

    ``track_id`` is AI Ark's single-use token for the result set (expires
    ~6 h). Kept because the export/reveal flow may need it; never treated as
    stable state.
    """

    total_elements: int
    total_pages: int
    page: int
    size: int
    people: list[AiArkPerson] = field(default_factory=list)
    track_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'total_elements': self.total_elements,
            'total_pages': self.total_pages,
            'page': self.page,
            'size': self.size,
            'people': [p.to_dict() for p in self.people],
            'track_id': self.track_id,
        }


@dataclass(frozen=True, slots=True)
class AiArkEmail:
    """One revealed email for a person (1 reveal spent).

    ``address`` is the email; ``status`` is AI Ark's verification verdict
    (``VALID`` / ``INVALID``). The remaining flags describe the address the
    way AI Ark grades it — ``domain_type`` (``SMTP`` / ``CATCH_ALL`` /
    ``UNKNOWN``), whether it is a ``free`` provider, and whether it is a
    ``generic`` role address (info@, sales@).
    """

    address: str
    status: str | None = None
    domain_type: str | None = None
    found: bool = True
    free: bool = False
    generic: bool = False
    person_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'email': self.address,
            'email_status': self.status,
            'domain_type': self.domain_type,
            'found': self.found,
            'free': self.free,
            'generic': self.generic,
            'id': self.person_id,
        }


#: AI Ark's email verdict vocabulary → the Apollo enum the results table and
#: its FE label map already speak. The FE only knows Apollo's tokens
#: (``verified`` / ``guessed`` / ``unavailable`` / ``bounced``), so an AI Ark
#: ``VALID`` left verbatim falls through to the weakest "da verificare" label —
#: the wrong verdict on an address AI Ark verified. Mapped at the reveal
#: boundary so a revealed ``VALID`` shows "verificata". Only these three tokens
#: are translated; anything else (or ``None``) is left as-is so an unknown
#: vendor value is never silently relabelled.
_ARK_EMAIL_STATUS_TO_APOLLO: Final[dict[str, str]] = {
    'VALID': 'verified',
    'INVALID': 'bounced',
    'CATCH_ALL': 'guessed',
}


def normalize_ark_email_status(status: str | None) -> str | None:
    """AI Ark's ``VALID`` / ``INVALID`` / ``CATCH_ALL`` → the Apollo enum the
    FE label map understands. An unmapped token or ``None`` passes through
    unchanged. Pure — see :data:`_ARK_EMAIL_STATUS_TO_APOLLO`."""
    if status is None:
        return None
    return _ARK_EMAIL_STATUS_TO_APOLLO.get(status, status)


# ── Client ──────────────────────────────────────────────────────────────────


class AiArkClient:
    """HTTPX-backed AI Ark wrapper. Stateless except for the api key + base URL."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.aiark_api_key
        self._base_url = (base_url or settings.aiark_base_url).rstrip('/')
        self._timeout = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            # AI Ark authenticates on X-TOKEN, NOT Authorization/Bearer/X-Api-Key.
            'X-TOKEN': self._api_key,
            # ai-ark: Cloudflare blocks non-browser UA (verified live 2026-09).
            'User-Agent': _BROWSER_USER_AGENT,
        }

    async def search_people(
        self,
        *,
        person_titles: list[str] | None = None,
        title_mode: str = 'WORD',
        person_seniorities: list[str] | None = None,
        person_locations: list[str] | None = None,
        account_keywords: list[str] | None = None,
        account_domains: list[str] | None = None,
        account_industries: list[str] | None = None,
        account_locations: list[str] | None = None,
        employee_size: tuple[int, int] | None = None,
        technologies_all: list[str] | None = None,
        technologies_any: list[str] | None = None,
        technologies_excluded: list[str] | None = None,
        revenue_min: int | None = None,
        revenue_max: int | None = None,
        page: int = 0,
        size: int = 25,
    ) -> Result[AiArkSearchResult]:
        """Search AI Ark for people matching the filters.

        Returns previews (no email reveal). ``page`` is ZERO-based and
        ``size`` is clamped to :data:`MAX_PROVIDER_SIZE` (100). There is no
        ``max_results`` — AI Ark ignores it, so callers page with page/size.

        ``person_titles`` are wrapped under the current experience title with
        ``title_mode`` (``WORD`` exact-substring, ``SMART`` synonyms, ``STRICT``
        exact). ``person_locations`` / ``account_locations`` use AI Ark's
        ``"{Continent} {Country} {City}"`` string format and are passed
        through verbatim — this transport does not guess that shape on the
        caller's behalf. ``employee_size`` is an inclusive ``(start, end)``
        headcount range. ``technologies_*`` are the employer's tech-stack slugs
        (uses all / any / none of them); ``revenue_min`` / ``revenue_max`` are
        the employer's annual revenue in USD, either bound optional.
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        body = self._build_search_body(
            person_titles=person_titles,
            title_mode=title_mode,
            person_seniorities=person_seniorities,
            person_locations=person_locations,
            account_keywords=account_keywords,
            account_domains=account_domains,
            account_industries=account_industries,
            account_locations=account_locations,
            employee_size=employee_size,
            technologies_all=technologies_all,
            technologies_any=technologies_any,
            technologies_excluded=technologies_excluded,
            revenue_min=revenue_min,
            revenue_max=revenue_max,
            page=page,
            size=size,
        )
        result = await paid_call('aiark.search_people', lambda: self._post_search(body))
        return result

    def _build_search_body(
        self,
        *,
        person_titles: list[str] | None,
        title_mode: str,
        person_seniorities: list[str] | None,
        person_locations: list[str] | None,
        account_keywords: list[str] | None,
        account_domains: list[str] | None,
        account_industries: list[str] | None,
        account_locations: list[str] | None,
        employee_size: tuple[int, int] | None,
        page: int,
        size: int,
        technologies_all: list[str] | None = None,
        technologies_any: list[str] | None = None,
        technologies_excluded: list[str] | None = None,
        revenue_min: int | None = None,
        revenue_max: int | None = None,
    ) -> dict[str, Any]:
        # Only-set-when-non-empty, same contract as ApolloClient._build_search_body:
        # an absent filter must not appear in the nested object at all, so AI Ark
        # never receives an empty ``include`` it might read as "match nothing".
        contact: dict[str, Any] = {}
        if person_titles:
            contact['experience'] = {
                'current': {
                    'title': {
                        'any': {'include': {'mode': title_mode, 'content': list(person_titles)}},
                    },
                },
            }
        if person_seniorities:
            contact['seniority'] = {'any': {'include': list(person_seniorities)}}
        if person_locations:
            contact['location'] = {'any': {'include': list(person_locations)}}

        account: dict[str, Any] = {}
        if account_keywords:
            account['keyword'] = {
                'any': {
                    'include': {
                        'sources': [{'source': source, 'mode': 'SMART'} for source in _ACCOUNT_KEYWORD_SOURCES],
                        'content': list(account_keywords),
                    },
                },
            }
        if account_domains:
            # Company anchor: only people at these employer domains. Verified
            # live that AI Ark honours ``account.domain`` (``account.website``
            # is silently ignored), so this is the domain filter Apollo's
            # ``organization_domains`` is — the anchor find_decision_maker needs.
            account['domain'] = {'any': {'include': list(account_domains)}}
        if account_industries:
            account['industry'] = {'any': {'include': list(account_industries)}}
        if account_locations:
            account['location'] = {'any': {'include': list(account_locations)}}
        if employee_size is not None:
            start, end = employee_size
            account['employeeSize'] = {'type': 'RANGE', 'range': [{'start': start, 'end': end}]}
        technologies = _technologies_filter(technologies_all, technologies_any, technologies_excluded)
        if technologies:
            account['technologies'] = technologies
        revenue = _revenue_filter(revenue_min, revenue_max)
        if revenue:
            account['revenue'] = revenue

        body: dict[str, Any] = {
            'page': max(0, page),
            'size': min(MAX_PROVIDER_SIZE, max(1, size)),
        }
        if contact:
            body['contact'] = contact
        if account:
            body['account'] = account
        return body

    async def _post_search(self, body: dict[str, Any]) -> Result[AiArkSearchResult]:
        url = f'{self._base_url}/v1/people'
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.TimeoutException as exc:
            logger.warning('aiark.search.timeout', error=type(exc).__name__)
            return err('CONTACTS.TIMEOUT', detail='the contact search did not answer in time', cause=type(exc).__name__)
        except httpx.HTTPError as exc:
            logger.warning('aiark.search.transport_error', error=type(exc).__name__)
            return err(
                'CONTACTS.TRANSPORT', detail='the contact database could not be reached', cause=type(exc).__name__
            )
        return _decode_search(resp, page=body['page'], size=body['size'])

    async def find_email(self, url_or_id: str) -> Result[AiArkEmail]:
        """Reveal one person's email (real-time, synchronous, 1 reveal spent).

        ``url_or_id`` may be a LinkedIn URL or an AI Ark person id; a value
        that looks like a URL is sent as ``{"url": ...}``, otherwise as
        ``{"id": ...}``. A record that matches but carries no deliverable
        address (``found: false`` or an empty ``output``) is a value, not an
        exception: it returns ``Err(CONTACTS.NO_MATCH)``.
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')
        if not (url_or_id and url_or_id.strip()):
            return err('CONTACTS.MATCH_INSUFFICIENT_INPUT', detail='no url or id given')

        anchor = url_or_id.strip()
        body = {'url': anchor} if _looks_like_url(anchor) else {'id': anchor}
        return await paid_call('aiark.find_email', lambda: self._post_export(body))

    async def _post_export(self, body: dict[str, Any]) -> Result[AiArkEmail]:
        url = f'{self._base_url}/v1/people/export/single'
        timeout = httpx.Timeout(self._timeout, read=_EXPORT_READ_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.TimeoutException as exc:
            logger.warning('aiark.export.timeout', error=type(exc).__name__)
            return err('CONTACTS.TIMEOUT', detail='the contact lookup did not answer in time', cause=type(exc).__name__)
        except httpx.HTTPError as exc:
            logger.warning('aiark.export.transport_error', error=type(exc).__name__)
            return err(
                'CONTACTS.TRANSPORT', detail='the contact database could not be reached', cause=type(exc).__name__
            )
        return _decode_email(resp)

    # ── Company search ──────────────────────────────────────────────────────
    # The company-side twin of ``search_people`` / ``_build_search_body`` /
    # ``_post_search``. AI Ark's ``POST /v1/companies`` reuses the SAME
    # ``account.*`` filter surface as people search (domain, industry,
    # employeeSize RANGE, location, funding, keyword), so these methods mirror
    # the people ones one-for-one — same only-set-when-non-empty body contract,
    # same X-TOKEN + browser-UA transport, same ``CONTACTS.*`` error taxonomy —
    # differing only in the endpoint, the ``lookalikeDomains`` anchor and the
    # company row shape. Kept as a clearly separated section so the person and
    # company paths never share a symbol.

    async def search_companies(
        self,
        *,
        account_domains: list[str] | None = None,
        lookalike_domains: list[str] | None = None,
        account_industries: list[str] | None = None,
        account_locations: list[str] | None = None,
        account_keywords: list[str] | None = None,
        funding_types: list[str] | None = None,
        employee_size: tuple[int, int] | None = None,
        revenue_min: int | None = None,
        revenue_max: int | None = None,
        page: int = 0,
        size: int = 25,
    ) -> Result[AiArkCompanySearchResult]:
        """Search AI Ark for companies matching the filters (billed per row).

        Returns trimmed company rows. ``page`` is ZERO-based and ``size`` is
        clamped to :data:`MAX_PROVIDER_SIZE` (100), exactly like people search.

        ``lookalike_domains`` is AI Ark's "companies similar to these" anchor;
        it is clamped to :data:`MAX_LOOKALIKE_DOMAINS` (5, the documented
        ceiling). ``account_locations`` uses AI Ark's
        ``"{Continent} {Country} {City}"`` string format and is passed through
        verbatim. ``funding_types`` are the ``account.funding.type`` enum
        values (``SEED`` / ``SERIES_A`` …). ``employee_size`` is an inclusive
        ``(start, end)`` headcount range. ``revenue_min`` / ``revenue_max`` are
        annual revenue in USD, either bound optional.
        """
        if not self.is_configured:
            return err('CONTACTS.NOT_CONFIGURED', detail='the contact database is not configured')

        body = self._build_company_body(
            account_domains=account_domains,
            lookalike_domains=lookalike_domains,
            account_industries=account_industries,
            account_locations=account_locations,
            account_keywords=account_keywords,
            funding_types=funding_types,
            employee_size=employee_size,
            revenue_min=revenue_min,
            revenue_max=revenue_max,
            page=page,
            size=size,
        )
        result = await paid_call(
            'aiark.search_companies',
            lambda: self._post_company_search(body),
        )
        return result

    def _build_company_body(
        self,
        *,
        account_domains: list[str] | None,
        lookalike_domains: list[str] | None,
        account_industries: list[str] | None,
        account_locations: list[str] | None,
        account_keywords: list[str] | None,
        funding_types: list[str] | None,
        employee_size: tuple[int, int] | None,
        page: int,
        size: int,
        revenue_min: int | None = None,
        revenue_max: int | None = None,
    ) -> dict[str, Any]:
        # Same only-set-when-non-empty contract as ``_build_search_body``: an
        # absent filter must not appear in ``account`` at all, so AI Ark never
        # receives an empty ``include`` it might read as "match nothing".
        account: dict[str, Any] = {}
        if account_keywords:
            account['keyword'] = {
                'any': {
                    'include': {
                        'sources': [{'source': source, 'mode': 'SMART'} for source in _ACCOUNT_KEYWORD_SOURCES],
                        'content': list(account_keywords),
                    },
                },
            }
        if account_domains:
            account['domain'] = {'any': {'include': list(account_domains)}}
        if account_industries:
            account['industry'] = {'any': {'include': list(account_industries)}}
        if account_locations:
            account['location'] = {'any': {'include': list(account_locations)}}
        if funding_types:
            # Company-search-tier funding filters on the round type only; the
            # amount/duration ranges and per-round investors are export-only.
            account['funding'] = {'type': list(funding_types)}
        if employee_size is not None:
            start, end = employee_size
            account['employeeSize'] = {'type': 'RANGE', 'range': [{'start': start, 'end': end}]}
        revenue = _revenue_filter(revenue_min, revenue_max)
        if revenue:
            account['revenue'] = revenue

        body: dict[str, Any] = {
            'page': max(0, page),
            'size': min(MAX_PROVIDER_SIZE, max(1, size)),
        }
        if account:
            body['account'] = account
        if lookalike_domains:
            # Documented ceiling is 5; clamp rather than let AI Ark 400 the call.
            body['lookalikeDomains'] = list(lookalike_domains)[:MAX_LOOKALIKE_DOMAINS]
        return body

    async def _post_company_search(
        self,
        body: dict[str, Any],
    ) -> Result[AiArkCompanySearchResult]:
        url = f'{self._base_url}/v1/companies'
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.TimeoutException as exc:
            logger.warning('aiark.company_search.timeout', error=type(exc).__name__)
            return err(
                'CONTACTS.TIMEOUT',
                detail='the company search did not answer in time',
                cause=type(exc).__name__,
            )
        except httpx.HTTPError as exc:
            logger.warning('aiark.company_search.transport_error', error=type(exc).__name__)
            return err(
                'CONTACTS.TRANSPORT', detail='the contact database could not be reached', cause=type(exc).__name__
            )
        return _decode_company_search(resp, page=body['page'], size=body['size'])


# ── Module-level helpers + decoders ─────────────────────────────────────────


def _http_error_to_code(status: int) -> str:
    if status == 402:
        return 'CONTACTS.PAYMENT_REQUIRED'  # prepaid balance exhausted
    if status == 403:
        # ai-ark: Cloudflare blocks non-browser UA (verified live 2026-09) — a
        # 403 here is almost always the edge rejecting the request, not a data
        # verdict, so it reads as FORBIDDEN rather than NO_MATCH.
        return 'CONTACTS.FORBIDDEN'
    if status == 404:
        return 'CONTACTS.NO_MATCH'
    if status == 429:
        return 'CONTACTS.RATE_LIMITED'  # 5/s, 300/min, 18000/hr
    if 500 <= status < 600:
        return 'CONTACTS.UPSTREAM_5XX'
    return 'CONTACTS.HTTP_ERROR'


def _looks_like_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(('http://', 'https://')) or 'linkedin.com' in lowered


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int_or_none(value: Any) -> int | None:
    """A whole-number amount as ``int``, or ``None`` when absent/unparseable.

    AI Ark sends funding amounts as ints, but a stray string or float must not
    raise: an unparseable value reads as absent so the row simply omits it.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except TypeError, ValueError:
        return None


def _person_location(location: Any) -> str | None:
    """AI Ark's ``location`` object flattened to one display string.

    Prefers the vendor's own ``default`` line; falls back to composing
    ``city, state, country`` when only the parts are present. Empty strings
    are dropped, never rendered.
    """
    if not isinstance(location, dict):
        return None
    default = _str_or_none(location.get('default'))
    if default:
        return default
    parts: list[str] = []
    for key in ('city', 'state', 'country'):
        value = _str_or_none(location.get(key))
        if value and value not in parts:
            parts.append(value)
    return ', '.join(parts) or None


def _position_evidence(position_groups: Any) -> str | None:
    """First non-empty position description — the early-career evidence.

    AI Ark nests positions two ways: ``position_groups[].profile_positions[]``
    or fields directly on the group. Descriptions are frequently the empty
    string, so this walks in order and returns the first that carries text,
    or ``None`` when none do.
    """
    if not isinstance(position_groups, list):
        return None
    for group in position_groups:
        if not isinstance(group, dict):
            continue
        positions = group.get('profile_positions')
        if isinstance(positions, list):
            for position in positions:
                if isinstance(position, dict):
                    desc = _str_or_none(position.get('description'))
                    if desc:
                        return desc
        desc = _str_or_none(group.get('description'))
        if desc:
            return desc
    return None


def _skills(raw_skills: Any) -> tuple[str, ...]:
    """The person's skills as a tuple of non-empty strings, order preserved.

    AI Ark sends ``skills`` as a list that is usually plain strings but is
    occasionally a list of objects (``{"name": ...}``); this reads either and
    drops blanks. ``()`` when absent or malformed, so an Apollo row (which has
    no skills) and an AI Ark row with none look the same downstream.
    """
    if not isinstance(raw_skills, list):
        return ()
    out: list[str] = []
    for item in raw_skills:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = _str_or_none(item.get('name')) or ''
        else:
            value = ''
        if value:
            out.append(value)
    return tuple(out)


def _linkedin_url(raw: dict[str, Any]) -> str | None:
    """Prefer the full LinkedIn URL from ``link``; fall back to the slug."""
    link = raw.get('link')
    if isinstance(link, dict):
        url = _str_or_none(link.get('linkedin'))
        if url:
            return url
    return _str_or_none(raw.get('identifier'))


def _functions(department: dict[str, Any]) -> tuple[str, ...]:
    """AI Ark's ``department.functions`` as a tuple of non-empty strings.

    The VERIFIED function taxonomy (unlike Apollo's ``has_*`` presence flags);
    blanks dropped, order preserved. ``()`` when absent or malformed.
    """
    raw = department.get('functions')
    if not isinstance(raw, list):
        return ()
    return tuple(v for item in raw if (v := _str_or_none(item)))


def _top_education(educations: Any) -> tuple[str | None, str | None]:
    """The first education's ``(school_name, degree_name)`` — most recent first.

    Returns ``(None, None)`` when there is no education list or the first entry
    carries neither a school name nor a degree.
    """
    if not isinstance(educations, list) or not educations:
        return None, None
    first = educations[0]
    if not isinstance(first, dict):
        return None, None
    school = _dict_or_empty(first.get('school'))
    return _str_or_none(school.get('name')), _str_or_none(first.get('degree_name'))


def _funding(company: dict[str, Any]) -> tuple[str | None, int | None]:
    """Search-tier company funding ``(type, amount)`` off ``financial.funding``.

    ``amount`` prefers ``total_amount`` and falls back to ``last_amount``. No
    per-round investors exist at search tier, so this reads only the scalar
    type + amount. ``(None, None)`` when the block is absent.
    """
    financial = _dict_or_empty(company.get('financial'))
    funding = _dict_or_empty(financial.get('funding'))
    if not funding:
        return None, None
    amount = _int_or_none(funding.get('total_amount'))
    if amount is None:
        amount = _int_or_none(funding.get('last_amount'))
    return _str_or_none(funding.get('type')), amount


def _funding_detail(company: dict[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    """Export-tier funding round ``(date, investors)`` off ``financial.funding``.

    Extends :func:`_funding` (which reads the scalar type + amount) with the
    round DATE and the named investors when the payload carries them. Both are
    null-safe: ``(None, ())`` when the funding block is absent or carries
    neither, so a row with only the scalar funding stays unchanged.
    """
    financial = _dict_or_empty(company.get('financial'))
    funding = _dict_or_empty(financial.get('funding'))
    if not funding:
        return None, ()
    date = _date_display(funding.get('last_date') or funding.get('date'))
    return date, _name_list(funding.get('investors'))


def _name_list(raw: Any) -> tuple[str, ...]:
    """A list of names as a tuple of non-empty strings, order preserved.

    AI Ark sends these lists (industries, technologies, keywords, investors)
    sometimes as plain strings and sometimes as ``{"name": ...}`` objects; this
    reads either and drops blanks, exactly like :func:`_skills`. ``()`` when
    absent or malformed.
    """
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = _str_or_none(item.get('name')) or ''
        else:
            value = ''
        if value:
            out.append(value)
    return tuple(out)


def _date_display(value: Any) -> str | None:
    """A vendor date flattened to one display string.

    AI Ark carries dates either as a plain string or as a ``{year, month}``
    object. A ``{year, month}`` pair renders ``YYYY-MM``; a lone year renders
    the year; a string passes through trimmed. ``None`` when there is nothing
    parseable, so an absent date is simply omitted rather than rendered blank.
    """
    if isinstance(value, dict):
        year = _int_or_none(value.get('year'))
        month = _int_or_none(value.get('month'))
        if year and month and 1 <= month <= 12:
            return f'{year:04d}-{month:02d}'
        if year:
            return str(year)
        return None
    return _str_or_none(value)


def _employment_history(position_groups: Any) -> tuple[dict[str, Any], ...]:
    """The full employment history as ``{title, company, start, end}`` dicts.

    Where :func:`_position_evidence` keeps only the first description line, this
    keeps every position: each group's ``company.name`` and ``date`` span paired
    with each of its ``profile_positions[].title`` (falling back to a group-level
    title). Empty entries are dropped, so a group carrying nothing usable adds
    nothing. ``()`` when the block is absent or malformed.
    """
    if not isinstance(position_groups, list):
        return ()
    out: list[dict[str, Any]] = []
    for group in position_groups:
        if not isinstance(group, dict):
            continue
        company = _str_or_none(_dict_or_empty(group.get('company')).get('name'))
        date = _dict_or_empty(group.get('date'))
        start = _date_display(date.get('start'))
        end = _date_display(date.get('end'))
        titles: list[str] = []
        positions = group.get('profile_positions')
        if isinstance(positions, list):
            titles = [t for p in positions if isinstance(p, dict) if (t := _str_or_none(p.get('title')))]
        if not titles and (group_title := _str_or_none(group.get('title'))):
            titles = [group_title]
        # A group with no title still yields one entry (company + dates alone).
        titles_or_blank: list[str | None] = list(titles) or [None]
        for title in titles_or_blank:
            entry: dict[str, Any] = {}
            if title:
                entry['title'] = title
            if company:
                entry['company'] = company
            if start:
                entry['start'] = start
            if end:
                entry['end'] = end
            if entry:
                out.append(entry)
    return tuple(out)


def _current_positions(
    position_groups: Any,
) -> tuple[tuple[str, str | None, bool], ...]:
    """``(title, employer, own_span)`` for every position whose date span is
    still open: a start and no end.

    The position's own ``date`` decides, and the group's only when the position
    carries none — and then ``own_span`` is False, because a group spans every
    job the person held at that employer: a promotion's earlier title sits in a
    group whose span is still open without being current. The title check
    (``app.people.title_match``) trusts only an own span to call a second title
    at the row's employer in target. A position with no start date, on itself
    or on its group, is NOT current: this cannot tell it from a past one, and
    naming a past job as the one the filter matched would be the defect it
    exists to stop (#3459). The employer is the position's own ``company``
    string when it has one, the group's ``company.name`` otherwise. ``()`` when
    the block is absent or malformed.

    The position-level ``date`` is in the staging payload shape #3654 pins
    (#3652: ISO strings on the group AND on the position, in
    ``tests/unit/test_aiark_client.py``); the position-level ``company`` is
    only the reseller docs' shape for ``profile_positions[]``. A position
    without its own date reads as ``own_span=False``: a second title at the
    row's employer is then flagged instead of confirmed, never a flag lost.
    """
    if not isinstance(position_groups, list):
        return ()
    out: list[tuple[str, str | None, bool]] = []
    for group in position_groups:
        if not isinstance(group, dict):
            continue
        group_company = _str_or_none(_dict_or_empty(group.get('company')).get('name'))
        positions = group.get('profile_positions')
        entries = (
            [p for p in positions if isinstance(p, dict)] if isinstance(positions, list) and positions else [group]
        )
        for position in entries:
            title = _str_or_none(position.get('title')) or _str_or_none(group.get('title'))
            # A group with no ``profile_positions`` IS the position, so its
            # date is its own; only a position borrowing its group's is not.
            own_date = position.get('date')
            own_span = isinstance(own_date, dict) and bool(own_date)
            date = _dict_or_empty(own_date if own_span else group.get('date'))
            if not title or not _date_display(date.get('start')) or _date_display(date.get('end')):
                continue
            own = position.get('company')
            company = _str_or_none(own) if isinstance(own, str) else None
            out.append((title, company or group_company, own_span))
    return tuple(out)


def _educations(educations: Any) -> tuple[dict[str, Any], ...]:
    """The full education list as ``{school, degree, field_of_study, start,
    end}`` dicts.

    Where :func:`_top_education` keeps only the first school + degree, this keeps
    every entry. Empty entries are dropped. ``()`` when the block is absent or
    malformed.
    """
    if not isinstance(educations, list):
        return ()
    out: list[dict[str, Any]] = []
    for education in educations:
        if not isinstance(education, dict):
            continue
        date = _dict_or_empty(education.get('date'))
        entry: dict[str, Any] = {}
        if school := _str_or_none(_dict_or_empty(education.get('school')).get('name')):
            entry['school'] = school
        if degree := _str_or_none(education.get('degree_name')):
            entry['degree'] = degree
        if field := _str_or_none(education.get('field_of_study')):
            entry['field_of_study'] = field
        if start := _date_display(date.get('start')):
            entry['start'] = start
        if end := _date_display(date.get('end')):
            entry['end'] = end
        if entry:
            out.append(entry)
    return tuple(out)


def _certifications(certifications: Any) -> tuple[str, ...]:
    """Certifications as ``"name — authority"`` strings (or just the name).

    Reads ``raw['certifications'][].name`` and ``.authority``; an entry with no
    name is skipped. ``()`` when the block is absent or malformed.
    """
    if not isinstance(certifications, list):
        return ()
    out: list[str] = []
    for cert in certifications:
        if not isinstance(cert, dict):
            continue
        name = _str_or_none(cert.get('name'))
        if not name:
            continue
        authority = _str_or_none(cert.get('authority'))
        out.append(f'{name} — {authority}' if authority else name)
    return tuple(out)


def _staff(summary: dict[str, Any]) -> tuple[int | None, str | None]:
    """``(total, "start-end")`` from ``company.summary.staff``; either may be None."""
    staff = _dict_or_empty(summary.get('staff'))
    total = _int_or_none(staff.get('total'))
    band = _dict_or_empty(staff.get('range'))
    start, end = _int_or_none(band.get('start')), _int_or_none(band.get('end'))
    label = f'{start}-{end}' if start is not None and end is not None else (f'{start}+' if start is not None else None)
    return (total if total is not None and total >= 0 else None), label


def _months_in_role(raw: dict[str, Any]) -> int | None:
    """Whole months from the start in the current TITLE to today.

    ``statistics.experience.tenure.title`` is AI Ark's own date for that start,
    and it is the one to read: the first position group's ``date.start`` is when
    the person joined the COMPANY, which is not the same thing for anyone
    promoted in place. Measured live on staging (#3652): a person at the
    company since 2012-12 and in the title since 2024-12 read 165 months
    instead of 21. The group's first position is the fallback when the
    statistics block is absent, and the group start only after that.
    ``None`` when no start parses to a year, so an unknown tenure is omitted
    rather than guessed. A start in the future clamps to ``0``.
    """
    tenure = _dict_or_empty(
        _dict_or_empty(_dict_or_empty(raw.get('statistics')).get('experience')).get('tenure'),
    )
    groups = raw.get('position_groups')
    first = groups[0] if isinstance(groups, list) and groups and isinstance(groups[0], dict) else {}
    positions = first.get('profile_positions')
    position = positions[0] if isinstance(positions, list) and positions and isinstance(positions[0], dict) else {}
    for start in (
        tenure.get('title'),
        _dict_or_empty(position.get('date')).get('start'),
        _dict_or_empty(first.get('date')).get('start'),
    ):
        year, month = _year_month(start)
        if year is not None:
            today = datetime.now(UTC).date()
            return max(0, (today.year - year) * 12 + (today.month - (month or 1)))
    return None


def _year_month(value: Any) -> tuple[int | None, int | None]:
    """``(year, month)`` off a vendor date — a ``{year, month}`` object or an
    ISO-ish ``YYYY-MM`` / ``YYYY`` string. ``(None, None)`` when no year parses."""
    if isinstance(value, dict):
        return _int_or_none(value.get('year')), _int_or_none(value.get('month'))
    text = _str_or_none(value)
    if not text:
        return None, None
    parts = text.split('-')
    year = _int_or_none(parts[0])
    month = _int_or_none(parts[1]) if len(parts) > 1 else None
    return year, month


def _social_links(raw: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """``link``'s ``(twitter, github, facebook)`` URLs, each ``None`` if absent."""
    link = _dict_or_empty(raw.get('link'))
    return (
        _str_or_none(link.get('twitter')),
        _str_or_none(link.get('github')),
        _str_or_none(link.get('facebook')),
    )


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """A nested object as a dict, or ``{}`` when the vendor sent a non-object."""
    return value if isinstance(value, dict) else {}


#: What AI Ark sends and we never keep (#3652, RAW-FIELDS.md §6), matched as a
#: key AT ANY DEPTH: the birth date (a protected attribute; its year is a
#: placeholder anyway), test scores, third parties named under a project's
#: ``contributors``, and every personal contact channel. A search row carries no
#: email or phone today; naming them means a payload that starts carrying one,
#: anywhere, still never stores a channel a reveal is paid for.
_NEVER_STORED: Final = frozenset(
    {
        'birth_date',
        'test_scores',
        'contributors',
        'email',
        'emails',
        'personal_email',
        'personal_emails',
        'work_email',
        'phone',
        'phones',
        'phone_number',
        'phone_numbers',
        'mobile',
        'mobile_phone',
    }
)

#: The one exception to ``_NEVER_STORED``: ``company.contact.email`` /
#: ``.phone`` are the employer's switchboard and general mailbox — business
#: data about the COMPANY, not the person's own channel a reveal is paid for.
#: Matching every ``email``/``phone`` key inside ``company`` at any depth also
#: stripped this pair, which is what PR B fixes. Deliberately just these two
#: literal spellings, not the whole ``_NEVER_STORED`` list: a company contact
#: block has no business reason to carry ``mobile`` or ``personal_email``, so
#: those stay dropped everywhere, ``company`` included.
_COMPANY_CONTACT_CHANNELS: Final = frozenset({'email', 'phone'})


def _stored_payload(raw: Any, *, in_company: bool = False, in_contact: bool = False) -> Any:
    """``raw`` minus the never-store keys, at every depth, except
    ``company.contact.email`` / ``.phone`` (see :data:`_COMPANY_CONTACT_CHANNELS`)."""
    if isinstance(raw, dict):
        keep_channels = in_company and in_contact
        return {
            k: _stored_payload(v, in_company=in_company or k == 'company', in_contact=k == 'contact')
            for k, v in raw.items()
            if k not in _NEVER_STORED or (keep_channels and k in _COMPANY_CONTACT_CHANNELS)
        }
    if isinstance(raw, list):
        return [_stored_payload(v, in_company=in_company, in_contact=in_contact) for v in raw]
    return raw


def _decode_person(raw: dict[str, Any]) -> AiArkPerson:
    profile = _dict_or_empty(raw.get('profile'))
    company = _dict_or_empty(raw.get('company'))
    summary = _dict_or_empty(company.get('summary'))
    company_link = _dict_or_empty(company.get('link'))
    department = _dict_or_empty(raw.get('department'))
    badges = _dict_or_empty(raw.get('member_badges'))
    picture = _dict_or_empty(profile.get('picture'))
    school_name, degree_name = _top_education(raw.get('educations'))
    funding_type, funding_amount = _funding(company)
    funding_date, funding_investors = _funding_detail(company)
    twitter_url, github_url, facebook_url = _social_links(raw)
    # Industries / stack / keywords live on the company block (with a fallback
    # to its ``summary``, which is where the trimmed company row reads industry).
    org_industries = _name_list(company.get('industries') or summary.get('industries'))
    org_technologies = _name_list(company.get('technologies') or summary.get('technologies'))
    org_keywords = _name_list(company.get('keywords') or summary.get('keywords'))
    employee_count, employee_range = _staff(summary)

    return AiArkPerson(
        # PRESERVE the AI Ark id verbatim — it is the reveal/dedupe key.
        id=str(raw.get('id') or ''),
        first_name=str(profile.get('first_name') or ''),
        last_name=str(profile.get('last_name') or ''),
        title=str(profile.get('title') or profile.get('headline') or ''),
        full_name=_str_or_none(profile.get('full_name')),
        linkedin_url=_linkedin_url(raw),
        organization_name=_str_or_none(summary.get('name')),
        organization_domain=_str_or_none(company_link.get('domain')),
        organization_website_url=_str_or_none(company_link.get('website')),
        person_location=_person_location(raw.get('location')),
        position_evidence=_position_evidence(raw.get('position_groups')),
        skills=_skills(raw.get('skills')),
        seniority_verified=_str_or_none(department.get('seniority')),
        functions=_functions(department),
        open_to_work=bool(badges.get('open_to_work')),
        hiring=bool(badges.get('hiring')),
        creator=bool(badges.get('creator')),
        top_education_school=school_name,
        top_education_degree=degree_name,
        headline=_str_or_none(profile.get('headline')),
        picture_url=_str_or_none(picture.get('source')),
        company_funding_type=funding_type,
        company_funding_amount=funding_amount,
        twitter_url=twitter_url,
        github_url=github_url,
        facebook_url=facebook_url,
        employment_history=_employment_history(raw.get('position_groups')),
        current_positions=_current_positions(raw.get('position_groups')),
        educations=_educations(raw.get('educations')),
        certifications=_certifications(raw.get('certifications')),
        organization_industries=org_industries,
        organization_technologies=org_technologies,
        organization_keywords=org_keywords,
        months_in_role=_months_in_role(raw),
        raw=_stored_payload(raw),
        company_funding_date=funding_date,
        company_funding_investors=funding_investors,
        organization_employee_count=employee_count,
        organization_employee_range=employee_range,
    )


def _decode_search(
    resp: httpx.Response,
    *,
    page: int,
    size: int,
) -> Result[AiArkSearchResult]:
    if resp.status_code != 200:
        return err(
            _http_error_to_code(resp.status_code),
            detail=f'the contact search returned {resp.status_code}',
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')
    if not isinstance(payload, dict):
        return err('CONTACTS.UNEXPECTED_SHAPE', detail='search payload not an object')

    people = [_decode_person(raw) for raw in (payload.get('content') or []) if isinstance(raw, dict)]
    return ok(
        AiArkSearchResult(
            total_elements=int(payload.get('totalElements') or 0),
            total_pages=int(payload.get('totalPages') or 0),
            page=int(payload.get('number', page)),
            size=int(payload.get('size', size)),
            people=people,
            track_id=_str_or_none(payload.get('trackId')),
        )
    )


def _decode_email(resp: httpx.Response) -> Result[AiArkEmail]:
    if resp.status_code != 200:
        return err(
            _http_error_to_code(resp.status_code),
            detail=f'the contact lookup returned {resp.status_code}',
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')
    if not isinstance(payload, dict):
        return err('CONTACTS.UNEXPECTED_SHAPE', detail='reveal payload not an object')

    email = _dict_or_empty(payload.get('email'))
    output = email.get('output')
    entry = output[0] if isinstance(output, list) and output and isinstance(output[0], dict) else None
    address = _str_or_none(entry.get('address')) if entry else None
    # ``found: false`` or a missing/empty output is a matched record with no
    # deliverable address — a value, not an exception.
    if entry is None or not entry.get('found') or address is None:
        return err(
            'CONTACTS.NO_MATCH',
            detail='the contact lookup returned no deliverable email',
            person_id=_str_or_none(payload.get('id')),
        )

    return ok(
        AiArkEmail(
            address=address,
            status=_str_or_none(entry.get('status')),
            domain_type=_str_or_none(entry.get('domainType')),
            found=True,
            free=bool(entry.get('free')),
            generic=bool(entry.get('generic')),
            person_id=_str_or_none(payload.get('id')),
        )
    )


# ── Company payload shapes + decoders ───────────────────────────────────────
# A clearly separated section, symmetrical with the person shapes/decoders
# above but sharing NONE of their symbols. Reuses only the vendor-neutral
# helpers ``_str_or_none`` / ``_int_or_none`` / ``_dict_or_empty`` /
# ``_person_location`` / ``_http_error_to_code``.


@dataclass(frozen=True, slots=True)
class AiArkCompany:
    """A trimmed company row from ``POST /v1/companies``.

    ``id`` is AI Ark's uuid for the company and is PRESERVED verbatim: it is
    the join / dedupe / lists-anchor key, the same role ``id`` plays on
    :class:`AiArkPerson`.

    The rest is the company-search-tier signal the results table needs:
    identity (``name`` / ``domain`` / ``website`` / ``linkedin`` /
    ``crunchbase``), the ``industry`` + ``description`` + ``founded_year`` +
    ``company_type``, the headcount (``staff_total``), the scalar funding
    (``funding_type`` + ``funding_amount`` — company tier has type + amount,
    per-round investors are export-only), a display ``revenue_range`` and a
    flattened ``hq_location``. Each is null-safe: any may be missing or the
    wrong type in a real response and then stays at its default.
    """

    id: str
    name: str | None = None
    domain: str | None = None
    website: str | None = None
    linkedin: str | None = None
    crunchbase: str | None = None
    industry: str | None = None
    description: str | None = None
    founded_year: int | None = None
    company_type: str | None = None
    staff_total: int | None = None
    funding_type: str | None = None
    funding_amount: int | None = None
    revenue_range: str | None = None
    hq_location: str | None = None
    # Predict reads these two; they stay OUT of ``to_dict`` so the search
    # result the tools and the table see is unchanged. ``funding_rounds`` is
    # the whole ``financial.funding.rounds[]`` history as ``{type,
    # announced_at}`` (the scalar ``funding_type`` above is only the latest),
    # and ``hq_country`` the bare headquarters country.
    funding_rounds: tuple[dict[str, str], ...] = field(default=(), compare=False)
    hq_country: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'domain': self.domain,
            'website': self.website,
            'linkedin': self.linkedin,
            'crunchbase': self.crunchbase,
            'industry': self.industry,
            'description': self.description,
            'founded_year': self.founded_year,
            'company_type': self.company_type,
            'staff_total': self.staff_total,
            'funding_type': self.funding_type,
            'funding_amount': self.funding_amount,
            'revenue_range': self.revenue_range,
            'hq_location': self.hq_location,
        }


@dataclass(frozen=True, slots=True)
class AiArkCompanySearchResult:
    """Result of a company search: trimmed rows + totals + page metadata.

    The company twin of :class:`AiArkSearchResult`; there is no ``track_id``
    because the company path has no single-use reveal token to carry.
    """

    total_elements: int
    total_pages: int
    page: int
    size: int
    companies: list[AiArkCompany] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'total_elements': self.total_elements,
            'total_pages': self.total_pages,
            'page': self.page,
            'size': self.size,
            'companies': [c.to_dict() for c in self.companies],
        }


def _company_revenue_range(financial: dict[str, Any]) -> str | None:
    """A display ``revenue`` string off ``financial.revenue.annual``.

    Prefers the ``{start, end}`` bracket ("1000000-5000000"); falls back to a
    single ``amount``. ``None`` when the block is absent or carries no number,
    so the row simply omits revenue rather than rendering a half-empty range.
    """
    revenue = _dict_or_empty(financial.get('revenue'))
    annual = _dict_or_empty(revenue.get('annual'))
    if not annual:
        return None
    start = _int_or_none(annual.get('start'))
    end = _int_or_none(annual.get('end'))
    if start is not None and end is not None:
        return f'{start}-{end}'
    amount = _int_or_none(annual.get('amount'))
    if amount is not None:
        return str(amount)
    # Only one bound present — still worth surfacing rather than dropping.
    if start is not None:
        return str(start)
    if end is not None:
        return str(end)
    return None


def _company_hq_location(location: Any) -> str | None:
    """The company's HQ flattened to one display string.

    AI Ark nests it under ``location.headquarter`` with the same
    ``default`` / ``city`` / ``state`` / ``country`` shape a person's location
    uses, so this reuses ``_person_location`` on that subobject. ``None`` when
    there is no headquarter block.
    """
    headquarter = _dict_or_empty(location).get('headquarter') if isinstance(location, dict) else None
    return _person_location(headquarter)


def _decode_company(raw: dict[str, Any]) -> AiArkCompany:
    summary = _dict_or_empty(raw.get('summary'))
    link = _dict_or_empty(raw.get('link'))
    staff = _dict_or_empty(summary.get('staff'))
    financial = _dict_or_empty(raw.get('financial'))
    funding = _dict_or_empty(financial.get('funding'))

    return AiArkCompany(
        # PRESERVE the AI Ark id verbatim — it is the dedupe / lists-anchor key.
        id=str(raw.get('id') or ''),
        name=_str_or_none(summary.get('name')),
        domain=_str_or_none(link.get('domain')),
        website=_str_or_none(link.get('website')),
        linkedin=_str_or_none(link.get('linkedin')),
        crunchbase=_str_or_none(link.get('crunchbase')),
        industry=_str_or_none(summary.get('industry')),
        description=_str_or_none(summary.get('description')),
        founded_year=_int_or_none(summary.get('founded_year')),
        company_type=_str_or_none(summary.get('type')),
        staff_total=_int_or_none(staff.get('total')),
        funding_type=_str_or_none(funding.get('type')),
        funding_amount=_int_or_none(funding.get('total_amount')),
        revenue_range=_company_revenue_range(financial),
        hq_location=_company_hq_location(raw.get('location')),
        funding_rounds=_funding_rounds(funding.get('rounds')),
        hq_country=_str_or_none(
            _dict_or_empty(_dict_or_empty(raw.get('location')).get('headquarter')).get('country'),
        ),
    )


def _funding_rounds(rounds: Any) -> tuple[dict[str, str], ...]:
    """Every dated round as ``{type, announced_at}``; undated ones are dropped."""
    if not isinstance(rounds, list):
        return ()
    return tuple(
        {'type': _str_or_none(r.get('type')) or 'UNKNOWN', 'announced_at': announced}
        for r in rounds
        if isinstance(r, dict) and (announced := _str_or_none(r.get('announced_at')))
    )


def _decode_company_search(
    resp: httpx.Response,
    *,
    page: int,
    size: int,
) -> Result[AiArkCompanySearchResult]:
    if resp.status_code != 200:
        return err(
            _http_error_to_code(resp.status_code),
            detail=f'the company search returned {resp.status_code}',
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')
    if not isinstance(payload, dict):
        return err('CONTACTS.UNEXPECTED_SHAPE', detail='company payload not an object')

    companies = [_decode_company(raw) for raw in (payload.get('content') or []) if isinstance(raw, dict)]
    return ok(
        AiArkCompanySearchResult(
            total_elements=int(payload.get('totalElements') or 0),
            total_pages=int(payload.get('totalPages') or 0),
            page=int(payload.get('number', page)),
            size=int(payload.get('size', size)),
            companies=companies,
        )
    )
