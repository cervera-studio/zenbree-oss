"""web_search + fetch_page: free tools that prove the loop end to end."""

from __future__ import annotations

import asyncio

from app.agent.hosts import host_of
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.billing.paid_call import current_user_id, record_vendor_call
from app.billing.vendors import VENDORS
from app.core.config import settings
from app.scraping.use_cases.fetch_page import fetch_page
from app.scraping.use_cases.web_search import web_search


async def _web_search(args: dict, ctx: ToolContext) -> ToolResult:
    res = await asyncio.to_thread(web_search, str(args.get('query') or ''), num=int(args.get('num') or 10))
    if not res.get('success'):
        return ToolResult(ok=False, error=res.get('error'))
    vendor = VENDORS[res.get('provider', 'firecrawl')]
    current_user_id.set(ctx.user_id)
    keyless = vendor.name == 'exa' and not settings.exa_api_key
    await asyncio.to_thread(record_vendor_call, vendor, 'search', metered=0 if keyless else 1)
    return ToolResult(ok=True, data={'query': res['query'], 'results': res['results']})


async def _fetch_page(args: dict, ctx: ToolContext) -> ToolResult:
    url = str(args.get('url') or '')
    if not (target := ctx.named.resolve(url)):
        if host_of(url) in ctx.named.pages:
            error = (
                f'{url} was not a web_search result or a link on a page you read; on a site that only '
                'web_search found, fetch_page reads only those URLs.'
            )
        else:
            error = (
                f'{host_of(url) or url} was not named by the user, a web_search result or a list row; '
                'fetch_page reads only those sites.'
            )
        return ToolResult(ok=False, error=error)
    res = await asyncio.to_thread(fetch_page, target, follow=ctx.named.follows)
    if not res.pop('success'):
        return ToolResult(ok=False, error=res.get('error'), data=res)
    return ToolResult(ok=True, data=res)


register(
    ToolDef(
        name='web_search',
        description=(
            'Search the web. Returns up to `num` results as {url, title, snippet}. Use to discover websites, '
            'news or facts about a company; follow up with fetch_page to read a result. Prefer the company/people '
            'search tools for lead lists; when you do find leads here, store them with save_to_list.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Search query'},
                'num': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 10},
            },
            'required': ['query'],
        },
        handler=_web_search,
        provider='firecrawl',
    )
)

register(
    ToolDef(
        name='fetch_page',
        description=(
            'Read one public web page and return its text as markdown (max ~20k chars). Use after web_search '
            'or on a company website to check what they do, find team pages or contact info. Reads only sites '
            'the user named or a search tool put in a list row (and their subdomains); on a site that only '
            'web_search found, only the result URLs and the links on its pages you read. Follows a redirect '
            'only to those sites.'
        ),
        input_schema={
            'type': 'object',
            'properties': {'url': {'type': 'string', 'description': 'Absolute http(s) URL'}},
            'required': ['url'],
        },
        handler=_fetch_page,
    )
)
