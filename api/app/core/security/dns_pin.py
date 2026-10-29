"""DNS pinning for the SSRF guard: which name gets dialled, and which addresses it may resolve to.

`ssrf_validator` validates a resolution, then pins it here for the duration of the fetch, so the
socket layer connects to the addresses that were checked and to no others (DNS rebinding,
P1-SEC-01). Importing this module installs the `socket.getaddrinfo` patch; it is a no-op while
no pin is active.
"""

import contextlib
import contextvars
import socket
from contextlib import contextmanager
from typing import Any

import httpx

# ── Context-local DNS pin ──────────────────────────────────────────────────
#
# Why a contextvar (not a thread-local): FastAPI handlers are coroutines
# multiplexed on the same event-loop thread, so threading.local would
# leak pins across concurrent requests. ContextVars are isolated per
# task and propagate across `await` boundaries.
#
# Why monkey-patch socket.getaddrinfo (rather than a custom httpx
# transport): httpx, requests, urllib3, and any std-lib client all
# bottom out on `socket.getaddrinfo` for sync DNS. Patching there gives
# us coverage with one hook and zero per-caller wiring.

# Default is None (not `{}`) — ruff B039: mutable defaults on ContextVars
# can leak across contexts when the value is reassigned by reference.
_pinned_dns: contextvars.ContextVar[dict[str, list[tuple]] | None] = contextvars.ContextVar(
    'ssrf_pinned_dns',
    default=None,
)
original_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(
    host: Any,
    port: Any,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple]:
    """Drop-in for `socket.getaddrinfo` that returns the pre-validated
    resolution for hostnames pinned via `pin_dns`. No-op while no pin is
    active (delegates to the original). While one is, a name that is not
    pinned is refused: the fetch inside the pin dials only the name that
    was validated, so any other lookup is one the guard never saw (#361).

    ``host`` is normalised from ``bytes`` before the lookup: the std-lib
    signature accepts either, and callers really do pass both — asyncio's
    resolver hands ``getaddrinfo`` a ``bytes`` host. Matching only ``str``
    would silently miss the pin for those callers and fall through to the
    live resolver, which is the failure mode this patch exists to prevent
    (it fails OPEN, so no test of the pinned path would go red).
    """
    pinned = _pinned_dns.get()
    key: str | None = None
    if isinstance(host, str):
        key = host
    elif isinstance(host, (bytes, bytearray)):
        # Pins are keyed by the ASCII name httpx dials, so a host that is
        # not ASCII matches none and is refused below.
        with contextlib.suppress(UnicodeDecodeError):
            key = bytes(host).decode('ascii')
    if not pinned:
        return original_getaddrinfo(host, port, family, type, proto, flags)
    base = pinned.get(key.lower()) if key is not None else None
    if base is None:
        raise socket.gaierror(socket.EAI_NONAME, f'{host!r} is not the pinned host: refusing an unvalidated lookup')

    # Caller may request a specific address family (AF_INET, AF_INET6) —
    # filter the pinned list to match. AF_UNSPEC (=0) accepts both.
    if family and family != socket.AF_UNSPEC:
        base = [t for t in base if t[0] == family]
    if not base:
        raise socket.gaierror(
            socket.EAI_NONAME,
            f'pinned DNS for {host} has no AF={family} entry',
        )

    # Substitute the requested port into each sockaddr (validate_url calls
    # getaddrinfo with port=None; httpx asks for port=80/443 etc.).
    return [(fam, stype, prto, cname, (sa[0], port, *sa[2:])) for fam, stype, prto, cname, sa in base]


# Install once at import. A no-op for unpinned hostnames.
socket.getaddrinfo = _patched_getaddrinfo


@contextmanager
def pin_dns(hostname: str, resolution: list[tuple]):
    """Pin `socket.getaddrinfo(hostname, ...)` to `resolution` within the
    `with` block. Stacks correctly with outer pins.
    """
    snapshot = _pinned_dns.get() or {}
    token = _pinned_dns.set({**snapshot, hostname: resolution})
    try:
        yield
    finally:
        _pinned_dns.reset(token)


def dialled_host(host: str) -> str | None:
    """The ASCII name httpx puts on the wire for ``host``, or None when httpx cannot encode it.

    httpx encodes with IDNA 2008 and the std-lib resolver with IDNA 2003. They disagree on
    ``ß`` (``xn--zca`` against ``ss``), ``ς`` and ZWJ/ZWNJ, so a Unicode host checked through
    ``getaddrinfo`` is not the host that gets dialled (#361). Everything below works on this name.
    """
    try:
        return httpx.URL(scheme='http', host=host).raw_host.decode('ascii')
    except httpx.InvalidURL, UnicodeError:
        return None
