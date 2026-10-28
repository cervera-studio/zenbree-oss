import time

import httpx

from app.scraping.use_cases import fetch_page as fp
from app.scraping.use_cases.fetch_page import _title, html_to_text


def test_title_of_a_normal_page():
    assert _title('<html><head><TITLE lang="en">\n Acme   Inc </TITLE></head></html>') == 'Acme Inc'


def test_title_bomb_is_linear():
    start = time.perf_counter()
    assert _title('<title>' * (128 * 1024 // 7)) == ''
    assert time.perf_counter() - start < 1


def test_markup_bomb_is_bounded():
    start = time.perf_counter()
    html_to_text('<' * (16 * 1024 * 1024))
    assert time.perf_counter() - start < 5


def test_text_after_a_huge_inline_style_survives():
    html = '<html><head><style>' + 'a{}' * 500_000 + '</STYLE></head><body><p>Hello world</p></body></html>'
    assert html_to_text(html) == 'Hello world'


def test_opener_bomb_is_fast():
    start = time.perf_counter()
    html_to_text('<script>' * (2 * 1024 * 1024) + 'x')
    html_to_text('<script></script>' * (256 * 1024))
    assert time.perf_counter() - start < 2


def test_markup_that_only_looks_like_a_block_keeps_the_page():
    for odd in ('<div data-x="<style>">', '<style-guide>', '<!-- <script> -->', '<script'):
        assert 'Hello world' in html_to_text(f'{odd}<p>Hello world</p>')
    assert html_to_text('<script>x</script/><p>Hello world</p>') == 'Hello world'
    assert html_to_text('<style>x</style foo><p>Hello world</p>') == 'Hello world'


def test_title_after_a_huge_inline_style_is_found(monkeypatch):
    html = '<head><style>' + 'a{}' * 100_000 + '</style><title>Acme</title></head><body><p>Hi</p></body>'
    monkeypatch.setattr(fp, 'validate_url', lambda url: 'ok')
    resp = httpx.Response(
        200, text=html, headers={'content-type': 'text/html'}, request=httpx.Request('GET', 'https://acme.test/')
    )
    monkeypatch.setattr(fp, 'safe_httpx_get', lambda *a, **k: resp)
    page = fp.fetch_page('https://acme.test/')
    assert (page['title'], page['content']) == ('Acme', 'Hi')


def test_closer_bomb_is_fast_through_fetch_page(monkeypatch):
    html = '<script>' + '</script x' * (24 * 1024 * 1024 // 10)
    resp = httpx.Response(
        200, text=html, headers={'content-type': 'text/html'}, request=httpx.Request('GET', 'https://acme.test/')
    )
    monkeypatch.setattr(fp, 'validate_url', lambda url: 'ok')
    monkeypatch.setattr(fp, 'safe_httpx_get', lambda *a, **k: resp)
    start = time.perf_counter()
    assert fp.fetch_page('https://acme.test/')['success'] is True
    assert time.perf_counter() - start < 2
