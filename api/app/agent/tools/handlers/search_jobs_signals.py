"""search_jobs_signals: companies currently hiring for a role (Apollo), with their open postings.

ponytail: Adzuna not wired (the earlier implementation adapter is 259 LOC); add it when Apollo's
job-postings coverage for Italian SMBs proves thin.
"""

from __future__ import annotations

import asyncio

from app.agent.tools.handlers._rows import err_text
from app.agent.tools.handlers.search_companies import company_row
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.billing.paid_call import current_user_id
from app.core.errors import is_err, is_ok
from app.people.apollo_client import ApolloClient

# Postings cost 1 Apollo credit per company that has any: only the top rows get them.
_POSTINGS_FOR = 5


async def handle(args: dict, ctx: ToolContext) -> ToolResult:
    current_user_id.set(ctx.user_id)
    titles = [t for t in args.get('job_titles') or [] if t.strip()]
    if not titles:
        return ToolResult(ok=False, error='job_titles is required')
    client = ApolloClient()
    if not client.is_configured:
        return ToolResult(ok=False, error='job signals search is not configured')
    page = int(args.get('page') or 1)
    result = await client.discover_organizations(
        job_titles=titles,
        min_active_jobs=int(args.get('min_active_jobs') or 1),
        locations=args.get('locations') or None,
        employee_ranges=args.get('employee_ranges') or None,
        page=page,
    )
    if is_err(result):
        return ToolResult(ok=False, error=err_text(result))
    companies = result.value.companies
    rows = [{**company_row(c), 'hiring_for': ', '.join(titles)} for c in companies]

    if args.get('with_postings', True):
        wanted = [t.lower() for t in titles]
        postings = await asyncio.gather(
            *(client.get_job_postings(org_id=c.id, org_name=c.name) for c in companies[:_POSTINGS_FOR])
        )
        for row, got in zip(rows, postings, strict=False):
            if is_ok(got):
                matching = [p for p in got.value.postings if any(w in p.title.lower() for w in wanted)]
                row['open_roles'] = '; '.join(p.title for p in (matching or got.value.postings)[:5]) or None
                row['job_url'] = next((p.url for p in matching if p.url), None)

    return ToolResult(
        ok=True,
        rows=rows,
        entity='company',
        data={'count': len(rows), 'total': result.value.total_entries, 'page': page, 'hiring_for': titles},
    )


register(
    ToolDef(
        name='search_jobs_signals',
        description=(
            'Find companies that are hiring right now for given roles (a buying signal: '
            'e.g. hiring "SDR" or "Sales Development Representative" means they are building a sales team). '
            'Filters: job_titles (required), locations ("Milan, Italy"), employee_ranges ("11,50"). '
            'Returns companies with hiring_for and, for the first 5, open_roles from their job postings.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'job_titles': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1},
                'locations': {'type': 'array', 'items': {'type': 'string'}},
                'employee_ranges': {'type': 'array', 'items': {'type': 'string'}},
                'min_active_jobs': {'type': 'integer', 'minimum': 1, 'default': 1},
                'with_postings': {'type': 'boolean', 'default': True},
                'page': {'type': 'integer', 'minimum': 1, 'default': 1},
            },
            'required': ['job_titles'],
        },
        handler=handle,
        found_noun='company',
        provider='apollo',
    )
)
