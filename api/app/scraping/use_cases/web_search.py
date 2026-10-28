"""web_search use case — query → ranked web results (links + snippets only;
the agent chains ``fetch_page`` for full text).

From the earlier implementation ``scraping/use_cases/web_search.py``; the Exa gate is the env bool
``EXA_ENABLED`` instead of a PostHog flag. When on, Exa goes first and any
failure or empty result falls back to Firecrawl.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.admin.settings import vendor_disabled_now
from app.core.config import settings
from app.core.error_codes import TOOL
from app.scraping.adapters.exa_adapter import exa_search
from app.scraping.adapters.firecrawl_adapter import firecrawl_search

_DEFAULT_NUM = 10

logger = structlog.get_logger()


def web_search(query: str, *, num: int = _DEFAULT_NUM) -> dict[str, Any]:
    """``{'success', 'query', 'results': [...]}`` or ``{'success': False, 'error', ...}``."""
    cleaned = (query or '').strip()
    if not cleaned:
        return {
            'success': False,
            'error': 'empty query',
            'query': '',
            'results': [],
            'error_class': TOOL.INVALID_ARGUMENTS,
        }

    if settings.exa_enabled and not vendor_disabled_now('exa'):
        res = exa_search(cleaned, num=num)
        if res.get('success') and res.get('results'):
            return res
        logger.info(
            'web_search.exa_fallback_to_firecrawl',
            query=cleaned[:80],
            exa_success=bool(res.get('success')),
            exa_count=len(res.get('results') or []),
        )
    return firecrawl_search(cleaned, num=num)
