"""Apollo.io response decoders: raw httpx responses to trimmed :mod:`app.people.apollo_models` shapes."""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.core.errors import Result, err, ok
from app.people.apollo_models import (
    CompanyDiscoveryResult,
    CompanyDiscoveryRow,
    JobPosting,
    JobPostingsResult,
    OrganizationEnriched,
    OrganizationSearchResult,
    PersonEnriched,
    PersonPreview,
    SearchResult,
    SuborganizationRef,
)
from app.people.entity_match import normalise_domain

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.core.errors import Err

logger = structlog.get_logger()


# ── Response decoders (kept module-private; pure functions for testability) ─


def _http_error_to_code(status: int) -> str:
    if status == 401:
        return 'CONTACTS.AUTH'
    if status == 402:
        return 'CONTACTS.PAYMENT_REQUIRED'  # out of credits / plan limit
    if status == 422:
        return 'CONTACTS.INVALID_INPUT'
    if status == 429:
        return 'CONTACTS.RATE_LIMITED'
    if 500 <= status < 600:
        return 'CONTACTS.UPSTREAM_5XX'
    return 'CONTACTS.HTTP_ERROR'


def _provider_error_code(resp: httpx.Response) -> str | None:
    """Apollo's own ``error_details.code`` (``DOMAIN.CATEGORY.REASON``), or
    None when the body carries none. Read from ``error_details`` rather than
    the root ``error`` string: Apollo documents the root fields as legacy,
    removed on 2027-02-16."""
    try:
        body = resp.json()
    except ValueError:
        return None
    details = body.get('error_details') if isinstance(body, dict) else None
    code = details.get('code') if isinstance(details, dict) else None
    return code if isinstance(code, str) else None


def _upstream_err(
    resp: httpx.Response,
    status_to_code: Callable[[int], str],
    *,
    detail: str,
    **context: Any,
) -> Err:
    """The one ``Err`` every Apollo decoder returns for a non-200.

    A spent plan is a funding failure whatever the status says. Apollo does
    not send the 402 the status maps expect: it sends a 422 whose
    ``error_details.code`` is in the BILLING domain
    (``BILLING.LIMIT.CREDITS_EXHAUSTED``), and read by status alone that was
    ``CONTACTS.INVALID_INPUT`` — users were told the person had no email
    while the provider never looked (#3398). Apollo documents no other
    BILLING code, so the whole domain counts: a future one is still about
    money, never about the request.

    On a funding failure the caller's detail and context are dropped: they
    may carry Apollo's own sentence, an upgrade link to our plan, which the
    model would repeat to the user. ``provider_code`` is kept on every
    failure so a log line can name the provider's verdict without the body.
    """
    provider_code = _provider_error_code(resp)
    if resp.status_code == 402 or (provider_code or '').startswith('BILLING.'):
        logger.error(
            'provider.access_unavailable',
            provider='apollo',
            cause='funding',
            status_code=resp.status_code,
            provider_code=provider_code,
        )
        return err(
            'CONTACTS.PAYMENT_REQUIRED',
            detail=(
                f'the contact database returned {resp.status_code}: the account is out of '
                'credits for this billing cycle'
            ),
            provider_code=provider_code,
        )
    return err(
        status_to_code(resp.status_code),
        detail=detail,
        provider_code=provider_code,
        **context,
    )


# Apollo expects locations in the format ``City, IT`` (English city +
# ISO country code). Italian locales like ``Milano`` or ``Milano,
# Italia`` 4xx silently — the LLM was previously retrying 3x with
# the same wrong format. The hint steers it to fix on first retry.
_APOLLO_LOCATION_HINT = (
    'Location format must be `"Milan, IT"` not `"Milano"` or '
    '`"Milano, Italia"`. A location must use English city name + '
    'ISO country code. Examples: "Milan, IT", "Rome, IT", "Berlin, DE", '
    '"Paris, FR".'
)


def _looks_like_location_error(body: str) -> bool:
    """Detect Apollo 4xx bodies that hint at a location-syntax issue.

    Apollo's error messages are inconsistent ("invalid location",
    "could not parse person_locations", etc.); checking a few
    substrings keeps the heuristic robust without over-fitting.
    """
    if not body:
        return False
    needle = body.lower()
    return bool(
        re.search(
            r'\b(location|person_locations|organization_locations)\b',
            needle,
        )
    )


#: The provider writes its surname mask with asterisks (``Zi***o``), and an
#: asterisk is a markdown control character in the one place this value is
#: ever read: the model's own prose. Prod session 71063133 delivered
#: ``**Alessandra Zir****o**`` — the mask's asterisks and the bold markers
#: pairing off against each other, leaving the reader a name with four
#: asterisks in the middle of it and no way to tell which part was hidden.
#: Nothing keys on the character (``row_key_recovery._squash`` drops every
#: non-alphanumeric before comparing, and ``entity_columns`` forbids this
#: field a column of its own), so the mask is written with a character
#: markdown has no opinion about instead.
MASK_CHAR = '\u00b7'


def _markdown_safe_mask(value: object) -> str:
    """The provider's surname mask with its markdown control characters
    swapped for a typographic one.

    Apollo occasionally returns a full surname in this preview field. Keep the
    preview contract at this boundary rather than letting provider variance
    expose data a reveal normally sells.
    """
    text = str(value or '')
    if MASK_CHAR not in text and '*' not in text and '_' not in text:
        # Below four characters the keep-two-and-the-last shape hides nothing:
        # at three it is the surname itself.
        if len(text) < 4:
            return MASK_CHAR * len(text)
        text = f'{text[:2]}{MASK_CHAR * (len(text) - 3)}{text[-1]}'
    for char in ('*', '_'):
        text = text.replace(char, MASK_CHAR)
    return text


def _decode_search(
    resp: httpx.Response,
    *,
    page: int,
    per_page: int,
) -> Result[SearchResult]:
    if resp.status_code != 200:
        body = resp.text[:200]
        detail = f'the contact search returned {resp.status_code}'
        kwargs: dict[str, Any] = {'body': body}
        # Educational hint when Apollo 4xx looks like a location-syntax
        # issue — the LLM was retrying with the same wrong format
        # ("Milano, Italia") 3x before giving up (staging sessions
        # d824f611 + a0a0c222, 2026-05-21). Surface the hint on the
        # error envelope so the model can self-correct on first retry.
        if 400 <= resp.status_code < 500 and _looks_like_location_error(body):
            detail = f'{detail}. {_APOLLO_LOCATION_HINT}'
            kwargs['hint'] = _APOLLO_LOCATION_HINT
            logger.info(
                'apollo.search.location_hint_attached',
                status=resp.status_code,
            )
        return _upstream_err(
            resp,
            _http_error_to_code,
            detail=detail,
            **kwargs,
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')

    if not isinstance(payload, dict):
        return err('CONTACTS.UNEXPECTED_SHAPE', detail='search payload not an object')

    raw_people = payload.get('people') or []
    previews: list[PersonPreview] = []
    for raw in raw_people:
        if not isinstance(raw, dict):
            continue
        # Search gates org down to name + has_* booleans on our plan; read
        # primary_domain anyway so a plan upgrade needs no change (#1349).
        org = raw.get('organization') or {}
        previews.append(
            PersonPreview(
                id=str(raw.get('id') or ''),
                first_name=str(raw.get('first_name') or ''),
                last_name_obfuscated=_markdown_safe_mask(
                    raw.get('last_name_obfuscated'),
                ),
                title=str(raw.get('title') or ''),
                seniority=raw.get('seniority') if isinstance(raw.get('seniority'), str) else None,
                organization_name=str(org.get('name') or '') if isinstance(org, dict) else '',
                has_email=bool(raw.get('has_email')),
                has_direct_phone=bool(_truthy_phone(raw.get('has_direct_phone'))),
                linkedin_url=_str_or_none(raw.get('linkedin_url')),
                organization_domain=(_str_or_none(org.get('primary_domain')) if isinstance(org, dict) else None),
                person_location=_person_location(raw),
                last_refreshed_at=_str_or_none(raw.get('last_refreshed_at')),
                organization_logo_url=(_str_or_none(org.get('logo_url')) if isinstance(org, dict) else None),
                has_city=bool(raw.get('has_city')),
                has_state=bool(raw.get('has_state')),
                has_country=bool(raw.get('has_country')),
                organization_has_industry=bool(org.get('has_industry')) if isinstance(org, dict) else False,
                organization_has_phone=bool(org.get('has_phone')) if isinstance(org, dict) else False,
                organization_has_city=bool(org.get('has_city')) if isinstance(org, dict) else False,
                organization_has_state=bool(org.get('has_state')) if isinstance(org, dict) else False,
                organization_has_country=bool(org.get('has_country')) if isinstance(org, dict) else False,
                organization_has_zip_code=bool(org.get('has_zip_code')) if isinstance(org, dict) else False,
                organization_has_revenue=bool(org.get('has_revenue')) if isinstance(org, dict) else False,
                organization_has_employee_count=(
                    bool(org.get('has_employee_count')) if isinstance(org, dict) else False
                ),
            )
        )

    return ok(
        SearchResult(
            total_entries=int(payload.get('total_entries') or 0),
            page=page,
            per_page=per_page,
            people=previews,
        )
    )


def _truthy_phone(value: Any) -> bool:
    """Apollo returns 'Yes'/'No'/None for has_direct_phone — normalize."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'yes', 'true', '1'}
    return False


def _person_location(raw: dict[str, Any]) -> str | None:
    """Flatten the optional Apollo place fields without treating flags as data.

    A preview on the current Apollo plan may carry ``has_city`` / ``has_country``
    but not the values themselves. Those booleans only describe availability;
    they must never turn into a fabricated location in an exported table.
    """
    parts: list[str] = []
    for location_key in ('city', 'state', 'country'):
        value = _str_or_none(raw.get(location_key))
        if value and value not in parts:
            parts.append(value)
    return ', '.join(parts) or None


def _employment_date(value: object) -> str | None:
    """One employment date at the precision Apollo actually has.

    The API answers with a full ISO date even when it only knows the year: an
    entry it dates to 2024 arrives as ``2024-01-01``. Rendering that day states
    a start we cannot support, and the invented days collide — every year-only
    role reads as the first of January. A January-1 date therefore collapses to
    its bare year. Someone who genuinely started on New Year's Day loses a day
    of precision, which is the cheaper error: the alternative fabricates one for
    everybody else. Any other day is a real observation and survives intact.
    """
    text = _str_or_none(value)
    if not text:
        return None
    day = text.split('T', 1)[0]
    return day[:-6] if day.endswith('-01-01') else day


def _employment_period(item: dict[str, Any]) -> str | None:
    """How long one role lasted, as a range when Apollo knows both ends.

    ``end_date`` is documented alongside ``start_date`` on every entry, and
    without it a history reads as a contradiction rather than a sequence — two
    roles that both begin in 2024 look like one person holding two jobs at
    once. An absent end stays silent instead of guessed: ``current`` already
    says which role is still running, and an open-ended range would claim the
    person never left the others.
    """
    start = _employment_date(item.get('start_date'))
    end = _employment_date(item.get('end_date'))
    if start and end:
        return f'{start}–{end}'
    if end:
        # An end with no start: the leading dash keeps it readable as "until
        # 2024" rather than being misread as the year the role began.
        return f'–{end}'
    return start


def _employment_history(raw: object) -> tuple[str, ...]:
    """Compact Apollo employment history for a table cell.

    One exportable string per role — ``Current``, title, employer, and the
    period it spans — so the cell reads down a column and survives a CSV round
    trip unchanged. The list is capped so a provider record with an unusually
    long history cannot crowd out the rest of a chat-tool result.

    ``organization_id`` is deliberately absent even though the payload carries
    it. It is Apollo's internal record id, it says nothing to whoever reads the
    cell, and Apollo holds no organisation record for every employer — so it
    showed on some rows and not others and made the column look broken rather
    than merely sparse. Nothing parses these strings positionally (the FE draws
    each one as a chip; the rerank prompts read them as prose), so the field
    order here is a display decision, not a contract.
    """
    if not isinstance(raw, list):
        return ()

    entries: list[str] = []
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        parts: list[str] = []
        if item.get('current') is True:
            parts.append('Current')
        for key in ('title', 'organization_name'):
            value = _str_or_none(item.get(key))
            if value:
                parts.append(value)
        period = _employment_period(item)
        if period:
            parts.append(period)
        if parts:
            entries.append(' · '.join(parts))
    return tuple(entries)


def _funding_events(raw: object) -> tuple[str, ...]:
    """Compact Apollo's round-by-round funding history for a table cell.

    Same treatment as :func:`_employment_history`, for the same reason: the
    documented fields of a round (type, date, amount + currency, investors)
    become one exportable string and the list is capped, so a company with a
    long history cannot crowd out the rest of a chat-tool result. The date is
    reduced to its calendar day — Apollo sends a full ISO timestamp whose time
    component is always midnight and never carries information.
    """
    if not isinstance(raw, list):
        return ()

    entries: list[str] = []
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        parts: list[str] = []
        round_type = _str_or_none(item.get('type'))
        if round_type:
            parts.append(round_type)
        date = _str_or_none(item.get('date'))
        if date:
            parts.append(date.split('T', 1)[0])
        amount = _str_or_none(item.get('amount'))
        if amount:
            currency = _str_or_none(item.get('currency'))
            parts.append(f'{currency}{amount}' if currency else amount)
        investors = _str_or_none(item.get('investors'))
        if investors:
            parts.append(investors)
        if parts:
            entries.append(' · '.join(parts))
    return tuple(entries)


def _technology_categories(raw: object) -> tuple[str, ...]:
    """The distinct categories behind Apollo's ``current_technologies``.

    ``organization_technologies`` already carries the flat vendor names from
    ``technology_names``; the categories are the only thing the richer array
    adds, so keep those and drop the names rather than shipping one list twice.
    """
    if not isinstance(raw, list):
        return ()

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        category = _str_or_none(item.get('category'))
        if category and category not in seen:
            seen.add(category)
            out.append(category)
    return tuple(out)


def _decode_match(resp: httpx.Response) -> Result[PersonEnriched]:
    if resp.status_code != 200:
        return _upstream_err(
            resp,
            _http_error_to_code,
            detail=f'the contact match returned {resp.status_code}',
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')

    if not isinstance(payload, dict):
        return err('CONTACTS.UNEXPECTED_SHAPE', detail='match payload not an object')

    person = payload.get('person')
    if not isinstance(person, dict):
        return err(
            'CONTACTS.NO_MATCH',
            detail='no one matched those parameters in the contact database',
        )

    org = person.get('organization') or {}
    if not isinstance(org, dict):
        org = {}

    return ok(
        PersonEnriched(
            id=str(person.get('id') or ''),
            first_name=str(person.get('first_name') or ''),
            last_name=str(person.get('last_name') or ''),
            title=str(person.get('title') or ''),
            seniority=person.get('seniority') if isinstance(person.get('seniority'), str) else None,
            email=_revealed_email_or_none(person.get('email')),
            email_status=_str_or_none(person.get('email_status')),
            linkedin_url=_str_or_none(person.get('linkedin_url')),
            organization_name=str(org.get('name') or ''),
            organization_domain=_str_or_none(org.get('primary_domain')),
            organization_id=(_str_or_none(person.get('organization_id')) or _str_or_none(org.get('id'))),
            organization_logo_url=_str_or_none(org.get('logo_url')),
            organization_industry=_str_or_none(org.get('industry')),
            organization_industries=tuple(_organization_industries(org)),
            organization_description=_str_or_none(
                org.get('short_description') or org.get('description'),
            ),
            organization_employee_count=_int_or_none(
                org.get('estimated_num_employees') or org.get('employee_count') or org.get('num_employees'),
            ),
            organization_annual_revenue=_int_or_none(
                org.get('annual_revenue') or org.get('organization_revenue'),
            ),
            organization_annual_revenue_printed=_str_or_none(
                org.get('annual_revenue_printed') or org.get('organization_revenue_printed'),
            ),
            organization_founded_year=_int_or_none(org.get('founded_year')),
            organization_headquarters_location=_organization_headquarters_location(org),
            organization_linkedin_url=_str_or_none(org.get('linkedin_url')),
            organization_technologies=tuple(
                _str_list(
                    org.get('technology_names') or org.get('technologies'),
                )
            ),
            organization_funding_stage=_str_or_none(org.get('latest_funding_stage')),
            organization_total_funding=_int_or_none(org.get('total_funding')),
            organization_total_funding_printed=_str_or_none(org.get('total_funding_printed')),
            organization_latest_funding_amount=_int_or_none(
                org.get('latest_funding_round_amount'),
            ),
            organization_latest_funding_amount_printed=_str_or_none(
                org.get('latest_funding_round_amount_printed'),
            ),
            organization_latest_funding_date=_str_or_none(
                org.get('latest_funding_round_date') or org.get('latest_funding_date'),
            ),
            organization_phone=_org_phone(org),
            organization_parent_id=_str_or_none(
                org.get('owned_by_organization_id') or org.get('parent_organization_id'),
            ),
            organization_suborganization_count=_int_or_none(org.get('num_suborganizations')),
            organization_website_url=_str_or_none(org.get('website_url')),
            organization_blog_url=_str_or_none(org.get('blog_url')),
            organization_angellist_url=_str_or_none(org.get('angellist_url')),
            organization_crunchbase_url=_str_or_none(org.get('crunchbase_url')),
            organization_twitter_url=_str_or_none(org.get('twitter_url')),
            organization_facebook_url=_str_or_none(org.get('facebook_url')),
            organization_alexa_ranking=_int_or_none(org.get('alexa_ranking')),
            organization_keywords=tuple(_label_list(org.get('keywords'))),
            organization_secondary_industries=tuple(_label_list(org.get('secondary_industries'))),
            organization_street_address=_str_or_none(org.get('street_address')),
            organization_postal_code=_str_or_none(org.get('postal_code')),
            organization_technology_categories=_technology_categories(
                org.get('current_technologies'),
            ),
            organization_funding_events=_funding_events(org.get('funding_events')),
            city=_str_or_none(person.get('city')),
            state=_str_or_none(person.get('state')),
            country=_str_or_none(person.get('country')),
            person_location=_person_location(person),
            photo_url=_str_or_none(person.get('photo_url')),
            headline=_str_or_none(person.get('headline')),
            twitter_url=_str_or_none(person.get('twitter_url')),
            github_url=_str_or_none(person.get('github_url')),
            facebook_url=_str_or_none(person.get('facebook_url')),
            extrapolated_email_confidence=_float_or_none(
                person.get('extrapolated_email_confidence'),
            ),
            is_likely_to_engage=(
                person.get('is_likely_to_engage') if isinstance(person.get('is_likely_to_engage'), bool) else None
            ),
            departments=tuple(_str_list(person.get('departments'))),
            subdepartments=tuple(_str_list(person.get('subdepartments'))),
            functions=tuple(_str_list(person.get('functions'))),
            employment_history=_employment_history(person.get('employment_history')),
            phone_status=_phone_enrichment_status(payload),
        )
    )


def _phone_enrichment_status(payload: dict[str, Any]) -> str | None:
    """Read ``phone_enrichment.status`` from a /people/match body.

    Present ('pending') only when a phone reveal was requested; absent on a
    plain reveal, in which case the phone job status is None.
    """
    enrichment = payload.get('phone_enrichment')
    if not isinstance(enrichment, dict):
        return None
    return _str_or_none(enrichment.get('status'))


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _revealed_email_or_none(value: Any) -> str | None:
    """Decode an Apollo ``email`` field, treating locked placeholders as absent.

    Apollo's People Match endpoint does not always return ``null`` for an
    unrevealable email — when a person is matched but the email cannot be
    unlocked (a locked record, or the shared credit pool is exhausted) it
    returns the sentinel ``email_not_unlocked@domain.com`` instead. That is
    not a real email, so we normalise it to ``None``: the empty-reveal guard
    then refunds (``has_contact_channel`` is False if LinkedIn is also
    missing) rather than charging the user for a placeholder, and we never
    surface the sentinel as if it were a contact. Real emails pass through
    unchanged.
    """
    email = _str_or_none(value)
    if email is None:
        return None
    local = email.split('@', 1)[0].strip().lower()
    if local == 'email_not_unlocked' or 'not_unlocked' in local:
        return None
    return email


# ── Organization endpoints: error mapping + decoders + helpers ──────────────


def _org_http_error_to_code(status: int) -> str:
    """Per the brief: org endpoints use a slightly different code surface
    than the people endpoints (UNAUTHORIZED vs AUTH, UPSTREAM_ERROR vs
    UPSTREAM_5XX) so adapters can distinguish them cleanly."""
    if status in (401, 403):
        return 'CONTACTS.UNAUTHORIZED'
    if status == 429:
        return 'CONTACTS.RATE_LIMITED'
    if 500 <= status < 600:
        return 'CONTACTS.UPSTREAM_ERROR'
    return 'CONTACTS.HTTP_ERROR'


# ``normalise_domain`` used to live here as ``_normalise_domain``, private
# and reached across a package boundary by a chat handler — the same shape
# that moved ``score_name_match`` out in #2001. It is now
# :mod:`app.people.entity_match`, beside the name comparison, and re-exported
# here so existing call sites keep working.
_normalise_domain = normalise_domain


def _decode_organization_search(
    resp: httpx.Response,
) -> Result[list[OrganizationSearchResult]]:
    if resp.status_code != 200:
        return _upstream_err(
            resp,
            _org_http_error_to_code,
            detail=f'the company search returned {resp.status_code}',
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')

    if not isinstance(payload, dict):
        return err(
            'CONTACTS.UNEXPECTED_SHAPE',
            detail='organization search payload not an object',
        )

    # /mixed_companies/search splits its rows across the same TWO arrays as
    # discovery: ``accounts`` (already saved) and ``organizations`` (net-new).
    # Resolution has to search BOTH — the ``or`` this replaces was a
    # preference, not a fusion: any net-new homonym made the saved company
    # unresolvable (#1998). Fusion order is ``_merge_discovery_arrays``'
    # relative rank, so neither array is systematically ahead and the
    # tie-break downstream inherits a defensible order.
    merged = list(
        _merge_discovery_arrays(
            payload.get('accounts'),
            payload.get('organizations'),
        )
    )
    # The merge drops nameless rows (``_usable_discovery_rows``) — right for
    # a discovery page, where a row that cannot be named cannot be shown,
    # but a nameless stub that still carries a DOMAIN is a valid exact-domain
    # resolution candidate, so those are re-appended after it. A row with
    # neither a name nor a domain is a candidate for no tier at all and stays
    # dropped: it could only ever pad the page.
    #
    # Their POSITION is not what keeps them out of the other tiers — on a page
    # whose named rows were all dropped, the tail is the head. What keeps them
    # out is that every other consumer requires the thing they lack: they
    # score 0.0 on any name, and ``_pick_match`` scores named rows only, so a
    # nameless row is a candidate for the exact-domain tier and for no other.
    for rows, saved in (
        (payload.get('accounts'), True),
        (payload.get('organizations'), False),
    ):
        merged.extend(
            (raw, saved)
            for raw in rows or []
            if isinstance(raw, dict)
            and not str(raw.get('name') or '').strip()
            and _str_or_none(raw.get('primary_domain') or raw.get('domain'))
        )

    out: list[OrganizationSearchResult] = []
    for raw, saved in merged:
        name = str(raw.get('name') or '').strip()
        domain = _str_or_none(raw.get('primary_domain') or raw.get('domain'))
        row_id = _org_row_id(raw, saved=saved)
        out.append(
            OrganizationSearchResult(
                id=row_id,
                name=name,
                already_saved=saved,
                domain=domain,
                industry=_str_or_none(raw.get('industry')),
                location=_format_org_location(raw),
                employee_count=_int_or_none(
                    raw.get('estimated_num_employees') or raw.get('employee_count') or raw.get('num_employees'),
                ),
                website=_str_or_none(raw.get('website_url') or raw.get('website')),
            )
        )
    return ok(out)


def _org_phone(raw: dict) -> str | None:
    """Company phone: flat ``phone`` first, else ``primary_phone.number``."""
    phone = _str_or_none(raw.get('phone'))
    if phone:
        return phone
    primary = raw.get('primary_phone')
    if isinstance(primary, dict):
        return _str_or_none(primary.get('number'))
    return None


def _org_sanitized_phone(raw: dict) -> str | None:
    """Company phone in E.164: flat ``sanitized_phone``, else
    ``primary_phone.sanitized_number``.

    The same two-shape rule as ``_org_phone`` directly above, and here for the
    same reason: ``/mixed_companies/search`` answers in two arrays, and the
    ``accounts`` records are not guaranteed to carry the flat key the
    ``organizations`` records do. A dialer-ready number that quietly empties on
    the saved rows of a page is a gap nothing downstream can see.
    """
    sanitized = _str_or_none(raw.get('sanitized_phone'))
    if sanitized:
        return sanitized
    primary = raw.get('primary_phone')
    if isinstance(primary, dict):
        return _str_or_none(primary.get('sanitized_number'))
    return None


def _usable_discovery_rows(rows: Any) -> list[dict[str, Any]]:
    """The rows of one Apollo array that the decoder will actually emit.

    Both discard rules live here, together, because they are ONE rule and the
    rank denominator below has to be this list rather than the raw array. An
    entry the decoder throws away must not shift the ranks of the rows that
    survive it: an ``accounts`` array carrying three nameless stubs ahead of a
    real row ranks that row at 4/5 instead of 1/2 and drops it to the tail of
    the page — the same inversion ``_merge_discovery_arrays`` exists to remove,
    arriving through a different door and just as invisibly.

    A nameless row is discarded exactly as unconditionally as a non-dict entry,
    so splitting the two checks across two functions sixty lines apart is what
    let the second one keep counting.
    """
    return [row for row in rows or [] if isinstance(row, dict) and str(row.get('name') or '').strip()]


_PLACEHOLDER_ORG_IDS = frozenset({'0', '-1', 'null', 'none', 'nil', 'unknown', 'n/a'})


def _non_placeholder(value: Any) -> str:
    """A provider id, or ``''`` when the provider answered a placeholder.

    A provider that cannot name a thing sometimes says ``"0"`` or ``"unknown"``
    rather than saying nothing, and every row it does that on then carries the
    SAME id. ``_search_cache.discovery_row_key`` prefers ``id``, so a union
    would fold all of them into one row and silently drop the rest of a page
    the user was already billed for. Reading the placeholder as absent sends
    those rows down the domain-then-name path, which keeps distinct companies
    distinct.

    Applied to BOTH ids a discovery row carries. The CRM account id is not
    decoration: ``discovery_row_keys`` mints the migration alias from it, so a
    placeholder there collides at every membership site exactly as one in
    ``id`` would.
    """
    text = str(value or '').strip()
    return '' if text.casefold() in _PLACEHOLDER_ORG_IDS else text


def _org_row_id(raw: dict[str, Any], *, saved: bool) -> str:
    """The ORGANIZATION id of a /mixed_companies/search row, whichever array it
    arrived in (#2025, #2039).

    An ``accounts`` row keys ``id`` in the CRM keyspace and carries the
    organization under ``organization_id``; an ``organizations`` row carries the
    organization id in ``id`` and no ``organization_id`` at all (live probe
    2026-08-18: 7/7 and 93/93). A saved row with no usable ``organization_id``
    yields ``''`` rather than the CRM id, which is a key into a DIFFERENT table:
    the row stays usable for the domain and name driven paths, and an id-driven
    caller fails loudly and for free instead of matching nothing quietly.

    Declared ONCE and called by both decoders. The rule was restated by hand in
    each of them, which is how they came to disagree in the first place.

    A placeholder spelling counts as no id (:func:`_non_placeholder`).
    """
    return _non_placeholder(raw.get('organization_id' if saved else 'id'))


def _merge_discovery_arrays(
    accounts: Any,
    organizations: Any,
) -> list[tuple[dict[str, Any], bool]]:
    """Fuse the two arrays of /mixed_companies/search into ONE ordered page.

    Apollo defines an order INSIDE each array — relevance, or the ``sort_by``
    the caller asked for — and none ACROSS them. Concatenating the two invents
    one, and the order it invents is ``already_saved`` descending: the
    companies the team already has hold the head of the page by construction.
    The fit sort downstream then leaves them there, correctly, because it may
    only rank rows it can compare (#1913) — so a DISCOVERY tool opened on the
    companies the user already owns, three fixes running (#1881, #1908).

    Nothing in the row can undo that downstream. Live probe 2026-08-13 on the
    incident query: ``city``/``state``/``country`` on 7/7 ``accounts`` and
    **0/18** ``organizations``, ``estimated_num_employees`` absent on all 25,
    ``industry`` dormant since #1760. Location is the only criterion that can
    score at all and it exists only where ``already_saved`` is true, so
    "scored" means "already in the workspace" (#1891 owns whether an arm can
    be revived).

    Rows are therefore merged on their RELATIVE rank within their own array,
    ``(i + 1) / (n + 1)``. Each array keeps its vendor order, neither is
    systematically ahead, and the window the model reads carries each array in
    its true proportion: the 7 saved rows of that page land at positions 3, 6,
    10, 13, 16, 20 and 23 instead of 1-7.

    Relative rank is also as close to a server-side ``sort_by`` as this
    function can get, but it is NOT exact and the difference is worth naming.
    ``sort_by`` accepts two fields (``DISCOVERY_SORT_FIELDS``). ``employees``
    cannot be re-applied locally at all — the probe found no headcount key on
    any row. ``founded_year`` could be, since the decoder reads it, but the
    decoder is handed a response and never the sort the caller asked for, so
    both arms keep vendor rank instead. A ``founded_year`` page therefore still
    zigzags where the two arrays interleave, exactly as it did when they were
    concatenated. Threading the sort criteria down here would fix that arm
    properly; it is a separate change from this one.

    Cross-array duplicates need no handling here, and that is MEASURED rather
    than assumed — live probe 2026-08-18, the incident query, one billed page of
    100 rows (7 accounts, 93 organizations): the two arrays overlap on nothing,
    0 shared values on ``id``, on domain and on name. An ``accounts`` record
    carries BOTH ``id`` (the CRM account id, minted when the team saved the
    company) and ``organization_id`` (7/7 each), while an ``organizations``
    record carries only ``id`` (93/93, ``organization_id`` 0/93).

    Until #2039 that shape ALSO meant an overlap could not be collapsed even if
    it happened: the decoder passed ``id`` through, so two copies of one company
    arrived under two different values and ``_search_cache.discovery_row_key``,
    which prefers ``id``, kept them apart. #2039 normalises both arrays onto the
    ORGANIZATION id, so the collapse is now possible and this ordering DOES
    decide a first-wins winner. Which is why the order below is stated rather
    than left to be discovered:

    **The ``organizations`` copy wins.** The union fold keeps the row it sees
    first (``_search_cache.merge_discovery_rows``) and the tie-break below sorts
    net-new ahead of saved at equal rank. The dropped ``accounts`` copy is the
    RICHER of the two: it is the only one carrying ``city``/``state``/``country``
    (7/7 against 0/18 on the probe) and the only one with ``already_saved=True``.
    So a company the team already saved would render as net-new and without a
    location.

    The tie-break is NOT flipped to answer that, deliberately. It orders
    DISTINCT companies, where #1881 and #1908 ruled net-new first on purpose,
    and flipping it globally would reverse that ruling on every genuine tie to
    pay for a case the probe measures at zero and the live canary watches. The
    cost is named here instead, and the day the canary in
    ``tests/contract/test_company_discovery_live.py`` goes red the answer is a
    duplicate-aware fusion at that point, not a silent reordering now.

    Do not answer a future overlap with a dedup HERE. Dropping a row from a
    page is a contract break rather than a local decision: ``_fetch_all_pages``
    reads ``len(rows) < per_page`` as "the provider ran out", so one row fewer
    on a full page ends the walk after a single page and reports the partial
    set as complete — silently, and with no test able to see it. The canary for
    the disjointness this rests on is in
    ``tests/contract/test_company_discovery_live.py``.

    ``already_saved`` is read here as PROVENANCE — which array the row arrived
    in — and never as merit. Ties resolve to net-new: a discovery tool shows
    the row you do not have.

    That rule is this function's, NOT its callers'. Since #1998 the org-search
    decoder shares this merge, and the resolution path on top of it
    (``_pick_match`` in ``chat/tools/handlers/companies_jobs.py``) breaks
    exact-name ties the opposite way, toward the saved row: showing a page and
    picking one company are different questions, and there the company the
    team already saved is the best prior for which homonym was meant. Neither
    caller may assume the other's polarity, and this order is a fair
    interleave, never a relevance ranking across the two arrays.
    """
    ranked: list[tuple[float, int, dict[str, Any], bool]] = []
    for rows, saved in ((organizations, False), (accounts, True)):
        usable = _usable_discovery_rows(rows)
        for index, raw in enumerate(usable):
            ranked.append(
                (
                    (index + 1) / (len(usable) + 1),
                    int(saved),
                    raw,
                    saved,
                )
            )
    # Sorting on the pair keeps the tie-break explicit rather than resting on
    # the order the loop above happens to visit the two arrays in.
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [(raw, saved) for _, _, raw, saved in ranked]


def _decode_company_discovery(
    resp: httpx.Response,
) -> Result[CompanyDiscoveryResult]:
    """Decode /mixed_companies/search. The response splits rows across TWO
    arrays: ``accounts`` (companies the team already saved) and
    ``organizations`` (net-new) — a page of 100 can be any mix. Dropping
    either array silently under-delivers the page the credit paid for, and
    the order the two are fused in is a selection decision in its own right —
    see ``_merge_discovery_arrays``.
    """
    if resp.status_code != 200:
        # Surface the upstream error message when the body carries one
        # ("Invalid sort criteria" on a 422): the detail is what the agent
        # loop shows the model, and the named reason lets it self-correct
        # instead of retrying blind on "returned 422".
        upstream_msg = ''
        try:
            body = resp.json()
            if isinstance(body, dict):
                upstream_msg = str(body.get('error') or '')[:150]
        except ValueError:
            pass
        return _upstream_err(
            resp,
            _org_http_error_to_code,
            detail=(
                f'the company discovery returned {resp.status_code}' + (f': {upstream_msg}' if upstream_msg else '')
            ),
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')

    if not isinstance(payload, dict):
        return err(
            'CONTACTS.UNEXPECTED_SHAPE',
            detail='company discovery payload not an object',
        )
    if 'error' in payload:
        return err(
            'CONTACTS.INVALID_INPUT',
            detail=str(payload.get('error'))[:200],
        )

    pagination = payload.get('pagination') or {}
    rows: list[CompanyDiscoveryRow] = []
    for raw, saved in _merge_discovery_arrays(
        payload.get('accounts'),
        payload.get('organizations'),
    ):
        # Non-dicts and nameless rows are already gone: dropping them here
        # instead would leave them counted in the rank denominator that
        # decides this row's position. See ``_usable_discovery_rows``.
        name = str(raw.get('name') or '').strip()
        rows.append(
            CompanyDiscoveryRow(
                id=_org_row_id(raw, saved=saved),
                crm_account_id=((_non_placeholder(raw.get('id')) or None) if saved else None),
                name=name,
                domain=_str_or_none(raw.get('primary_domain') or raw.get('domain')),
                linkedin_url=_str_or_none(raw.get('linkedin_url')),
                revenue_printed=_str_or_none(raw.get('organization_revenue_printed')),
                founded_year=_int_or_none(raw.get('founded_year')),
                phone=_org_phone(raw),
                already_saved=saved,
                employee_count=_int_or_none(
                    raw.get('estimated_num_employees') or raw.get('employee_count') or raw.get('num_employees'),
                ),
                industry=_str_or_none(raw.get('industry')),
                location=_format_org_location(raw),
                logo_url=_str_or_none(raw.get('logo_url')),
                website_url=_str_or_none(raw.get('website_url')),
                twitter_url=_str_or_none(raw.get('twitter_url')),
                facebook_url=_str_or_none(raw.get('facebook_url')),
                blog_url=_str_or_none(raw.get('blog_url')),
                angellist_url=_str_or_none(raw.get('angellist_url')),
                crunchbase_url=_str_or_none(raw.get('crunchbase_url')),
                alexa_ranking=_int_or_none(raw.get('alexa_ranking')),
                languages=tuple(_str_list(raw.get('languages'))),
                linkedin_uid=_str_or_none(raw.get('linkedin_uid')),
                publicly_traded_symbol=_str_or_none(raw.get('publicly_traded_symbol')),
                publicly_traded_exchange=_str_or_none(raw.get('publicly_traded_exchange')),
                sanitized_phone=_org_sanitized_phone(raw),
                owned_by_organization_id=_str_or_none(raw.get('owned_by_organization_id')),
            )
        )
    return ok(
        CompanyDiscoveryResult(
            total_entries=_int_or_none(pagination.get('total_entries')) or 0,
            page=_int_or_none(pagination.get('page')) or 1,
            per_page=_int_or_none(pagination.get('per_page')) or len(rows),
            companies=rows,
        )
    )


def _decode_organization_enrich(
    resp: httpx.Response,
) -> Result[OrganizationEnriched]:
    if resp.status_code == 404:
        return err(
            'CONTACTS.ORG_NOT_FOUND',
            detail='the company database has no organization for that domain',
        )
    if resp.status_code != 200:
        return _upstream_err(
            resp,
            _org_http_error_to_code,
            detail=f'the company enrichment returned {resp.status_code}',
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')

    if not isinstance(payload, dict):
        return err(
            'CONTACTS.UNEXPECTED_SHAPE',
            detail='enrich payload not an object',
        )

    org = payload.get('organization')
    if not isinstance(org, dict):
        # Apollo returns 200 with organization=null for unknown domains on
        # some plan tiers — treat the same as a 404.
        return err(
            'CONTACTS.ORG_NOT_FOUND',
            detail='the company database has no organization for that domain',
        )

    return ok(
        OrganizationEnriched(
            id=str(org.get('id') or ''),
            name=str(org.get('name') or ''),
            domain=str(
                org.get('primary_domain') or org.get('domain') or '',
            ),
            industry=_str_or_none(org.get('industry')),
            sic_codes=_str_list(org.get('sic_codes')),
            naics_codes=_str_list(org.get('naics_codes')),
            employee_count=_int_or_none(
                org.get('estimated_num_employees') or org.get('employee_count') or org.get('num_employees'),
            ),
            founded_year=_int_or_none(org.get('founded_year')),
            website=_str_or_none(org.get('website_url') or org.get('website')),
            technologies=_str_list(
                org.get('technology_names') or org.get('technologies'),
            ),
            short_description=_str_or_none(
                org.get('short_description') or org.get('description'),
            ),
            industries=_organization_industries(org),
            logo_url=_str_or_none(org.get('logo_url')),
            linkedin_url=_str_or_none(org.get('linkedin_url')),
            phone=_org_phone(org),
            annual_revenue=_int_or_none(
                org.get('annual_revenue') or org.get('organization_revenue'),
            ),
            annual_revenue_printed=_str_or_none(
                org.get('annual_revenue_printed') or org.get('organization_revenue_printed'),
            ),
            headquarters_location=_organization_headquarters_location(org),
            funding_stage=_str_or_none(org.get('latest_funding_stage')),
            total_funding=_int_or_none(org.get('total_funding')),
            total_funding_printed=_str_or_none(org.get('total_funding_printed')),
            latest_funding_amount=_int_or_none(org.get('latest_funding_round_amount')),
            latest_funding_amount_printed=_str_or_none(
                org.get('latest_funding_round_amount_printed'),
            ),
            latest_funding_date=_str_or_none(
                org.get('latest_funding_round_date') or org.get('latest_funding_date'),
            ),
            parent_id=_str_or_none(
                org.get('owned_by_organization_id') or org.get('parent_organization_id'),
            ),
            suborganization_count=_int_or_none(org.get('num_suborganizations')),
            blog_url=_str_or_none(org.get('blog_url')),
            angellist_url=_str_or_none(org.get('angellist_url')),
            twitter_url=_str_or_none(org.get('twitter_url')),
            facebook_url=_str_or_none(org.get('facebook_url')),
            crunchbase_url=_str_or_none(org.get('crunchbase_url')),
            sanitized_phone=_org_sanitized_phone(org),
            languages=tuple(_str_list(org.get('languages'))),
            alexa_ranking=_int_or_none(org.get('alexa_ranking')),
            linkedin_uid=_str_or_none(org.get('linkedin_uid')),
            publicly_traded_symbol=_str_or_none(org.get('publicly_traded_symbol')),
            publicly_traded_exchange=_str_or_none(org.get('publicly_traded_exchange')),
            keywords=tuple(_label_list(org.get('keywords'))),
            secondary_industries=tuple(_label_list(org.get('secondary_industries'))),
            street_address=_str_or_none(org.get('street_address')),
            city=_str_or_none(org.get('city') or org.get('primary_city')),
            state=_str_or_none(org.get('state') or org.get('primary_state')),
            postal_code=_str_or_none(org.get('postal_code')),
            country=_str_or_none(org.get('country') or org.get('primary_country')),
            raw_address=_str_or_none(org.get('raw_address')),
            seo_description=_str_or_none(org.get('seo_description')),
            funding_events=_funding_events(org.get('funding_events')),
            departmental_head_count=_departmental_head_count(
                org.get('departmental_head_count'),
            ),
            suborganizations=_suborganizations(org.get('suborganizations')),
            headcount_six_month_growth=_float_or_none(
                org.get('organization_headcount_six_month_growth'),
            ),
            headcount_twelve_month_growth=_float_or_none(
                org.get('organization_headcount_twelve_month_growth'),
            ),
            headcount_twenty_four_month_growth=_float_or_none(
                org.get('organization_headcount_twenty_four_month_growth'),
            ),
        )
    )


# Rows shown to the LLM per page. Apollo's job_postings response for a large
# employer (e.g. 132 rows for a mid-size company) is small enough per-row to
# not need session-cache slicing like discover_companies — a flat cap keeps
# the tool-result payload predictable without adding that machinery here.
_MAX_JOB_POSTINGS_ROWS = 25


def _decode_job_postings(
    resp: httpx.Response,
    *,
    org_name: str,
) -> Result[JobPostingsResult]:
    if resp.status_code == 404:
        return err(
            'CONTACTS.ORG_NOT_FOUND',
            detail='the company database has no job postings for that organization id',
        )
    if resp.status_code != 200:
        return _upstream_err(
            resp,
            _org_http_error_to_code,
            detail=f'the job postings lookup returned {resp.status_code}',
            body=resp.text[:200],
        )
    try:
        payload = resp.json()
    except ValueError:
        return err('CONTACTS.INVALID_JSON', detail='the contact database returned a non-JSON body')

    if not isinstance(payload, dict):
        return err(
            'CONTACTS.UNEXPECTED_SHAPE',
            detail='job postings payload not an object',
        )

    raw_postings = payload.get('organization_job_postings') or []
    postings: list[JobPosting] = []
    for raw in raw_postings:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get('title') or '').strip()
        if not title:
            continue
        postings.append(
            JobPosting(
                title=title,
                city=_str_or_none(raw.get('city')),
                country=_str_or_none(raw.get('country')),
                url=_str_or_none(raw.get('url')),
                posted_at=_str_or_none(raw.get('posted_at')),
            )
        )
        if len(postings) >= _MAX_JOB_POSTINGS_ROWS:
            break

    pagination = payload.get('pagination') or {}
    total = _int_or_none(
        pagination.get('total_entries') if isinstance(pagination, dict) else None,
    )
    if total is None:
        total = len(raw_postings)

    return ok(
        JobPostingsResult(
            org_name=org_name,
            total=total,
            postings=postings,
        )
    )


def _format_org_location(raw: dict[str, Any]) -> str | None:
    """Apollo splits HQ into city/country fields; format as 'City, Country'."""
    city = _str_or_none(raw.get('city') or raw.get('primary_city'))
    country = _str_or_none(raw.get('country') or raw.get('primary_country'))
    parts = [p for p in (city, country) if p]
    return ', '.join(parts) if parts else None


def _organization_headquarters_location(raw: dict[str, Any]) -> str | None:
    """Return Apollo's exact HQ address, or its city/state/country form."""
    raw_address = _str_or_none(raw.get('raw_address'))
    if raw_address:
        return raw_address

    parts = [
        _str_or_none(raw.get('city') or raw.get('primary_city')),
        _str_or_none(raw.get('state') or raw.get('primary_state')),
        _str_or_none(raw.get('country') or raw.get('primary_country')),
    ]
    unique = list(dict.fromkeys(part for part in parts if part))
    return ', '.join(unique) if unique else None


def _label_list(values: Any) -> list[str]:
    """Normalize an Apollo label array to deduped strings, order preserved.

    Tolerates the three shapes Apollo uses across these arrays — a list of
    plain strings, a list of ``{'name': ...}`` objects, and a single bare
    string — without inventing a taxonomy label for any of them.
    """
    if isinstance(values, str):
        value = _str_or_none(values)
        return [value] if value else []
    if not isinstance(values, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        value = _str_or_none(item.get('name')) if isinstance(item, dict) else _str_or_none(item)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _organization_industries(raw: dict[str, Any]) -> list[str]:
    """Normalize Apollo's industry list without inventing a taxonomy label."""
    return _label_list(raw.get('industries') or raw.get('industry_names'))


#: How many subsidiaries the payload lists. A cap for the same reason
#: ``_funding_events`` has one — a conglomerate must not crowd out the rest of a
#: chat-tool result — and safe to impose because ``suborganization_count``
#: carries the vendor's own total, so a truncated list never reads as the
#: complete one.
_MAX_SUBORGANIZATIONS = 25


def _suborganizations(raw: object) -> tuple[SuborganizationRef, ...]:
    """Apollo's ``suborganizations`` array as identifying references.

    An entry survives only if it can be identified — a stub with neither an id
    nor a name names no company, and a row of empty strings in a subsidiary
    list reads as a subsidiary nobody bothered to name rather than as the
    nothing it is.
    """
    if not isinstance(raw, list):
        return ()

    out: list[SuborganizationRef] = []
    for item in raw[:_MAX_SUBORGANIZATIONS]:
        if not isinstance(item, dict):
            continue
        sub_id = _str_or_none(item.get('id'))
        name = _str_or_none(item.get('name'))
        if not sub_id and not name:
            continue
        out.append(
            SuborganizationRef(
                id=sub_id or '',
                name=name or '',
                website_url=_str_or_none(
                    item.get('website_url') or item.get('primary_domain'),
                ),
            )
        )
    return tuple(out)


def _departmental_head_count(raw: object) -> dict[str, int]:
    """Apollo's per-department headcount, kept as a mapping of name to count.

    ``{'engineering': 228, 'operations': 28, 'sales': 177}`` on the wire. Kept
    as numbers rather than flattened into one string like the funding history
    is: these are figures a caller sums, sorts and charts, and a reader that has
    to re-parse them is a reader that will re-parse them wrong.

    An entry survives only when its value decodes to an integer — a department
    the vendor sends as ``null`` or as a string it did not mean numerically is
    dropped rather than coerced to a headcount nobody reported. A genuine ``0``
    is kept: that is Apollo saying the department is empty, which is data.
    """
    if not isinstance(raw, dict):
        return {}

    out: dict[str, int] = {}
    for key, value in raw.items():
        name = _str_or_none(key)
        count = _int_or_none(value)
        if name and count is not None:
            out[name] = count
    return out


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):  # bool subclasses int — guard explicitly
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN / Inf raise ValueError under ``int(...)``; treat as missing.
        if not math.isfinite(value):
            return None
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
        if not math.isfinite(f):
            return None
        return int(f)
    return None


def _float_or_none(value: Any) -> float | None:
    """Coerce an Apollo confidence score, keeping 0.0 distinct from missing."""
    if value is None:
        return None
    if isinstance(value, bool):  # bool subclasses int — guard explicitly
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v not in (None, '')]


# ``score_name_match`` used to live here as ``_score_search_match``, private
# and yet imported from another package. It is now
# :mod:`app.people.entity_match`, next to the comparison built on top of it.
