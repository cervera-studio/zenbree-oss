"""Firecrawl search adapter — query → ranked web results.

From the earlier implementation ``scraping/adapters/firecrawl_adapter.py``, search only: plain httpx
against ``/v2/search`` instead of the firecrawl-py SDK (one fewer dependency
for one endpoint). The result parser and the vendor-free error envelope are
unchanged.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.core.error_codes import TOOL

logger = structlog.get_logger()

_SEARCH_URL = 'https://api.firecrawl.dev/v2/search'
_TIMEOUT_SECONDS = 60.0
_SEARCH_DEFAULT_NUM = 10
_SEARCH_MAX_NUM = 20

# Substrings that mark a credit/quota wall (vs a transient network blip).
_DEPLETION_MARKERS: tuple[str, ...] = (
    'insufficient credits',
    'not enough credits',
    'payment required',
    'quota',
    '402',
)


def _is_credit_exhaustion(detail: str) -> bool:
    low = detail.lower()
    return any(marker in low for marker in _DEPLETION_MARKERS)


def _search_unavailable(query: str) -> dict[str, Any]:
    """Clean, vendor-free, code-free error envelope.

    The model echoes tool errors into its prose, so a leaked
    ``"firecrawl 402"`` becomes user-visible. Say only what the user can act on.
    """
    return {
        'success': False,
        'error': 'web search is temporarily unavailable',
        'query': query,
        'results': [],
    }


def _extract_web_items(res: Any) -> list[Any]:
    """``{'data': {'web': [...]}}`` (v2), ``{'data': [...]}`` (v1), or a bare list."""
    data = res.get('data') if isinstance(res, dict) else res
    if isinstance(data, dict):
        inner = data.get('web')
        if isinstance(inner, list):
            return inner
    if isinstance(data, list):
        return data
    return []


def _parse_firecrawl_web(res: Any) -> list[dict[str, Any]]:
    """Project Firecrawl results to ``{url, title, snippet, position}``; entries
    without a URL are skipped, ``description`` maps to ``snippet``."""
    results: list[dict[str, Any]] = []
    for index, entry in enumerate(_extract_web_items(res)):
        if not isinstance(entry, dict) or not entry.get('url'):
            continue
        position = entry.get('position')
        results.append(
            {
                'url': entry['url'],
                'title': entry.get('title') or '',
                'snippet': entry.get('description') or entry.get('snippet') or '',
                'position': position if position is not None else index + 1,
            }
        )
    return results


def firecrawl_search(query: str, *, num: int = _SEARCH_DEFAULT_NUM) -> dict[str, Any]:
    """One Firecrawl web search → ``{'success', 'query', 'results': [...]}``.

    Never raises: failures collapse to the clean error envelope. Credit
    exhaustion is logged distinctly as ``firecrawl.depleted`` for alerting.
    Synchronous; callers run it under ``asyncio.to_thread``.
    """
    cleaned = (query or '').strip()
    if not cleaned:
        return {
            'success': False,
            'error': 'empty query',
            'query': '',
            'results': [],
            'error_class': TOOL.INVALID_ARGUMENTS,
        }

    if not settings.firecrawl_api_key:
        logger.warning('firecrawl.search_unconfigured')
        return _search_unavailable(cleaned)

    limit = max(1, min(num, _SEARCH_MAX_NUM))
    try:
        resp = httpx.post(
            _SEARCH_URL,
            json={'query': cleaned, 'limit': limit},
            headers={'Authorization': f'Bearer {settings.firecrawl_api_key}'},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        res = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        # Log type + status only: str() of an httpx error embeds the request URL.
        r = getattr(e, 'response', None)
        status = getattr(r, 'status_code', None)
        fields = {'query': cleaned[:80], 'error_type': type(e).__name__, 'status_code': status}
        if status == 402 or _is_credit_exhaustion(getattr(r, 'text', '') or ''):
            logger.error('firecrawl.depleted', **fields)
        else:
            logger.warning('firecrawl.search_failed', **fields)
        return _search_unavailable(cleaned)

    results = _parse_firecrawl_web(res)
    logger.info('firecrawl.search', query=cleaned[:80], count=len(results))
    return {'success': True, 'provider': 'firecrawl', 'query': cleaned, 'results': results}
