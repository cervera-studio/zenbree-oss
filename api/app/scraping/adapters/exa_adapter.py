"""Exa semantic search adapter returning the shared ``web_search`` envelope."""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.core.error_codes import TOOL

logger = structlog.get_logger()

_SEARCH_URL = 'https://api.exa.ai/search'
_TIMEOUT_SECONDS = 30.0
_SEARCH_MAX_NUM = 20
_SNIPPET_MAX_CHARS = 400


def _search_unavailable(query: str) -> dict[str, Any]:
    return {
        'success': False,
        'error': 'web search is temporarily unavailable',
        'query': query,
        'results': [],
    }


def _error_fields(e: httpx.HTTPError) -> dict[str, Any]:
    status = getattr(getattr(e, 'response', None), 'status_code', None)
    return {'error_type': type(e).__name__, **({'status_code': status} if status else {})}


def exa_search(query: str, *, num: int = 10) -> dict[str, Any]:
    """Search Exa and return ranked results in the provider-agnostic envelope."""
    cleaned = (query or '').strip()
    if not cleaned:
        return {
            'success': False,
            'error': 'empty query',
            'query': '',
            'results': [],
            'error_class': TOOL.INVALID_ARGUMENTS,
        }
    limit = max(1, min(num, _SEARCH_MAX_NUM))
    headers = {'x-api-key': settings.exa_api_key} if settings.exa_api_key else {}
    body: dict[str, Any] = {
        'query': cleaned,
        'type': 'auto',
        'contents': {'highlights': True},
    }
    if limit != 10:
        body['numResults'] = limit

    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            response = client.post(_SEARCH_URL, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as e:
        logger.warning('exa.search_failed', query=cleaned[:80], **_error_fields(e))
        return _search_unavailable(cleaned)
    except ValueError:
        logger.warning('exa.search_bad_payload', query=cleaned[:80])
        return _search_unavailable(cleaned)

    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        logger.warning('exa.search_bad_payload', query=cleaned[:80])
        return _search_unavailable(cleaned)

    results: list[dict[str, Any]] = []
    for item in payload['results']:
        if not isinstance(item, dict) or not isinstance(item.get('url'), str):
            continue
        highlights = item.get('highlights') or []
        snippet = re.sub(r'\s+', ' ', ' '.join(s for s in highlights if isinstance(s, str))).strip()
        results.append(
            {
                'url': item['url'],
                'title': item.get('title') if isinstance(item.get('title'), str) else '',
                'snippet': snippet[:_SNIPPET_MAX_CHARS],
                'position': len(results) + 1,
            }
        )
    logger.info('exa.search', query=cleaned[:80], count=len(results))
    return {'success': True, 'provider': 'exa', 'query': cleaned, 'results': results}
