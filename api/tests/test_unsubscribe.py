"""The one-click unsubscribe token (#172): opaque, authenticated, strict, bound to its secret."""

import base64
import uuid

from app.outreach import unsubscribe

OWNER = str(uuid.uuid4())
ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'


def _raw(token):
    return base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))


def test_round_trip_normalises_the_address_and_hides_it():
    token = unsubscribe.mint(OWNER, ' Anna@Client.TEST ', secret='s')
    assert token and set(token) <= set(ALPHABET)  # one URL path segment, no padding
    assert unsubscribe.parse(token, secret='s') == (OWNER, 'anna@client.test')
    assert b'anna' not in _raw(token) and uuid.UUID(OWNER).bytes not in _raw(token)  # safe in access logs


def test_tampered_wrong_secret_and_garbage_are_all_rejected():
    token = unsubscribe.mint(OWNER, 'anna@client.test', secret='s')
    assert token
    raw = bytearray(_raw(token))
    for i in (0, 20, len(raw) // 2, len(raw) - 1):
        forged = bytearray(raw)
        forged[i] ^= 1
        assert unsubscribe.parse(base64.urlsafe_b64encode(forged).decode().rstrip('='), secret='s') is None
    assert unsubscribe.parse(token, secret='other') is None
    assert unsubscribe.parse(token, secret='') is None
    for junk in ('', 'x', '!!!!', 'A' * 601, token[:20]):
        assert unsubscribe.parse(junk, secret='s') is None


def test_only_the_canonical_spelling_parses():
    """A lenient decoder would turn one token into many keys (the erasure limit, #172 review)."""
    token = unsubscribe.mint(OWNER, 'anna@client.test', secret='s')
    assert token
    for noisy in (token + '=', token + '.', '~' + token, token[:10] + '!' + token[10:], token + '\n'):
        assert unsubscribe.parse(noisy, secret='s') is None
    # Same bytes, different last character (unused low bits set): not ours either. A short address
    # makes a token length that is not a multiple of 3 bytes, so such spellings exist.
    token = unsubscribe.mint(OWNER, 'a@b.it', secret='s')
    assert token
    same_bytes = [token[:-1] + c for c in ALPHABET if c != token[-1] and _raw(token[:-1] + c) == _raw(token)]
    assert same_bytes and all(unsubscribe.parse(t, secret='s') is None for t in same_bytes)


def test_unconfigured_or_unusable_mints_nothing():
    assert unsubscribe.mint(OWNER, 'anna@client.test', secret='') is None
    assert unsubscribe.mint(OWNER, 'not an address', secret='s') is None
    assert unsubscribe.mint('not-a-uuid', 'anna@client.test', secret='s') is None


def test_urls_follow_the_unsubscribe_origin(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'web_origin', 'https://zenbree.test')
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.zenbree.test/')
    monkeypatch.setattr(settings, 'unsubscribe_origin', '')
    assert unsubscribe.page_url('tok') == 'https://zenbree.test/u/tok'
    assert unsubscribe.one_click_url('tok') == 'https://api.zenbree.test/v1/unsubscribe/tok'
    monkeypatch.setattr(settings, 'unsubscribe_origin', 'https://u.zenbree.test/')
    assert unsubscribe.page_url('tok') == unsubscribe.one_click_url('tok') == 'https://u.zenbree.test/x/tok'
