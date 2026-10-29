"""fetch_page use case — URL → readable page text.

Simplified from the earlier implementation ``scraping/use_cases/fetch_page.py``: SSRF-safe GET
(``safe_httpx_get`` validates every redirect hop and caps the body), HTML →
markdown via html2text. Dropped: domain health, request cache, extraction
sandbox (PDF), social-URL routing, JSON-LD. Add when a tool needs them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import html2text
import httpx
import structlog

from app.core.errors import is_err
from app.core.security.ssrf_validator import ResponseTooLargeError, SSRFBlockedError, safe_httpx_get, validate_url

logger = structlog.get_logger()

MAX_CHARS = 20_000
_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ZenbreeBot/1.0; +https://zenbree.com)'}


_TITLE_OPEN = re.compile(r'<title\b[^>]{0,256}>', re.IGNORECASE)
_TITLE_CLOSE = re.compile(r'</title\s*>', re.IGNORECASE)
_HEAD_CHARS = 65_536
# ponytail: html2text costs up to ~1.8 s/MiB under the GIL; callers keep 20k chars of text, so 1 MiB of markup is
# plenty. Raise it only if pages lose their content; move the parse off the API process if it must grow.
_PARSE_CHARS = 1_048_576


def _title(html: str) -> str:
    # The title sits in <head>: scan only a small head, each pattern once, so the cost stays linear.
    head = html[:_HEAD_CHARS]
    if not (start := _TITLE_OPEN.search(head)):
        return ''
    end = _TITLE_CLOSE.search(head, start.end())
    return re.sub(r'\s+', ' ', head[start.end() : end.start() if end else start.end()]).strip()[:300]


_BLOCK_OPEN = re.compile(r'<(script|style)(?=[\s/>])', re.IGNORECASE)
_BLOCK_CLOSE = {tag: re.compile(rf'</{tag}(?:[\s/][^<>]{{0,256}})?>', re.IGNORECASE) for tag in ('script', 'style')}


def _strip_blocks(html: str) -> str:
    """Drop <script>/<style> blocks, which html2text ignores anyway, in one forward pass: every search
    starts where the last ended, and a block with no closer is kept, the cap bounds it."""
    parts, pos = [], 0
    while m := _BLOCK_OPEN.search(html, pos):
        parts.append(html[pos : m.start()])
        close = _BLOCK_CLOSE[m.group(1).lower()].search(html, m.end())
        if not close:
            parts.append(html[m.start() :])
            return ''.join(parts)
        pos = close.end()
    parts.append(html[pos:])
    return ''.join(parts)


def html_to_text(html: str, url: str = '') -> str:
    return _markdown(_strip_blocks(html), url)


def _markdown(markup: str, url: str) -> str:
    """``markup`` already went through ``_strip_blocks``: a second pass would double the cost of a hostile page."""
    h = html2text.HTML2Text(baseurl=url)
    h.ignore_images = True
    h.body_width = 0
    return re.sub(r'\n{3,}', '\n\n', h.handle(markup[:_PARSE_CHARS])).strip()


def fetch_page(url: str, *, timeout: float = 30.0, follow: Callable[[str, str], bool] | None = None) -> dict[str, Any]:
    """``{'success', 'url', 'status_code', 'title', 'content', 'truncated'}`` or
    ``{'success': False, 'error', 'url'}``. Never raises. A redirect that ``follow(source, target)``
    rejects is an error that names the target."""
    check = validate_url(url)
    if is_err(check):
        return {'success': False, 'error': 'This URL cannot be fetched (blocked or invalid).', 'url': url}
    try:
        resp = safe_httpx_get(url, timeout=timeout, headers=_HEADERS, follow=follow)
    except ResponseTooLargeError:
        return {'success': False, 'error': 'The page is too large to process. Do not retry this URL.', 'url': url}
    except SSRFBlockedError:
        return {'success': False, 'error': 'This URL cannot be fetched (blocked or invalid).', 'url': url}
    except httpx.HTTPError as e:
        logger.info('fetch_page.failed', url=url, error_type=type(e).__name__)
        return {'success': False, 'error': 'The page could not be reached.', 'url': url}

    if resp.is_redirect and resp.headers['location']:
        target = urljoin(str(resp.url), resp.headers['location'])
        return {'success': False, 'error': f'The page redirects to {target}, which is not followed.', 'url': url}

    if resp.status_code >= 400:
        return {
            'success': False,
            'error': f'HTTP {resp.status_code}',
            'status_code': resp.status_code,
            'url': str(resp.url),
        }

    ctype = resp.headers.get('content-type', '')
    if 'html' in ctype or not ctype:
        markup = _strip_blocks(resp.text)  # the title often follows inline styles that fill the head
        body, title = _markdown(markup, str(resp.url)), _title(markup)
    elif ctype.startswith('text/') or 'json' in ctype:
        body, title = resp.text, ''
    else:
        return {'success': False, 'error': f'Unsupported content type {ctype.split(";")[0]}.', 'url': str(resp.url)}

    return {
        'success': True,
        'url': str(resp.url),
        'status_code': resp.status_code,
        'title': title,
        'content': body[:MAX_CHARS],
        'truncated': len(body) > MAX_CHARS,
    }
