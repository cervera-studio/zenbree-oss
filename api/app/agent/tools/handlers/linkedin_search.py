"""linkedin_search: LinkedIn people/company search through the user's own Unipile-connected account."""

from __future__ import annotations

import asyncio

from app.agent.tools.handlers._rows import city_country, err_text
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.billing.paid_call import current_user_id, record_vendor_call
from app.billing.vendors import UNIPILE
from app.core.errors import is_err
from app.linkedin.api import connected_account_id
from app.linkedin.unipile_client import UnipileClient

NOT_CONNECTED = (
    'LinkedIn is not connected. Only if the user asked for a LinkedIn search, ask them to connect their account '
    'from Integrations (Impostazioni → Integrazioni → LinkedIn), then retry; as a fallback for another failed '
    'search, never suggest it.'
)


async def _filter_ids(client: UnipileClient, account_id: str, kind: str, text: str | None) -> list[str]:
    """Free text ("Milano") → LinkedIn's numeric filter id, first match only."""
    if not text:
        return []
    found = await client.search_parameters(account_id=account_id, type=kind, keywords=text, limit=1)
    if is_err(found):
        return []
    return [str(i['id']) for i in found.value.get('items') or [] if i.get('id')][:1]


def person_row(item: dict) -> dict:
    city, country = city_country(item.get('location'))
    position = (item.get('current_positions') or [{}])[0] or {}
    return {
        'first_name': item.get('first_name'),
        'last_name': item.get('last_name'),
        'full_name': item.get('name'),
        'title': position.get('role') or item.get('headline'),
        'company_name': position.get('company'),
        'company_domain': None,
        'linkedin_url': item.get('public_profile_url') or item.get('profile_url'),
        'email': None,
        'email_status': None,
        'city': city,
        'country': country,
        'source': 'linkedin',
        'provider_id': item.get('id'),
        'headline': item.get('headline'),
        'network_distance': item.get('network_distance'),
    }


def company_row(item: dict) -> dict:
    city, country = city_country(item.get('location'))
    return {
        'name': item.get('name'),
        'domain': None,
        'website': None,
        'linkedin_url': item.get('profile_url'),
        'industry': item.get('industry'),
        'employees': item.get('headcount'),
        'city': city,
        'country': country,
        'description': item.get('summary'),
        'source': 'linkedin',
        'provider_id': item.get('id'),
    }


async def handle(args: dict, ctx: ToolContext) -> ToolResult:
    current_user_id.set(ctx.user_id)
    account_id = await asyncio.to_thread(connected_account_id, ctx.project_id) if ctx.project_id else None
    if not account_id:
        return ToolResult(ok=False, error=NOT_CONNECTED, error_code='not_connected')
    client = UnipileClient()
    category = args.get('category') or 'people'
    api = args.get('api') or 'classic'
    if args.get('url'):
        body: dict = {'url': args['url']}
    else:
        body = {'api': api, 'category': category}
        if args.get('keywords'):
            body['keywords'] = args['keywords']
        location, industry = await asyncio.gather(
            _filter_ids(client, account_id, 'LOCATION', args.get('location')),
            _filter_ids(client, account_id, 'INDUSTRY', args.get('industry')),
        )
        for key, ids in (('location', location), ('industry', industry)):
            if ids:
                body[key] = {'include': ids} if api == 'sales_navigator' else ids
    result = await client.search(
        account_id=account_id,
        body=body,
        limit=int(args.get('limit') or 25),
        cursor=args.get('cursor'),
    )
    await asyncio.to_thread(record_vendor_call, UNIPILE, 'search', metered=1, success=not is_err(result))
    if is_err(result):
        return ToolResult(ok=False, error=err_text(result))
    items = result.value.get('items') or []
    is_company = category == 'companies' or any(i.get('type') == 'COMPANY' for i in items)
    rows = [company_row(i) if is_company else person_row(i) for i in items]
    return ToolResult(
        ok=True,
        rows=rows,
        entity='company' if is_company else 'person',
        data={
            'count': len(rows),
            'total': (result.value.get('paging') or {}).get('total_count'),
            'cursor': result.value.get('cursor'),  # pass back as cursor for the next page
        },
    )


register(
    ToolDef(
        name='linkedin_search',
        description=(
            "Search LinkedIn with the user's own connected LinkedIn account. category: people or companies; "
            'api: classic (any account) or sales_navigator (only if the user has it). keywords is free text '
            '("head of sales saas"); location and industry are plain words ("Milano", "Software Development"). '
            'Or pass url: a LinkedIn / Sales Navigator search URL the user copied. Up to 100 results per call; '
            'use the returned cursor for more. If LinkedIn is not connected, tell the user to connect it.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'category': {'type': 'string', 'enum': ['people', 'companies'], 'default': 'people'},
                'api': {'type': 'string', 'enum': ['classic', 'sales_navigator'], 'default': 'classic'},
                'keywords': {'type': 'string'},
                'location': {'type': 'string'},
                'industry': {'type': 'string'},
                'url': {'type': 'string'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 25},
                'cursor': {'type': 'string'},
            },
        },
        handler=handle,
        found_noun='entity',
        provider='unipile',
    )
)
