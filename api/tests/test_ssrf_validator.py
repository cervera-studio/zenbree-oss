"""SSRF guard (#361): the name that is validated and pinned is the name httpx dials."""

import ipaddress
import socket

import httpx
import pytest

from app.core.errors import is_err
from app.core.security import dns_pin, ssrf_validator
from app.core.security.ssrf_validator import SSRFBlockedError, safe_httpx_get, safe_httpx_post, validate_url

PUBLIC, OTHER_PUBLIC, INTERNAL = '93.184.216.34', '93.184.216.35', '172.18.0.5'


@pytest.fixture
def dns(monkeypatch):
    """A fake resolver: ``{name: ip}``; an IP literal resolves to itself, anything else fails."""
    records: dict[str, str] = {}

    def resolve(host, port, family=0, type=0, proto=0, flags=0):
        name = host.decode('ascii') if isinstance(host, bytes) else host
        if name not in records:
            try:
                ipaddress.ip_address(name)
            except ValueError:
                raise socket.gaierror(socket.EAI_NONAME, f'no record for {name}') from None
        ip = records.get(name, name)
        family = socket.AF_INET6 if ':' in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, '', (ip, port or 0))]

    monkeypatch.setattr(dns_pin, 'original_getaddrinfo', resolve)
    return records


@pytest.fixture
def dialled(monkeypatch, dns):
    """What httpx dials: each ``(host, addresses)`` it looks up on connect. The attacker's DNS
    flips to an internal address just before that lookup; no connection is opened."""
    seen: list[tuple[str, list[str]]] = []

    def connect(address, *args, **kwargs):
        host, port = address
        for name in list(dns):
            dns[name] = INTERNAL
        seen.append((host, [info[4][0] for info in socket.getaddrinfo(host, port)]))
        raise OSError('test: no network')

    monkeypatch.setattr(socket, 'create_connection', connect)
    return seen


@pytest.mark.parametrize(
    ('host', 'wire'),
    [
        ('ß.attacker.test', 'xn--zca.attacker.test'),  # std-lib IDNA 2003 would resolve ss.attacker.test
        ('ς.attacker.test', 'xn--3xa.attacker.test'),  # ... and σ.attacker.test here
        ('ü.attacker.test', 'xn--tda.attacker.test'),
        ('a。attacker.test', 'a.attacker.test'),  # ideographic full stop as the dot
        ('MiXed.Attacker.Test', 'mixed.attacker.test'),
    ],
)
def test_the_validated_and_pinned_name_is_the_one_httpx_dials(dns, dialled, host, wire):
    dns[wire] = PUBLIC
    with pytest.raises(httpx.ConnectError):
        safe_httpx_get(f'http://{host}:5555/x')
    # Looked up under the wire name, and answered from the pin although DNS has flipped since.
    assert dialled == [(wire, [PUBLIC])]


def test_a_host_whose_dialled_name_is_internal_is_blocked(dns, dialled):
    dns['ss.attacker.test'] = OTHER_PUBLIC  # what the std-lib encoder would have checked
    dns['xn--zca.attacker.test'] = INTERNAL  # what httpx dials
    url = 'http://ß.attacker.test:5555/flower/tasks/datatable'
    assert validate_url(url).error.code == ssrf_validator.SSRF_BLOCKED
    with pytest.raises(SSRFBlockedError):
        safe_httpx_get(url)
    with pytest.raises(SSRFBlockedError):
        safe_httpx_post(url, content=b'{}', headers={})
    assert dialled == []


def test_post_pins_the_dialled_name_too(dns, dialled):
    dns['xn--zca.attacker.test'] = PUBLIC
    with pytest.raises(httpx.ConnectError):
        safe_httpx_post('https://ß.attacker.test/hook', content=b'{}', headers={})
    assert dialled == [('xn--zca.attacker.test', [PUBLIC])]


def test_a_redirect_to_a_non_ascii_host_is_checked_under_its_dialled_name(dns, monkeypatch):
    dns['public.test'] = PUBLIC
    dns['ss.attacker.test'] = OTHER_PUBLIC
    dns['xn--zca.attacker.test'] = INTERNAL

    def redirect(url, **kwargs):
        location = 'http://ß.attacker.test/'.encode()  # raw UTF-8 on the wire, as a server sends it
        return httpx.Response(302, headers={'location': location}, request=httpx.Request('GET', url))

    monkeypatch.setattr(ssrf_validator, '_capped_fetch', redirect)
    with pytest.raises(SSRFBlockedError, match='hop 1'):
        safe_httpx_get('http://public.test/')


def test_an_unpinned_name_is_refused_while_a_pin_is_active(dns):
    dns['pinned.test'] = PUBLIC
    dns['other.test'] = OTHER_PUBLIC
    resolution = ssrf_validator.resolve_and_validate_host('pinned.test').value
    with dns_pin.pin_dns('pinned.test', resolution):
        assert socket.getaddrinfo('pinned.test', 80)[0][4] == (PUBLIC, 80)
        assert socket.getaddrinfo(b'PINNED.test', 443)[0][4] == (PUBLIC, 443)
        for unpinned in ('other.test', b'other.test', 'ß.attacker.test', 'ß.attacker.test'.encode()):
            with pytest.raises(socket.gaierror, match='not the pinned host'):
                socket.getaddrinfo(unpinned, 80)
    assert socket.getaddrinfo('other.test', 80)[0][4] == (OTHER_PUBLIC, 80)  # no pin: the resolver as usual


@pytest.mark.parametrize('host', ['a‍b.attacker.test', 'ｅxample.test', 'exa mple.test'])
def test_a_host_httpx_cannot_dial_is_refused(dns, host):
    dns['example.test'] = PUBLIC
    assert is_err(validate_url(f'http://{host}/'))
    assert is_err(ssrf_validator.resolve_and_validate_host(host))


def test_plain_hosts_and_ip_literals_keep_their_verdict(dns):
    dns['example.test'] = PUBLIC
    dns['internal.test'] = INTERNAL
    for allowed in ('http://example.test/', f'https://{PUBLIC}/x', 'http://[2606:2800:220:1::1]/'):
        assert validate_url(allowed).value == allowed
    for blocked in (
        'http://internal.test/',
        'http://127.0.0.1/',
        'http://[::1]/',
        'http://[::]/',  # unspecified: Linux dials it as loopback
        'http://LOCALHOST/',
        'ftp://example.test/',
    ):
        assert validate_url(blocked).error.code == ssrf_validator.SSRF_BLOCKED
    assert ssrf_validator.resolve_and_validate_host('2606:2800:220:1::1').value[0][4][0] == '2606:2800:220:1::1'


class _Route:
    def __init__(self, url: str) -> None:
        self.request = type('Request', (), {'url': url, 'is_navigation_request': lambda self: False})()
        self.done: str | None = None

    async def abort(self) -> None:
        self.done = 'abort'

    async def fallback(self) -> None:
        self.done = 'fallback'


@pytest.mark.parametrize(('host', 'done'), [(INTERNAL, 'abort'), (PUBLIC, 'fallback')])
async def test_the_browser_guard_validates_off_the_loop_and_keeps_its_verdict(dns, host, done):
    """The resolve runs in asyncio.to_thread (E9 refuses a bare run_in_executor): the verdict is the same."""
    handlers = []

    class Context:
        async def route(self, pattern, handler):
            handlers.append(handler)

    await ssrf_validator.install_playwright_ssrf_guard(Context())
    route = _Route(f'http://{host}/')
    await handlers[0](route)
    assert route.done == done
