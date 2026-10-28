"""Sites a chat session named (#407, #437). fetch_page reads only these, so text on a fetched page cannot make
the agent send chat context to a host that no user message, web_search result or list row named, nor in a URL
of a web_search result's host that no result or read page linked."""

from __future__ import annotations

import re
from collections.abc import Set
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit

from publicsuffixlist import PublicSuffixList  # type: ignore[import-untyped]  # the library ships no stubs

# a dotted name in free text: the host of a URL, a bare domain or the domain of an email. Bounded like DNS
# names, because an unbounded pattern backtracks for seconds on a 20k-char 'a.a.a.' message
_DOMAIN = re.compile(r'(?:[\w-]{1,63}\.){1,8}[^\W\d_]{2,63}')
# the target of a markdown link or autolink as html_to_text writes it, with its escapes
_LINK = re.compile(r'(?:\]\(|<)(https?://(?:\\.|[^)\s>\\])+)')
_ESCAPE = re.compile(r'\\(.)')
ROW_HOST_KEYS = ('domain', 'website', 'company_domain')
_PSL = PublicSuffixList()


def _split(value: object) -> tuple[str, SplitResult, int | None] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        parts = urlsplit(text if '//' in text else f'//{text}')
        host = (parts.hostname or '').rstrip('.').removeprefix('www.')
        port = parts.port
    except ValueError:
        return None
    # httpx sends userinfo as an Authorization header, a channel for chat context like the query string
    if parts.username is not None or parts.password is not None:
        return None
    return (host, parts, port) if '.' in host else None


def host_of(value: object) -> str | None:
    """The host of a URL or a bare domain, lowercased without ``www.``; None when it has no dot."""
    split = _split(value)
    return split[0] if split else None


def page_of(value: object) -> tuple[str, str] | None:
    """``(host, ':port' + path + '?query')`` of a URL, without one trailing slash; None without a host."""
    if not (split := _split(value)):
        return None
    host, parts, port = split
    page = (f':{port}' if port else '') + parts.path.removesuffix('/')
    return host, f'{page}?{parts.query}' if parts.query else page


def hosts_in(text: str) -> set[str]:
    return {h for m in _DOMAIN.finditer(text) if (h := host_of(m.group()))}


def row_hosts(cells: dict) -> set[str]:
    # save_to_list rows are the model's copy of a page it read, so injected text could name any host there
    if cells.get('source') == 'web':
        return set()
    return {h for k in ROW_HOST_KEYS if (h := host_of(cells.get(k)))}


def is_named(url: str, hosts: Set[str]) -> bool:
    """True when the host of ``url``, or a parent domain of it, is in ``hosts``. The walk stops below a
    public suffix (#437): a named ``co.uk`` or ``pages.dev`` names none of the sites under it."""
    host = host_of(url)
    while host:
        if host in hosts:
            return True
        host = host.partition('.')[2]
        if _PSL.is_public(host):
            return False
    return False


@dataclass
class Named:
    """What a session named. A user message or a vendor row names a host and its subdomains. A web_search
    result names only its own page, and a page read from that host names the links on it to the same host:
    such a host may be the attacker's, and a path the agent writes itself can carry chat context (#437). A
    page is fetched as it was named, because the agent's spelling (scheme, ``www.``, slash) carries bits too.

    ponytail: which of a page's links the agent reads is still a narrow channel: a page can offer one link
    per character. Closing it means reading no page the agent picks."""

    hosts: set[str] = field(default_factory=set)
    pages: dict[str, dict[str, str]] = field(default_factory=dict)  # host -> page_of key -> the URL named

    def add_page(self, url: object) -> None:
        if isinstance(url, str) and (page := page_of(url)):
            self.pages.setdefault(page[0], {}).setdefault(page[1], url.strip())

    def add_links(self, url: object, markdown: str) -> None:
        host = host_of(url)
        for link in (_ESCAPE.sub(r'\1', m) for m in _LINK.findall(markdown)):
            if host_of(link) == host:
                self.add_page(link)

    def add_result(self, tool: str, data: dict, cells: list[dict]) -> None:
        """Name what a tool result names: the hosts of its rows, a search's result pages, a read page's links."""
        for row in cells:
            self.hosts |= row_hosts(row)
        if tool == 'web_search':
            for r in data.get('results') or []:
                self.add_page(r.get('url'))
        if tool == 'fetch_page':
            self.add_links(data.get('url'), data.get('content') or '')

    def resolve(self, url: str) -> str | None:
        """The URL to fetch for ``url``, None when nothing named it."""
        if is_named(url, self.hosts):
            return url
        page = page_of(url)
        return self.pages.get(page[0], {}).get(page[1]) if page else None

    def follows(self, source: str, target: str) -> bool:
        # a redirect on one host carries no more than the request that resolve() gave, but a trusted host's
        # open redirect would carry any URL to a host that only web_search found
        return self.resolve(target) == target or host_of(target) == host_of(source)
