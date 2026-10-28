"""``vendor_disabled:exa`` sends web search straight to Firecrawl."""

from app.core.config import settings
from app.scraping.use_cases import web_search as web_search_module


def _stub(monkeypatch, switched_off: bool):
    calls: list[str] = []
    monkeypatch.setattr(settings, 'exa_enabled', True)
    monkeypatch.setattr(web_search_module, 'vendor_disabled_now', lambda name: switched_off and name == 'exa')
    monkeypatch.setattr(
        web_search_module, 'exa_search', lambda q, num: calls.append('exa') or {'success': True, 'results': [1]}
    )
    monkeypatch.setattr(
        web_search_module,
        'firecrawl_search',
        lambda q, num: calls.append('firecrawl') or {'success': True, 'results': []},
    )
    return calls


def test_exa_runs_when_its_switch_is_off(monkeypatch):
    calls = _stub(monkeypatch, switched_off=False)
    web_search_module.web_search('acme')
    assert calls == ['exa']


def test_exa_is_skipped_when_its_switch_is_on(monkeypatch):
    calls = _stub(monkeypatch, switched_off=True)
    web_search_module.web_search('acme')
    assert calls == ['firecrawl']
