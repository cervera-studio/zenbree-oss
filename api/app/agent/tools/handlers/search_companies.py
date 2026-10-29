"""search_companies: Apollo company search, or Google Maps (Serper Places) for local businesses."""

from __future__ import annotations

import asyncio

from app.admin.settings import vendor_disabled_now
from app.agent.funnel import applied_filters
from app.agent.tools import search_cache
from app.agent.tools.handlers._rows import DISABLED, city_country, err_text
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.billing.paid_call import current_user_id, record_vendor_call
from app.billing.vendors import SERPER
from app.core.errors import Result, is_err
from app.people.aiark_client import AiArkClient, AiArkCompany
from app.people.apollo_client import ApolloClient
from app.people.apollo_models import (
    CompanyDiscoveryResult,
    CompanyDiscoveryRow,
    resolve_industry_tag_ids,
    unknown_industries,
)
from app.people.entity_match import normalise_domain
from app.scraping.adapters.serper_places import search_places

# Fewer Apollo matches than this on page 1 → point the model at the Google Maps fallback (#52).
LOCAL_FALLBACK_MIN = 10


def company_row(c: CompanyDiscoveryRow) -> dict:
    city, country = city_country(c.location)
    return {
        'name': c.name,
        'domain': normalise_domain(c.domain or c.website_url or ''),
        'website': c.website_url,
        'linkedin_url': c.linkedin_url,
        'industry': c.industry,
        'employees': c.employee_count,
        'city': city,
        'country': country,
        'description': None,
        'source': 'apollo',
        'apollo_id': c.id,
        'phone': c.phone,
    }


def place_row(p: dict) -> dict:
    _, country = city_country(p.get('address'))
    return {
        'name': p['name'],
        'domain': normalise_domain(p.get('website') or ''),
        'website': p.get('website') or None,
        'linkedin_url': None,
        'industry': p.get('category') or None,
        'employees': None,
        'city': None,
        'country': country,
        'description': None,
        'source': 'google_maps',
        'address': p.get('address') or None,
        'phone': p.get('phone') or None,
        'rating': p.get('rating'),
        'review_count': p.get('review_count'),
        'maps_url': p.get('maps_url') or None,
    }


def aiark_company_row(c: AiArkCompany) -> dict:
    city, country = city_country(c.hq_location)
    return {
        'name': c.name,
        'domain': normalise_domain(c.domain or c.website or ''),
        'website': c.website,
        'linkedin_url': c.linkedin,
        'industry': c.industry,
        'employees': c.staff_total,
        'city': city,
        'country': country,
        'description': c.description,
        'source': 'aiark',
        'company_id': c.id,
        'funding_type': c.funding_type,
        'funding_amount': c.funding_amount,
        'revenue_range': c.revenue_range,
    }


async def _local(args: dict) -> ToolResult:
    res = await asyncio.to_thread(
        search_places,
        args['local_query'],
        location=args.get('local_location'),
        gl=args.get('country_code'),
        limit=int(args.get('limit') or 20),
    )
    pages = res.get('pages', 0)
    if pages:
        await asyncio.to_thread(
            record_vendor_call,
            SERPER,
            'places',
            metered=pages,
            units=pages,
            success=bool(res.get('success')),
        )
    if not res.get('success'):
        return ToolResult(ok=False, error=res.get('error') or 'local business search failed')
    rows = [place_row(p) for p in res['places']]
    return ToolResult(
        ok=True,
        rows=rows,
        entity='company',
        data={'count': len(rows), 'truncated': res.get('truncated', False), 'source': 'google_maps'},
    )


async def discover_companies(client: ApolloClient, args: dict, page: int) -> Result[CompanyDiscoveryResult]:
    industries = args.get('industries') or []
    # Names the Apollo taxonomy can't express become keyword tags, not a silent no-op filter.
    keywords = list(dict.fromkeys((args.get('keywords') or []) + unknown_industries(industries)))
    return await client.discover_organizations(
        locations=args.get('locations') or None,
        employee_ranges=args.get('employee_ranges') or None,
        keyword_tags=keywords or None,
        industry_tag_ids=resolve_industry_tag_ids(industries) or None,
        job_titles=args.get('job_titles') or None,
        min_active_jobs=args.get('min_active_jobs'),
        latest_funding_min=args.get('latest_funding_min'),
        total_funding_min=args.get('total_funding_min'),
        revenue_min=args.get('revenue_min'),
        revenue_max=args.get('revenue_max'),
        sort_by=args.get('sort_by'),
        sort_descending=args.get('sort_descending', True),
        page=page,
    )


async def handle(args: dict, ctx: ToolContext) -> ToolResult:
    current_user_id.set(ctx.user_id)
    source = args.get('source') or 'apollo'
    if source not in {'apollo', 'aiark'}:
        return ToolResult(ok=False, error='unsupported company search source; use "apollo" or "aiark"')
    vendor = 'serper' if args.get('local_query') else source
    if await asyncio.to_thread(vendor_disabled_now, vendor):
        return ToolResult(ok=False, error=DISABLED)
    if vendor == 'serper':
        return await _local(args)

    page = int(args.get('page') or 1)
    cache_args = {**args, 'source': vendor, 'page': page}
    key = search_cache.cache_key(
        ctx,
        tool='search_companies',
        provider=vendor,
        endpoint='/v1/companies' if vendor == 'aiark' else '/mixed_companies/search',
        arguments=cache_args,
    )
    if cached := await search_cache.get(key):
        return cached

    if vendor == 'aiark':
        aiark = AiArkClient()
        if not aiark.is_configured:
            return ToolResult(ok=False, error='AI Ark is not configured; retry with source "apollo"')
        size = args.get('employee_size')
        aiark_result = await aiark.search_companies(
            account_domains=args.get('domains') or None,
            lookalike_domains=args.get('lookalike_domains') or None,
            account_industries=args.get('industries') or None,
            account_locations=args.get('locations') or None,
            account_keywords=args.get('keywords') or None,
            funding_types=args.get('funding_types') or None,
            employee_size=(size[0], size[1]) if size and len(size) == 2 else None,
            revenue_min=args.get('revenue_min'),
            revenue_max=args.get('revenue_max'),
            page=int(args.get('page') or 1) - 1,
        )
        if is_err(aiark_result):
            return ToolResult(ok=False, error=err_text(aiark_result))
        rows = [aiark_company_row(c) for c in aiark_result.value.companies]
        result = ToolResult(
            ok=True,
            rows=rows,
            entity='company',
            data={
                'count': len(rows),
                'total': aiark_result.value.total_elements,
                'page': int(args.get('page') or 1),
                'source': 'aiark',
            },
        )
        await search_cache.put(key, result)
        return result

    client = ApolloClient()
    if not client.is_configured:
        return ToolResult(ok=False, error='company search is not configured')
    if args.get('name'):
        name_result = await client.search_organizations(args['name'], location=(args.get('locations') or [None])[0])
        if is_err(name_result):
            return ToolResult(ok=False, error=err_text(name_result))
        rows = []
        for c in name_result.value:
            city, country = city_country(c.location)
            rows.append(
                {
                    'name': c.name,
                    'domain': normalise_domain(c.domain or c.website or ''),
                    'website': c.website,
                    'linkedin_url': None,
                    'industry': c.industry,
                    'employees': c.employee_count,
                    'city': city,
                    'country': country,
                    'description': None,
                    'source': 'apollo',
                    'apollo_id': c.id,
                }
            )
        result = ToolResult(
            ok=True,
            rows=rows,
            entity='company',
            data={'count': len(rows), 'source': 'apollo'},
        )
        await search_cache.put(key, result)
        return result
    apollo_result = await discover_companies(client, args, page)
    if is_err(apollo_result):
        return ToolResult(ok=False, error=err_text(apollo_result))
    found = apollo_result.value
    rows = [company_row(c) for c in found.companies]
    data: dict = {'count': len(rows), 'total': found.total_entries, 'page': page, 'source': 'apollo'}
    if not rows and page == 1 and (applied := applied_filters(args)):
        data['applied_filters'] = applied
    locations = args.get('locations') or []
    if (
        page == 1
        and len(rows) < LOCAL_FALLBACK_MIN
        and locations
        and not await asyncio.to_thread(vendor_disabled_now, 'serper')
    ):
        data['local_fallback'] = (
            f'Only {len(rows)} B2B matches. Google Maps may list more local businesses: call search_companies '
            f'with local_query (the kind of business, in the local language) and local_location "{locations[0]}".'
        )
    result = ToolResult(ok=True, rows=rows, entity='company', data=data)
    await search_cache.put(key, result)
    return result


register(
    ToolDef(
        name='search_companies',
        description=(
            'Find companies. Two modes:\n'
            '- B2B database (Apollo): filter by name, locations ("Milan, Italy"), employee_ranges ("11,50"), '
            'industries, keywords, job_titles, funding and revenue. ~25 companies per page; use page for more.\n'
            '- AI Ark: set source="aiark" to filter by domains, locations, employee_size [min,max], industries, '
            'keywords, funding_types and revenue.\n'
            '- Local businesses on Google Maps: set local_query (e.g. "ristoranti", "dentisti") and '
            'local_location ("Milano, Italia"). Use for shops, restaurants, studios, trades.\n'
            'Not for people (use search_people) nor for hiring signals (use search_jobs_signals).'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'locations': {'type': 'array', 'items': {'type': 'string'}},
                'name': {
                    'type': 'string',
                    'description': 'Apollo company name search; optionally narrow with locations',
                },
                'domains': {'type': 'array', 'items': {'type': 'string'}, 'description': 'AI Ark domain filter'},
                'lookalike_domains': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'AI Ark similar-company domains',
                },
                'source': {'type': 'string', 'enum': ['apollo', 'aiark'], 'default': 'apollo'},
                'employee_ranges': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '"min,max", e.g. "11,50"',
                },
                'industries': {'type': 'array', 'items': {'type': 'string'}},
                'keywords': {'type': 'array', 'items': {'type': 'string'}},
                'job_titles': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Apollo active job titles'},
                'min_active_jobs': {'type': 'integer', 'minimum': 0},
                'latest_funding_min': {
                    'type': 'integer',
                    'minimum': 0,
                    'description': 'Apollo latest funding amount in USD',
                },
                'total_funding_min': {'type': 'integer', 'minimum': 0, 'description': 'Apollo total funding in USD'},
                'funding_types': {'type': 'array', 'items': {'type': 'string'}, 'description': 'AI Ark funding stages'},
                'employee_size': {
                    'type': 'array',
                    'items': {'type': 'integer', 'minimum': 0},
                    'minItems': 2,
                    'maxItems': 2,
                    'description': 'AI Ark: [min, max]',
                },
                'revenue_min': {'type': 'integer', 'minimum': 0, 'description': 'Annual revenue in USD'},
                'revenue_max': {'type': 'integer', 'minimum': 0, 'description': 'Annual revenue in USD'},
                'sort_by': {'type': 'string', 'enum': ['employees', 'founded_year']},
                'sort_descending': {'type': 'boolean', 'default': True},
                'page': {'type': 'integer', 'minimum': 1, 'default': 1},
                'local_query': {'type': 'string', 'description': 'Google Maps mode: what the business is'},
                'local_location': {'type': 'string'},
                'country_code': {'type': 'string', 'description': 'Google Maps mode: 2-letter country, e.g. "it"'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 60, 'default': 20},
            },
        },
        handler=handle,
        found_noun='company',
        provider=None,  # apollo or serper by mode: the handler checks the kill switch itself
    )
)
