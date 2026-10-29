"""search_people: Apollo people search (free previews), AI Ark as the alternative source."""

from __future__ import annotations

import asyncio

from app.admin.settings import vendor_disabled_now
from app.agent.funnel import applied_filters
from app.agent.tools import search_cache
from app.agent.tools.handlers._rows import DISABLED, city_country, err_text
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.billing.paid_call import current_user_id
from app.core.errors import Result, is_err
from app.people.aiark_client import AiArkClient, AiArkPerson
from app.people.apollo_client import ApolloClient
from app.people.apollo_models import PersonPreview, SearchResult, resolve_industry_tag_ids, unknown_industries


def apollo_row(p: PersonPreview) -> dict:
    city, country = city_country(p.person_location)
    return {
        'first_name': p.first_name,
        # Apollo previews obfuscate the surname ("R***i"); enrich_email reveals it.
        'last_name': p.last_name_obfuscated,
        'full_name': f'{p.first_name} {p.last_name_obfuscated}'.strip(),
        'title': p.title,
        'company_name': p.organization_name,
        'company_domain': p.organization_domain,
        'linkedin_url': p.linkedin_url,
        'email': None,
        'email_status': None,
        'city': city,
        'country': country,
        'source': 'apollo',
        'person_id': p.id,
        'seniority': p.seniority,
        'has_email': p.has_email,
    }


def aiark_row(p: AiArkPerson) -> dict:
    city, country = city_country(p.person_location)
    return {
        'first_name': p.first_name,
        'last_name': p.last_name,
        'full_name': p.full_name or f'{p.first_name} {p.last_name}'.strip(),
        'title': p.title,
        'company_name': p.organization_name,
        'company_domain': p.organization_domain,
        'linkedin_url': p.linkedin_url,
        'email': None,
        'email_status': None,
        'city': city,
        'country': country,
        'source': 'aiark',
        'person_id': p.id,
        'headline': p.headline,
    }


async def discover_people(client: ApolloClient, args: dict, page: int, per_page: int = 10) -> Result[SearchResult]:
    industries = args.get('industries') or []
    # Names the Apollo taxonomy can't express become keyword tags (as in search_companies), not a silent no-op filter.
    keyword_tags = list(dict.fromkeys((args.get('keyword_tags') or []) + unknown_industries(industries)))
    return await client.search_people(
        person_titles=args.get('titles') or None,
        person_seniorities=args.get('seniorities') or None,
        person_locations=args.get('locations') or None,
        organization_domains=args.get('company_domains') or None,
        organization_name=args.get('company_name') or None,
        organization_ids=args.get('company_ids') or None,
        employee_ranges=args.get('employee_ranges') or None,
        industry_tag_ids=resolve_industry_tag_ids(industries) or None,
        keyword_tags=keyword_tags or None,
        q_keywords=' '.join(args.get('keywords') or []) or None,
        person_functions=args.get('functions') or None,
        organization_technologies=args.get('technologies_all') or None,
        organization_technologies_any=args.get('technologies_any') or None,
        organization_technologies_excluded=args.get('technologies_excluded') or None,
        organization_locations=args.get('company_locations') or None,
        contact_email_status=args.get('email_status') or None,
        include_similar_titles=args.get('include_similar_titles'),
        organization_job_titles=args.get('job_titles') or None,
        organization_job_locations=args.get('job_locations') or None,
        organization_num_jobs_min=args.get('min_active_jobs'),
        organization_num_jobs_max=args.get('max_active_jobs'),
        organization_job_posted_at_min=args.get('job_posted_after'),
        organization_job_posted_at_max=args.get('job_posted_before'),
        revenue_min=args.get('revenue_min'),
        revenue_max=args.get('revenue_max'),
        page=page,
        per_page=per_page,
    )


def _funnel_filters(args: dict) -> dict | None:
    """The no-results funnel's filters, only when they are the whole search: a funnel relaxes and re-runs
    them, so a filter it does not know (company domains, technologies, email status…) would be dropped silently.

    ponytail: such searches get no funnel; carry the other filters through funnel.build if they need one."""
    applied = applied_filters(args, 'person')
    given = {k for k, v in args.items() if v not in (None, [], '') and k not in ('page', 'source')}
    return applied if applied and given <= applied.keys() else None


async def handle(args: dict, ctx: ToolContext) -> ToolResult:
    current_user_id.set(ctx.user_id)
    page = int(args.get('page') or 1)
    vendor = 'aiark' if args.get('source') == 'aiark' else 'apollo'
    if await asyncio.to_thread(vendor_disabled_now, vendor):
        return ToolResult(ok=False, error=DISABLED)
    cache_args = {**args, 'source': vendor, 'page': page}
    key = search_cache.cache_key(
        ctx,
        tool='search_people',
        provider=vendor,
        endpoint='/v1/people' if vendor == 'aiark' else '/mixed_people/api_search',
        arguments=cache_args,
    )
    if cached := await search_cache.get(key):
        return cached
    if vendor == 'aiark':
        aiark = AiArkClient()
        if not aiark.is_configured:
            return ToolResult(ok=False, error='AI Ark is not configured; retry with source "apollo"')
        size = args.get('employee_size')
        aiark_result = await aiark.search_people(
            person_titles=args.get('titles') or None,
            person_seniorities=args.get('seniorities') or None,
            person_locations=args.get('locations') or None,
            account_domains=args.get('company_domains') or None,
            account_industries=args.get('industries') or None,
            account_keywords=args.get('keywords') or None,
            employee_size=(size[0], size[1]) if size and len(size) == 2 else None,
            account_locations=args.get('company_locations') or None,
            technologies_all=args.get('technologies_all') or None,
            technologies_any=args.get('technologies_any') or None,
            technologies_excluded=args.get('technologies_excluded') or None,
            revenue_min=args.get('revenue_min'),
            revenue_max=args.get('revenue_max'),
            page=page - 1,  # AI Ark pages are 0-based
        )
        if is_err(aiark_result):
            return ToolResult(ok=False, error=err_text(aiark_result))
        rows = [aiark_row(p) for p in aiark_result.value.people]
        total = aiark_result.value.total_elements
    else:
        apollo = ApolloClient()
        if not apollo.is_configured:
            return ToolResult(ok=False, error='people search is not configured')
        apollo_result = await discover_people(apollo, args, page)
        if is_err(apollo_result):
            return ToolResult(ok=False, error=err_text(apollo_result))
        rows = [apollo_row(p) for p in apollo_result.value.people]
        total = apollo_result.value.total_entries
    data: dict = {'count': len(rows), 'total': total, 'page': page, 'source': args.get('source') or 'apollo'}
    if not rows and page == 1 and vendor == 'apollo' and (applied := _funnel_filters(args)):
        data['applied_filters'] = applied
    result = ToolResult(ok=True, rows=rows, entity='person', data=data)
    await search_cache.put(key, result)
    return result


register(
    ToolDef(
        name='search_people',
        description=(
            'Find people (decision makers) by titles ("Head of Sales", "CEO"), seniorities '
            '(owner, founder, c_suite, vp, head, director, manager), locations ("Milan, Italy"), '
            'company_domains or company_name, employee_ranges ("11,50"), industries, keyword_tags and keywords. '
            'No emails here: surnames from Apollo are partly hidden until enrich_email reveals them. '
            'Optional filters include functions, technologies, employer locations, revenue, and employer job postings. '
            'source "aiark" is an alternative database (full names, paid per row) when Apollo finds too few; '
            'AI Ark also supports employee_size and technology/revenue filters.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'titles': {'type': 'array', 'items': {'type': 'string'}},
                'seniorities': {'type': 'array', 'items': {'type': 'string'}},
                'functions': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Apollo job functions'},
                'locations': {'type': 'array', 'items': {'type': 'string'}},
                'company_locations': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Employer location'},
                'company_domains': {'type': 'array', 'items': {'type': 'string'}},
                'company_ids': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Apollo organization IDs'},
                'company_name': {'type': 'string'},
                'employee_ranges': {'type': 'array', 'items': {'type': 'string'}},
                'employee_size': {
                    'type': 'array',
                    'items': {'type': 'integer', 'minimum': 0},
                    'minItems': 2,
                    'maxItems': 2,
                    'description': 'aiark: [min, max]',
                },
                'industries': {'type': 'array', 'items': {'type': 'string'}},
                'keyword_tags': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Apollo organization keyword tags',
                },
                'keywords': {'type': 'array', 'items': {'type': 'string'}},
                'technologies_all': {'type': 'array', 'items': {'type': 'string'}},
                'technologies_any': {'type': 'array', 'items': {'type': 'string'}},
                'technologies_excluded': {'type': 'array', 'items': {'type': 'string'}},
                'revenue_min': {'type': 'integer', 'minimum': 0, 'description': 'Annual revenue in USD'},
                'revenue_max': {'type': 'integer', 'minimum': 0, 'description': 'Annual revenue in USD'},
                'email_status': {
                    'type': 'array',
                    'items': {'type': 'string', 'enum': ['verified', 'guessed', 'unavailable', 'bounced']},
                },
                'include_similar_titles': {
                    'type': 'boolean',
                    'description': 'Apollo only; false disables similar-title matching',
                },
                'job_titles': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Employer active job titles',
                },
                'job_locations': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Employer job locations',
                },
                'min_active_jobs': {'type': 'integer', 'minimum': 0},
                'max_active_jobs': {'type': 'integer', 'minimum': 0},
                'job_posted_after': {'type': 'string', 'format': 'date'},
                'job_posted_before': {'type': 'string', 'format': 'date'},
                'source': {'type': 'string', 'enum': ['apollo', 'aiark'], 'default': 'apollo'},
                'page': {'type': 'integer', 'minimum': 1, 'default': 1},
            },
        },
        handler=handle,
        found_noun='person',
        provider=None,  # apollo or aiark by source: the handler checks the kill switch itself
    )
)
