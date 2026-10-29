"""save_to_list: rows the model extracted from web pages go into the List like any vendor row."""

from __future__ import annotations

from urllib.parse import urlparse

from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register

MAX_ITEMS = 100


def _clean(item: dict, entity: str) -> dict | None:
    """Keep a row only if it names something and cites where it was read (no invented leads)."""
    row = {k: v for k, v in item.items() if isinstance(v, (str, int, float)) and str(v).strip()}
    source = str(row.pop('source_url', '')).strip()
    if urlparse(source).scheme not in ('http', 'https'):
        return None
    if entity == 'company' and not row.get('name'):
        return None
    if entity == 'person' and not (row.get('full_name') or row.get('last_name')):
        return None
    if entity == 'company' and not row.get('domain') and row.get('website'):
        host = urlparse(str(row['website']) if '://' in str(row['website']) else f'https://{row["website"]}').hostname
        if host:
            row['domain'] = host.removeprefix('www.')  # dedupe key, same as vendor rows
    return {**row, 'source': 'web', 'source_url': source}


async def _save_to_list(args: dict, ctx: ToolContext) -> ToolResult:
    entity = args.get('entity')
    if entity not in ('company', 'person'):
        return ToolResult(ok=False, error="entity must be 'company' or 'person'")
    items = [i for i in (args.get('items') or [])[:MAX_ITEMS] if isinstance(i, dict)]
    rows = [r for r in (_clean(i, entity) for i in items) if r]
    if not rows:
        return ToolResult(ok=False, error='no valid items: each needs a name and the source_url where you read it')
    return ToolResult(ok=True, rows=rows, entity=entity, data={'saved': len(rows), 'skipped': len(items) - len(rows)})


register(
    ToolDef(
        name='save_to_list',
        description=(
            "Save companies or people you found on web pages (web_search / fetch_page) into the user's List. "
            'Use it whenever your answer would list entities found on the web, instead of writing a table. '
            'Only entities you actually read in a result; every item needs `source_url` (the page it came from). '
            'Company keys: name, domain, website, linkedin_url, industry, employees, city, country, description. '
            'Person keys: full_name or first_name+last_name, title, company_name, company_domain, linkedin_url, city. '
            'An item without a name (company) or a full_name/last_name (person), or without an http(s) '
            'source_url, is skipped; the result counts saved and skipped, and it is an error when no item is valid.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'entity': {'type': 'string', 'enum': ['company', 'person']},
                'items': {
                    'type': 'array',
                    'maxItems': MAX_ITEMS,
                    'items': {
                        'type': 'object',
                        'properties': {
                            'source_url': {'type': 'string', 'description': 'The page the entity was read on'},
                        },
                        'required': ['source_url'],
                        'description': 'One entity as flat key/value pairs, using the keys above',
                    },
                },
            },
            'required': ['entity', 'items'],
        },
        handler=_save_to_list,
        found_noun='entity',
    )
)
