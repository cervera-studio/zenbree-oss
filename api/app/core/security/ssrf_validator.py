"""
SSRF URL validator — blocks requests to private/internal networks.
Must be called before any user-provided URL is fetched.

DNS rebinding (P1-SEC-01): `validate_url` resolves DNS once. Without
pinning, `httpx.get` then re-resolves at fetch time — an attacker with a
TTL=1 record can flip 8.8.8.8 → 169.254.169.254 (Azure IMDS) between the
two calls. `safe_httpx_get` defends against this by pinning the resolved
addresses for the duration of the fetch via a context-local
`socket.getaddrinfo` patch. The patch is a no-op outside an active pin
(empty contextvar by default).
"""

import asyncio
import contextlib
import ipaddress
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from app.core.errors import Result, err, is_err, ok
from app.core.security import dns_pin
from app.core.security.dns_pin import dialled_host, pin_dns

logger = structlog.get_logger()

_BLOCKED_HOSTS = frozenset(
    {
        'localhost',
        '127.0.0.1',
        '0.0.0.0',  # noqa: S104 — a blocklist entry, not a bind address
        '0',  # getaddrinfo('0') -> 0.0.0.0 -> routes to 127.0.0.1 on Linux
        '::1',
        'metadata.google.internal',
        'metadata.internal',
    }
)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network('0.0.0.0/8'),  # "this host"; 0.0.0.0 reaches loopback on Linux
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),  # Link-local + cloud metadata
    ipaddress.ip_network('100.64.0.0/10'),  # Carrier-grade NAT
    ipaddress.ip_network('::/127'),  # '::' (Linux dials it as loopback) and '::1'
    ipaddress.ip_network('fc00::/7'),  # IPv6 private
    ipaddress.ip_network('fe80::/10'),  # IPv6 link-local
]

# NAT64 maps an IPv4 address into the low 32 bits of this well-known
# prefix. We unwrap the embedded IPv4 and check it against the v4
# blocklist above (see _resolve_and_validate) rather than blanket-blocking
# the prefix, so a NAT64 address embedding a public IPv4 stays reachable
# while one embedding IMDS / RFC1918 is blocked.
_NAT64_PREFIX = ipaddress.ip_network('64:ff9b::/96')

SSRF_BLOCKED = 'SCRAPE.URL_BLOCKED'


@dataclass(frozen=True, slots=True)
class _ResolvedURL:
    """A URL whose hostname has been resolved + validated.

    `resolution` is the raw `socket.getaddrinfo` output, suitable for
    feeding into the pinned-DNS patch in `safe_httpx_get`.
    """

    url: str
    hostname: str
    resolution: list[tuple]


def resolve_and_validate_host(hostname: str) -> Result[list[tuple]]:
    """Resolve a bare hostname and validate every resolved address against
    the SSRF policy. Returns the raw ``socket.getaddrinfo`` resolution on
    success, suitable for pinning (connect to a validated address, never
    re-resolve).

    Hostname-level core of ``_resolve_and_validate``, exposed because the
    validating proxy (``app.auth.ssrf_proxy``) sees only ``host:port`` on a
    CONNECT — there is no URL to parse. Both callers must share this single
    policy: a second CIDR list would drift.
    """
    dialled = dialled_host(hostname)
    if dialled is None:
        return err(SSRF_BLOCKED, detail=f'Invalid hostname: {hostname[:100]}')
    hostname = dialled
    if hostname in _BLOCKED_HOSTS:
        return err(SSRF_BLOCKED, detail=f'Blocked host: {hostname}')

    try:
        resolved = dns_pin.original_getaddrinfo(
            hostname,
            None,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return err('SCRAPE.URL_UNREACHABLE', detail=f'DNS resolution failed: {hostname}')

    for _family, _, _, _, sockaddr in resolved:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            # Fail CLOSED: a resolved address we cannot parse would stay in
            # ``resolved`` and get dialed by ``_open_validated`` unchecked.
            # getaddrinfo never yields this for a real host (scoped v6 like
            # ``fe80::1%eth0`` parses), so blocking loses nothing legitimate
            # and matches the reject-if-ANY posture (#2362 review).
            return err(SSRF_BLOCKED, detail=f'Unparseable resolved address: {ip_str}')

        # Unwrap IPv6-mapped IPv4 (::ffff:a.b.c.d) so v4 CIDR checks apply.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped

        # Unwrap NAT64 (64:ff9b::/96 carries an IPv4 in its low 32 bits) so
        # a host resolving to e.g. 64:ff9b::a9fe:a9fe (= 169.254.169.254)
        # is checked against the v4 blocklist instead of slipping past it.
        if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_PREFIX:
            ip = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)

        for network in _BLOCKED_NETWORKS:
            try:
                if ip in network:
                    return err(SSRF_BLOCKED, detail=f'Blocked IP range: {ip_str}')
            except TypeError:
                # v4/v6 mismatch between ip and network — skip.
                continue

    return ok(resolved)


def candidate_addresses(resolution: list[tuple]) -> list[str]:
    """De-duplicated addresses from a ``getaddrinfo`` resolution,
    interleaved by family (v6/v4 alternating, resolver order within each
    family) so a blackholed first family does not serialize ahead of a
    reachable one.

    The interleave is not cosmetic. ``resolve_and_validate_host`` asks for
    ``AF_UNSPEC`` without ``AI_ADDRCONFIG``, so a dual-stack record yields
    its AAAA entries first even where egress is IPv4-only (ACA). Dialling
    in raw resolver order then spends the whole connect budget on
    addresses that cannot work before reaching the A record — which, for a
    caller that reads a connect timeout as a verdict about the URL, means
    live rows dropped.

    Shared by both mechanisms that dial a vetted address — the validation
    proxy and the pinned async transport — because two copies of an
    address-selection policy drift, and the one that drifts is the one
    nobody is looking at.
    """
    v6: list[str] = []
    v4: list[str] = []
    for _family, _stype, _proto, _cname, sockaddr in resolution:
        ip = str(sockaddr[0])
        bucket = v6 if ':' in ip else v4
        if ip not in bucket:
            bucket.append(ip)
    interleaved: list[str] = []
    for pair in zip(v6, v4, strict=False):
        interleaved.extend(pair)
    longer = v6 if len(v6) > len(v4) else v4
    interleaved.extend(longer[min(len(v6), len(v4)) :])
    return interleaved


#: The resolve-and-validate contract, named once. Everything that decides
#: "may we talk to this host, and at which addresses" is one of these:
#: :func:`resolve_and_validate_host` itself, the TTL cache below, and the
#: test doubles that stand in for them. Both the validation proxy and the
#: pinned async transport take one, which is what lets a test permit a
#: synthetic hostname without weakening the policy everywhere else.
Resolver = Callable[[str], Result[list[tuple]]]


class TTLResolverCache:
    """A short-TTL cache over the resolve-and-validate contract (#2315
    review #25): a page with 20 distinct hosts costs ~120 ``getaddrinfo``
    round-trips through the proxy without one, each on an executor thread.

    Caching here does not widen the anti-rebinding window — it NARROWS it:
    a cached ``Ok`` re-serves the addresses that were resolved AND
    validated together, so within the TTL a rebinding record cannot flip
    the connect target at all (the per-fetch pin already connects to the
    vetted address; this extends the same property across a fetch's
    repeated lookups). Only decided verdicts are cached — ``Ok``
    resolutions and policy blocks (``SSRF_BLOCKED``). Transient failures
    (DNS servfail, timeouts) are NOT cached, so recovery is immediate.

    Thread-safe; the wrapped resolver runs OUTSIDE the lock (a slow lookup
    must not serialize every other host's cache hit). Duplicate concurrent
    misses for one host resolve twice and last-write-wins — harmless.

    Lives here, beside :func:`resolve_and_validate_host`, because it is a
    DECORATOR OF THAT CONTRACT rather than a part of any one consumer.
    Both the validation proxy (``app.auth.ssrf_proxy``) and the pinned
    async transport (``app.auth.ssrf_async``) wrap the same resolver with
    it; housing it inside either would make the other depend on a sibling
    mechanism to get at a utility.

    **The safety criterion is the shape, not the concurrency model.** A
    cached verdict may be trusted only where the cached resolution IS the
    address that gets dialled — true of the proxy and of the pinned
    transport, which both connect to the vetted address. It is NOT true of
    the Playwright route guard, where the browser resolves again on its
    own: there a cached verdict would authorise an address nobody
    re-checked, which is why that path deliberately does not cache.
    """

    _TTL_S = 30.0
    _MAXSIZE = 256

    def __init__(
        self,
        resolve: Resolver,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._resolve = resolve
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Result[list[tuple]]]] = {}

    def __call__(self, host: str) -> Result[list[tuple]]:
        now = self._clock()
        with self._lock:
            hit = self._entries.get(host)
            if hit is not None and now - hit[0] < self._TTL_S:
                return hit[1]
        decision = self._resolve(host)
        if not is_err(decision) or decision.error.code == SSRF_BLOCKED:
            with self._lock:
                if len(self._entries) >= self._MAXSIZE:
                    # Drop the stalest entry — a bounded dict, not an LRU:
                    # the working set is one page's host fan-out.
                    oldest = min(self._entries, key=lambda h: self._entries[h][0])
                    del self._entries[oldest]
                self._entries[host] = (now, decision)
        return decision


def _resolve_and_validate(url: str) -> Result[_ResolvedURL]:
    """The actual SSRF validation. Returns hostname + resolved addrinfo
    on success so callers can pin DNS before fetching.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return err(SSRF_BLOCKED, detail=f'Invalid URL: {url[:100]}')

    if parsed.scheme not in ('http', 'https'):
        return err(SSRF_BLOCKED, detail=f'Blocked scheme: {parsed.scheme}')

    if not parsed.hostname:
        return err(SSRF_BLOCKED, detail='No hostname in URL')
    # The host as httpx reads and encodes it, not as urlparse does: httpx is what dials (#361).
    try:
        hostname = httpx.URL(url).raw_host.decode('ascii')
    except httpx.InvalidURL, UnicodeError:
        return err(SSRF_BLOCKED, detail=f'Invalid hostname in URL: {url[:100]}')
    if not hostname:
        return err(SSRF_BLOCKED, detail='No hostname in URL')

    host_check = resolve_and_validate_host(hostname)
    if is_err(host_check):
        return host_check

    return ok(_ResolvedURL(url=url, hostname=hostname, resolution=host_check.value))


def validate_url(url: str) -> Result[str]:
    """Validate a URL is safe to fetch. Returns the URL or SSRF error.

    Backward-compatible public API. Use `safe_httpx_get` for fetches that
    need DNS-rebinding protection — this function alone has a TOCTOU
    window between resolution and any subsequent fetch.
    """
    result = _resolve_and_validate(url)
    if is_err(result):
        return result
    return ok(result.value.url)


_MAX_REDIRECTS = 3

# Hard ceiling on the response body we will buffer into memory (~25 MiB).
# The direct-fetch path decodes the whole body before handing it to the
# extractor; without a cap a single multi-hundred-MB response can push a
# worker child past its RSS recycle threshold (or OOM the replica). Bodies
# past this are almost never a page the agent can use — abort instead of
# buffering.
MAX_RESPONSE_BYTES = 25 * 1024 * 1024

# Webhook receivers answer with an ack, not a document. 64 KiB is generous
# for that and stops a hostile endpoint streaming a body at us forever.
MAX_WEBHOOK_RESPONSE_BYTES = 64 * 1024


class ResponseTooLargeError(httpx.HTTPError):
    """Raised when a fetched body exceeds ``MAX_RESPONSE_BYTES``.

    Subclasses ``httpx.HTTPError`` so the gateway's exception→Result
    classification catches it like any other transport failure.
    """


class SSRFBlockedError(httpx.RequestError):
    """Raised when SSRF validation blocks a URL inside the safe fetch helpers
    (pre-flight in ``safe_httpx_post``, per-redirect-hop in ``safe_httpx_get``).

    Subclasses ``httpx.RequestError`` so callers that catch
    ``httpx.RequestError``/``httpx.HTTPError`` keep working unchanged; the
    dedicated type exists so security-sensitive callers can branch on
    ``isinstance`` instead of the ``'SSRF blocked'`` message prefix — a bare
    ``except Exception`` treating a block as a transient fetch failure is how
    a blocked URL ended up re-routed to an unguarded Chromium (#2311).
    """


def _read_capped(resp: httpx.Response, max_bytes: int) -> bytes:
    """Read a streamed response body, aborting once it exceeds ``max_bytes``.

    Two guards: an early ``Content-Length`` short-circuit (honest servers)
    and a running tally over the decoded stream (chunked or lying servers).
    The tally bounds the DECODED size — the bytes actually buffered — which
    is what matters for worker memory, regardless of content-encoding.
    """
    declared = resp.headers.get('content-length')
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLargeError(
            f'Response body {declared} bytes exceeds {max_bytes}-byte cap',
        )
    buf = bytearray()
    for chunk in resp.iter_bytes():
        buf += chunk
        if len(buf) > max_bytes:
            raise ResponseTooLargeError(
                f'Response body exceeds {max_bytes}-byte cap (streamed past limit)',
            )
    return bytes(buf)


def _capped_fetch(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str],
    max_bytes: int,
    method: str = 'GET',
    **kwargs: Any,
) -> httpx.Response:
    """Single-hop request that streams the body under a hard size cap.

    Returns a fully-read ``httpx.Response`` (``.content``/``.text``/``.json``
    all work) built from the capped body, so callers see the same surface as
    ``httpx.get``. Raises ``ResponseTooLargeError`` when the cap is exceeded.
    """
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        request = client.build_request(method, url, headers=headers, **kwargs)
        resp = client.send(request, stream=True)
        try:
            body = _read_capped(resp, max_bytes)
        finally:
            resp.close()
    # ``body`` is already decoded, so the origin's transfer/encoding/length
    # headers no longer describe it. Drop them and let httpx recompute
    # Content-Length from the buffered body; keep everything else (Location,
    # Content-Type, ...) intact for the redirect loop and callers.
    passthrough = httpx.Headers(
        [
            (name, value)
            for name, value in resp.headers.raw
            if name.lower() not in (b'content-encoding', b'content-length', b'transfer-encoding')
        ]
    )
    return httpx.Response(
        status_code=resp.status_code,
        headers=passthrough,
        content=body,
        request=request,
    )


def safe_httpx_get(
    url: str,
    *,
    timeout: float = 30,
    headers: dict[str, str] | None = None,
    max_redirects: int = _MAX_REDIRECTS,
    max_bytes: int = MAX_RESPONSE_BYTES,
    follow: Callable[[str, str], bool] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """
    httpx.get that re-validates every redirect hop against SSRF rules, pins the resolved IP between
    validation and fetch (DNS rebind defence — P1-SEC-01), AND caps the buffered response body at
    ``max_bytes``. A redirect that ``follow(source, target)`` rejects comes back unfollowed.

    Raises httpx.HTTPError subclass on SSRF violation, network failure, or an over-cap body
    (``ResponseTooLargeError``). Use instead of `httpx.get(..., follow_redirects=True)` anywhere a URL
    may be influenced by user input or untrusted content (sitemaps, HTML hrefs, API responses).
    """
    current = url
    for hop in range(max_redirects + 1):
        check = _resolve_and_validate(current)
        if is_err(check):
            # Only a genuine policy block is an SSRFBlockedError. A DNS
            # failure (URL_UNREACHABLE) is a transient network error, not a
            # security block: typing it as one would let raw_scrape skip its
            # legitimate browser fallback and mislabel it URL_BLOCKED (#2311).
            if check.error.code == SSRF_BLOCKED:
                raise SSRFBlockedError(
                    f'SSRF blocked at hop {hop}: {check.error.detail}',
                    request=httpx.Request('GET', current),
                )
            raise httpx.ConnectError(
                f'{check.error.detail} (hop {hop})',
                request=httpx.Request('GET', current),
            )
        resolved = check.value
        # Pin DNS for the duration of this hop's fetch. If an attacker's
        # DNS flips between resolve_and_validate above and the fetch below,
        # the patched socket.getaddrinfo returns the pre-validated IPs
        # instead of the new (possibly malicious) record. The pin must
        # cover the full streamed read, so the whole _capped_fetch call
        # happens inside the context.
        with pin_dns(resolved.hostname, resolved.resolution):
            resp = _capped_fetch(
                current,
                timeout=timeout,
                headers=headers or {'User-Agent': 'Mozilla/5.0'},
                max_bytes=max_bytes,
                **kwargs,
            )
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        location = resp.headers.get('location')
        target = urljoin(current, location or '')
        if not location or (follow and not follow(current, target)):
            return resp
        current = target
    raise httpx.RequestError(
        f'Too many redirects (>{max_redirects})',
        request=httpx.Request('GET', url),
    )


def safe_httpx_post(
    url: str,
    *,
    content: bytes,
    headers: dict[str, str],
    timeout: float = 10.0,
    max_bytes: int = MAX_WEBHOOK_RESPONSE_BYTES,
) -> httpx.Response:
    """POST behind the same SSRF guard + DNS pin as :func:`safe_httpx_get`.

    Exists for outbound webhooks (#1072): the delivery URL is user-supplied,
    which makes it exactly the untrusted-URL case the guard is for, and
    ``HttpGateway`` — the port that normally owns this — is GET-only, so a
    webhook could not have gone through it.

    Redirects are NOT followed. A 3xx from a webhook endpoint is a
    misconfiguration, not a contract, and returning it lets the caller
    surface that instead of chasing an attacker-chosen Location.
    """
    check = _resolve_and_validate(url)
    if is_err(check):
        # Same discrimination as safe_httpx_get: only a real policy block is
        # an SSRFBlockedError; a DNS failure is a transient network error the
        # webhook retry ladder may legitimately retry (#2311).
        if check.error.code == SSRF_BLOCKED:
            raise SSRFBlockedError(
                f'SSRF blocked: {check.error.detail}',
                request=httpx.Request('POST', url),
            )
        raise httpx.ConnectError(
            check.error.detail,
            request=httpx.Request('POST', url),
        )
    resolved = check.value
    with pin_dns(resolved.hostname, resolved.resolution):
        return _capped_fetch(
            url,
            method='POST',
            timeout=timeout,
            headers=headers,
            max_bytes=max_bytes,
            content=content,
        )


# ── the route-guard block ledger (#2315 review) ────────────────────────────
#
# A Playwright route handler cannot raise into the navigation: it can only
# ABORT, and Chromium then reports one of its generic ``net::ERR_ABORTED``
# / ``ERR_FAILED`` codes — the same codes a genuinely broken site
# produces. Classifying on that string was a guess (the first version of
# this said so itself: "advisory, not authoritative"), and it cost a retry
# on every unrelated navigation failure that happened to carry the code.
#
# So the guard RECORDS what it refuses — exactly as the validation proxy's
# ``_record_block`` does for the CONNECT path — and the arms classify on
# the record. Two things the error string could never carry:
#
# - ``is_navigation``: whether the refused request was the document itself
#   (or a redirect hop of it) rather than a subresource or a nested frame.
#   A blocked tracker pixel must not turn a good page into a policy block;
#   a blocked main document must.
# - the URL actually refused, for the message and for the log line — the
#   route guard used to abort in complete silence, so an SSRF attempt
#   against any of the five route-guarded arms left no trace at all.
#
# THREAD-LOCAL, not process-wide: the worker's multi-URL fan-out runs five
# scrapegraph runs in five threads of ONE process
# (``worker._scrape_multi_with_checkpoint``), and a shared ledger would
# attribute thread A's block to thread B's failure — the cross-wiring the
# #2315 design exists to avoid. Both guard flavours record on the thread
# that drives the navigation (``asyncio.run`` runs its loop in the calling
# thread; the sync API dispatches route handlers on the caller's thread
# via greenlets), so the record lands where the classifier reads it.
#
# Degrades SAFE. If an upstream ever moves navigation onto another thread,
# the ledger reads empty and the caller keeps its ordinary retriable
# classification: the guard still aborts, we merely stop being able to say
# it was us. The failure mode is a lost attribution, never a false claim
# that a block happened.

#: Cap on the per-thread ledger. Only the entries since a run's mark are
#: ever read, and a page that blocks more hops than this has made the
#: point already.
_ROUTE_BLOCK_LOG_MAXLEN = 64

#: How many refused URLs a classified error names before it stops listing.
_ROUTE_BLOCK_ERROR_SAMPLE = 5


@dataclass(frozen=True, slots=True)
class RouteBlock:
    """One request the route guard refused. ``is_navigation`` means the
    refusal took down the PAGE (a main-frame document or one of its
    redirect hops), not a subresource or a nested frame."""

    seq: int
    url: str
    is_navigation: bool


class _RouteBlockLedger(threading.local):
    """Per-thread block log. ``threading.local`` runs ``__init__`` once per
    thread on first access, so every fan-out thread starts empty."""

    def __init__(self) -> None:
        self.seq = 0
        self.blocks: deque[RouteBlock] = deque(maxlen=_ROUTE_BLOCK_LOG_MAXLEN)


_route_blocks = _RouteBlockLedger()


def route_guard_mark() -> int:
    """This thread's ledger position. Take it before a guarded run, hand it
    to :func:`route_guard_blocked_error` after."""
    return _route_blocks.seq


def route_guard_blocks_since(marker: int) -> list[RouteBlock]:
    """Every request this thread's guard refused after ``marker``."""
    return [block for block in _route_blocks.blocks if block.seq > marker]


def _record_route_block(url: str, *, is_navigation: bool) -> None:
    # No lock, unlike ``ssrf_proxy._record_block``: the state is
    # thread-local, and this function has no ``await`` in it, so
    # concurrent route handlers on one event loop cannot interleave
    # inside it.
    _route_blocks.seq += 1
    _route_blocks.blocks.append(
        RouteBlock(_route_blocks.seq, url, is_navigation),
    )
    # The counterpart of ``ssrf_proxy.blocked``: without it a refused hop
    # on a route-guarded arm is invisible to ops and to any alert.
    logger.warning(
        'ssrf_guard.blocked',
        url=url[:200],
        is_navigation=is_navigation,
    )


def _is_navigation_request(request: Any) -> bool:
    """Did refusing this request refuse the PAGE?

    ``is_navigation_request()`` alone is not that question: it is also
    true for an iframe navigating, and a refused ad iframe is far closer
    to a blocked tracker pixel than to a blocked document. So the main
    frame is required as well — which still covers the case that matters
    most, a main document 30x-ing into an internal host, because the
    browser re-issues each hop as a fresh main-frame navigation.

    Unknown counts as a navigation, on both probes. The flag is only ever
    consulted for a run that ALREADY failed or came back empty — a refusal
    on a page that rendered never reaches a classifier — so on a
    Playwright that renamed either probe, the conservative reading is that
    the refusal is what killed the run.
    """
    probe = getattr(request, 'is_navigation_request', None)
    if not callable(probe):
        return True
    try:
        if not probe():
            return False
    except Exception:
        return True
    try:
        frame = request.frame
    except Exception:
        return True
    # No frame information (service worker, a fake, a future Playwright):
    # keep the conservative reading.
    return frame is None or getattr(frame, 'parent_frame', None) is None


def route_guard_blocked_error(
    marker: int,
    url: str | None = None,
) -> SSRFBlockedError | None:
    """The classified error for a NAVIGATION the guard refused since
    ``marker``, or ``None`` when it refused none.

    Single construction site for the ``SSRF blocked at browser hop``
    message — the mirror of ``ssrf_proxy.ValidationProxySession
    .blocked_error`` for the arms that guard at the route layer instead of
    the CONNECT layer. Subresource blocks are deliberately not classified:
    a page that rendered without its tracker pixels is a successful scrape.

    ``url`` is the entry URL for the attached ``httpx.Request``; the
    prompt-driven graphs (``SearchGraph``, ``OmniSearchGraph``) have none,
    so it falls back to the first refused hop — which is the more useful
    subject anyway.
    """
    navigations = [block for block in route_guard_blocks_since(marker) if block.is_navigation]
    if not navigations:
        return None
    sample = navigations[:_ROUTE_BLOCK_ERROR_SAMPLE]
    listed = '; '.join(block.url[:200] for block in sample)
    if len(navigations) > len(sample):
        listed += f' (+{len(navigations) - len(sample)} more)'
    return SSRFBlockedError(
        f'SSRF blocked at browser hop: {listed}',
        request=httpx.Request('GET', url or navigations[0].url),
    )


# ── guard installation ─────────────────────────────────────────────────────

#: Attribute stamped on a Playwright ``Page``/``BrowserContext`` once the
#: route guard is registered on it. ONE constant, owned by the installer:
#: "this target already carries the shared route guard" is a single fact,
#: and it used to be two duplicated string literals (here and in
#: ``crawlee_adapter``) that nothing kept in step.
GUARD_INSTALLED_ATTR = '_ssrf_guard_installed'


def _guard_already_installed(target: Any) -> bool:
    return bool(getattr(target, GUARD_INSTALLED_ATTR, False))


def _mark_guard_installed(target: Any) -> None:
    # Stamped only AFTER a successful install, so a failed install is
    # retried on the next call. A target that rejects attribute writes just
    # falls back to (harmless) re-installation — the guard composes with
    # itself through ``fallback()``.
    with contextlib.suppress(AttributeError):
        setattr(target, GUARD_INSTALLED_ATTR, True)


async def install_playwright_ssrf_guard(context: Any) -> None:
    """
    Install a route handler on a Playwright BrowserContext that aborts
    any outgoing request whose URL fails SSRF validation. Covers main
    navigation redirects, XHR/fetch, iframe src, img/script src.

    Must be called right after `browser.new_context(...)`, before the
    first `page.goto(...)`.

    Install-once by construction: the marker check lives HERE, not in each
    caller. Every arm needs the same "one guard per target" rule, and the
    two hand-rolled copies it replaces (scrapegraph's ``_ensure_guard_*``,
    crawlee's ``pre_navigation_hook``) keyed on the same attribute name by
    coincidence of two string literals.

    Blocked hosts are aborted; allowed requests use ``route.fallback()``
    (NOT ``route.continue_()``) so the guard COMPOSES with any handler the
    library already registered on the same target — crawlee, for one, can
    register its own asset-blocking ``context.route('**/*')``, and
    ``continue_()`` would pre-empt it (Playwright runs the most-recently
    registered matching handler first). With no downstream handler,
    ``fallback`` continues to the network. This mirrors
    ``install_playwright_ssrf_guard_sync`` — register this guard LAST so it
    runs first.

    Fail-closed (mirrors the sync counterpart): any error while resolving
    or validating the request aborts it. Without this, an exception inside
    ``validate_url`` (e.g. a resolver blowing up on a crafted hostname)
    would propagate out of the route handler and Playwright would let the
    request proceed unguarded.
    """
    if _guard_already_installed(context):
        return

    async def _guard(route: Any) -> None:
        try:
            request = route.request
            url = request.url
        except Exception:
            # Cannot even read the request: refuse it. Reading a URL is the
            # precondition of validating one.
            with contextlib.suppress(Exception):
                await route.abort()
            return
        # ── phase 1: VERDICT (fail-closed) ──────────────────────────────
        # ``validate_url`` bottoms out in a blocking ``getaddrinfo``. Run
        # it OFF the event loop: a page with many hosts issues its
        # subresource requests concurrently, and resolving inline
        # serialises every one of them behind the DNS round-trips — on a
        # subresource-heavy page that alone can spend the graph's 60s
        # fetch budget.
        #
        # Deliberately NOT cached (unlike the validation proxy and the
        # pinned async transport, which both wrap their resolver in
        # ``TTLResolverCache``): there the cached verdict IS the address
        # dialled, so caching narrows the rebinding window. Here the
        # browser resolves again on its own, so a cached verdict would
        # authorise an address nobody re-checked — the freshness is the
        # only thing standing in for the pin this mechanism lacks.
        try:
            refused = is_err(await asyncio.to_thread(validate_url, url))
        except Exception:
            refused = True

        # ── phase 2: REFUSAL — record the evidence, then abort ──────────
        if refused:
            with contextlib.suppress(Exception):
                _record_route_block(
                    url,
                    is_navigation=_is_navigation_request(request),
                )
            with contextlib.suppress(Exception):
                await route.abort()
            return

        # ── phase 3: DISPOSITION of an ALLOWED request ──────────────────
        # OUTSIDE any region that records a block. Handing a request on is
        # not a security decision, and it used to sit inside the ``try``
        # whose ``except`` wrote a ``RouteBlock`` — so a failure to pass
        # on a request we had just ALLOWED would have been filed as a
        # refusal, and ``route_guard_blocked_error`` would then tell the
        # user, through ``error_detail``, that we blocked a host we did
        # not block (#2375 review, F1).
        #
        # It never fired on this API by luck rather than by design:
        # ``Route.fallback`` is local bookkeeping with no IPC
        # (``playwright/_impl/_network.py``). The sync twin has a real
        # window — ``_sync_base._sync`` raises on a route that fires after
        # teardown — and there the request was abandoned, not refused.
        # The invariant "never claim a block that did not happen" is now
        # held by the SHAPE of the code, rather than by a Playwright
        # internal that no test of ours pins.
        #
        # If handing off fails the route still needs A disposition — an
        # undisposed route hangs the navigation until its own timeout —
        # so it is aborted, just WITHOUT a ledger entry. Abort and record
        # were one act before; separating them is the fix, dropping the
        # abort would have been a new bug.
        try:
            await route.fallback()
        except Exception:
            with contextlib.suppress(Exception):
                await route.abort()

    await context.route('**/*', _guard)
    _mark_guard_installed(context)


def install_playwright_ssrf_guard_sync(target: Any) -> None:
    """Synchronous counterpart of ``install_playwright_ssrf_guard`` for the
    ``playwright.sync_api`` surface (CloakBrowser's CDP client, and any
    other sync-Playwright adapter).

    Registers a route handler on a sync ``Page`` or ``BrowserContext`` that
    aborts any request whose URL fails SSRF validation. Because the browser
    re-issues every 30x redirect target as a fresh request, this handler
    runs again on each hop — so a public entry URL that redirects into an
    internal host (169.254.169.254, RFC1918, ACA CG-NAT) is blocked at the
    hop, not just at entry.

    Blocked hosts are aborted; everything else is passed on with
    ``route.fallback()`` rather than ``route.continue_()`` so the guard
    composes with any handler already registered on the same target (e.g.
    an asset-blocking handler) instead of pre-empting it. With no downstream
    handler, ``fallback`` continues to the network. Register this guard
    LAST so it runs first (Playwright invokes the most-recently-registered
    matching handler first).

    Fail-closed: any error while resolving or validating the request aborts
    it. Install-once, and every refusal recorded, exactly as in the async
    counterpart.
    """
    if _guard_already_installed(target):
        return

    def _guard(route: Any) -> None:
        try:
            request = route.request
            url = request.url
        except Exception:
            with contextlib.suppress(Exception):
                route.abort()
            return
        # Phase 1 — VERDICT (fail-closed). No executor hop here, unlike the
        # async guard: the sync API dispatches route handlers one at a time
        # on the caller's thread, so there is no concurrency for an
        # off-thread resolve to unblock — it would only add a hand-off.
        try:
            refused = is_err(validate_url(url))
        except Exception:
            refused = True

        # Phase 2 — REFUSAL: record the evidence, then abort.
        if refused:
            with contextlib.suppress(Exception):
                _record_route_block(
                    url,
                    is_navigation=_is_navigation_request(request),
                )
            with contextlib.suppress(Exception):
                route.abort()
            return

        # Phase 3 — DISPOSITION of an ALLOWED request, outside the recorded
        # region. This twin is where the window is REAL: ``_sync_base._sync``
        # raises ``Error("Event loop is closed!")`` on a route that fires
        # after teardown, and that is a request we abandoned — never one we
        # refused. See the async counterpart for the full reasoning —
        # including why a failed hand-off is still aborted, only without
        # a ledger entry.
        try:
            route.fallback()
        except Exception:
            with contextlib.suppress(Exception):
                route.abort()

    target.route('**/*', _guard)
    _mark_guard_installed(target)
