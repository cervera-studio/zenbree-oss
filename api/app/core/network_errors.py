"""One table mapping network failure strings to stable error classes.

Two hand-written copies of this used to exist:
``scraping/adapters/httpx_gateway._ERROR_PATTERNS`` with six entries, and
``chat/tools/executor._ERROR_CLASS_PATTERNS`` with four. They agreed
character-for-character on the four they shared and disagreed by omission on
the other two — ``ssl_eof`` and ``network_unreachable``, added to the gateway
by the PR that introduced them and never carried across. Exactly the shape the
domain-health policy rewrite is a correction for, one layer up.

The omission was not cosmetic. It cost both of the things the table exists to
buy:

- **The breaker never saw them.** An SSL-EOF surfacing through the tool
  executor's catch-all landed in the untagged fallback, so ``record_failure``
  was never reached — the same silent no-op as a trippable class with no TTL.
- **The prompt cache paid for it.** The whole point of the mapping, per the
  executor's own note, is that raw ``str(exc)`` "embeds variable metadata —
  hostname, ``_ssl.c:1006``, wrapped stack traces — that makes each failure
  look different to the Anthropic prompt cache even when the *cause* is the
  same domain being broken". An unclassified ``SSLEOFError`` carries an
  ``_ssl.c:`` line number straight into the tool_result. The table built to
  stop cache pollution was letting the two most frequent classes through.

Lives in ``shared/`` because it is genuinely cross-cutting: the scraping
transport and the chat tool surface both classify the same OS and httpx error
strings, and ``app.chat`` may not import ``app.scraping`` (see
``tests/unit/test_no_cross_context_imports.py``).

Substring matching on ``str(exc)`` is deliberate, not lazy: the httpx / ssl /
urllib exception hierarchies are too leaky to dispatch on, and these
substrings are what actually show up in prod logs. Verified against a Linux
container on 2026-07-26 — the messages are glibc's, and Windows differs, so
check any new pattern against the real runtime rather than a dev laptop.
"""

from __future__ import annotations

# (substring in str(exc), error_class, stable agent-facing message)
#
# ORDER IS LOAD-BEARING — first match wins, so specific patterns precede
# general ones. In particular ``timed out`` sits ahead of the DNS and routing
# entries because a connect timeout to a firewalled host says "timed out" and
# must classify as a timeout, which is deliberately NOT trippable.
#
# The messages are prompt contract: they reach the model verbatim and the
# golden snapshots lock them. Changing one is a prompt change.
NETWORK_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (
        'CERTIFICATE_VERIFY_FAILED',
        'ssl_cert_invalid',
        'TLS certificate invalid for this hostname. The domain is unreachable — do not retry this URL or its siblings.',
    ),
    (
        'UNEXPECTED_EOF_WHILE_READING',
        'ssl_eof',
        'TLS handshake truncated. The server closed the connection before '
        'completing TLS — do not retry within this turn.',
    ),
    (
        'Too many redirects',
        'redirect_loop',
        'The domain redirects in a loop. Do not retry this URL or its siblings on the same host.',
    ),
    (
        'timed out',
        'timeout',
        'Request timed out. The host may be slow or unreachable from our network. Try again once or move on.',
    ),
    (
        'Name or service not known',
        'dns_failed',
        'DNS resolution failed — this domain does not exist. Do not retry. Use web_search to find a real URL.',
    ),
    (
        'Network is unreachable',
        'network_unreachable',
        'Network unreachable from our scraping infrastructure. Likely a '
        'host-side routing or firewall issue — do not retry this URL.',
    ),
]


def classify_network_error(raw: str) -> tuple[str, str] | None:
    """``(error_class, stable message)`` for a raw exception string, or None.

    None means "not a network failure we recognise" — the caller keeps its own
    fallback, which differs by layer: the gateway answers
    ``SCRAPE.HTTP_ERROR``, the tool executor returns the truncated raw string.
    """
    for needle, error_class, message in NETWORK_ERROR_PATTERNS:
        if needle in raw:
            return error_class, message
    return None


def scrape_code(error_class: str) -> str:
    """``'ssl_eof'`` → ``'SCRAPE.SSL_EOF'``.

    The gateway speaks the ``SCRAPE.*`` taxonomy while the tool layer speaks
    bare ``error_class`` tags, and ``fetch_page._err_to_tool_result`` already
    converts one into the other with ``code.removeprefix('SCRAPE.').lower()``.
    That round-trip was an unwritten assumption holding two tables together;
    this is the same assumption, written down and used in one direction, so
    the tables can be one.
    """
    return f'SCRAPE.{error_class.upper()}'
