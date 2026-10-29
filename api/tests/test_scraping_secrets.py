"""Vendor keys never reach the logs (they ship to BetterStack) or a request URL."""

import json

import httpx
from structlog.testing import capture_logs

from app.core.config import settings
from app.scraping.adapters import exa_adapter, firecrawl_adapter

KEY = 'sk-secret-123'


def test_exa_search_requests_highlights_and_maps_results(monkeypatch):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                'results': [
                    {
                        'title': 'Example',
                        'url': 'https://example.com',
                        'highlights': ['A useful ', 'snippet.'],
                    }
                ]
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(settings, 'exa_api_key', KEY)
    monkeypatch.setattr(
        exa_adapter.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )

    res = exa_adapter.exa_search('  example query  ')

    body = json.loads(seen[0].content)
    assert seen[0].url == 'https://api.exa.ai/search'
    assert seen[0].headers['x-api-key'] == KEY
    assert body == {'query': 'example query', 'type': 'auto', 'contents': {'highlights': True}}
    assert res['results'] == [
        {
            'url': 'https://example.com',
            'title': 'Example',
            'snippet': 'A useful snippet.',
            'position': 1,
        }
    ]


def test_exa_key_in_header_not_url_or_logs(monkeypatch):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(500, text=f'boom {req.url}')

    real_client = httpx.Client
    monkeypatch.setattr(settings, 'exa_api_key', KEY)
    monkeypatch.setattr(
        exa_adapter.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    with capture_logs() as logs:
        res = exa_adapter.exa_search('agenzie Milano')

    assert res['success'] is False
    assert seen[0].headers['x-api-key'] == KEY and KEY not in str(seen[0].url)
    assert logs and KEY not in repr(logs)
    assert logs[0]['error_type'] == 'HTTPStatusError' and logs[0]['status_code'] == 500


def test_firecrawl_error_log_has_no_error_string(monkeypatch):
    def fake_post(url, **kw):
        req = httpx.Request('POST', f'{url}?token={KEY}')
        raise httpx.HTTPStatusError('x', request=req, response=httpx.Response(402, request=req, text=KEY))

    monkeypatch.setattr(settings, 'firecrawl_api_key', KEY)
    monkeypatch.setattr(firecrawl_adapter.httpx, 'post', fake_post)
    with capture_logs() as logs:
        res = firecrawl_adapter.firecrawl_search('agenzie Milano')

    assert res['success'] is False
    assert KEY not in repr(logs)
    assert logs[0]['event'] == 'firecrawl.depleted' and logs[0]['status_code'] == 402
