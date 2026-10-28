"""Per-project exclusions (#307): who the client said never to find, enrich or contact.

Ported from the earlier implementation ``outreach/domain/exclusions.py`` (address | domain), plus the
company name and LinkedIn URL kinds. Values normalise like list rows
(``app.lists.rows.norm_host`` / ``norm_url``) so a rule matches the row it names.

Where they apply: search results never reach a List (``agent/loop.py``),
enrichment skips them (``enrich_email.load_rows``), campaign refill never
enrolls them (``scheduling/runner.py``). The send path (not built yet) must
call ``load_rules`` + ``is_excluded`` on each recipient before sending. A list or a campaign is checked against
the rules of its own project.
"""

from __future__ import annotations

import re
from typing import Literal

from app.lists.rows import norm_host, norm_url

Kind = Literal['email', 'domain', 'company_name', 'linkedin_url']
KINDS: tuple[Kind, ...] = ('email', 'domain', 'company_name', 'linkedin_url')
Rules = frozenset[tuple[str, str]]

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_DOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$')

# Shared hosts: a company's social page in a Website cell must not exclude everyone on the platform.
SHARED_HOSTS = frozenset(
    {
        'facebook.com',
        'fb.com',
        'instagram.com',
        'linkedin.com',
        'twitter.com',
        'x.com',
        'youtube.com',
        'youtu.be',
        'tiktok.com',
        'pinterest.com',
        'github.com',
        'medium.com',
        'google.com',
        'linktr.ee',
    }
)


def guess_kind(value: str) -> Kind:
    """The kind a bare value (a CSV cell) most likely is."""
    v = (value or '').strip().lower()
    if 'linkedin.com/' in v:
        return 'linkedin_url'
    if _EMAIL_RE.match(v):
        return 'email'
    if _DOMAIN_RE.match(norm_host(v.lstrip('@'))) and ' ' not in v:
        return 'domain'
    return 'company_name'


# Profile lines that read as a kind of company, not a name (#193).
_GENERIC = frozenset(
    {
        'agencies',
        'agenzie',
        'clienti',
        'competitor',
        'competitors',
        'concorrenti',
        'consultants',
        'consulenti',
        'customers',
        'freelancer',
        'freelancers',
        'fornitori',
        'partner',
        'partners',
        'startup',
        'startups',
        'suppliers',
    }
)
_NAME_JOINERS = frozenset({'&', 'and', 'e', 'of', 'the', 'di', 'de', 'del', 'della', 'dei'})


def profile_rule(line: str) -> tuple[Kind, str] | None:
    """The rule a profile exclusion line names, or None for a description ("competitors").

    Emails, domains and LinkedIn URLs always parse. A company name is up to four
    capitalised words ("Acme Srl", "HubSpot"): a lowercase word or punctuation
    reads as a sentence and stays brief-only text.
    """
    # ponytail: capitalisation heuristic; a lowercase name ("acme") or a generic capitalised
    # word outside _GENERIC becomes a name rule. Harmless: name rules match exact names only.
    kind = guess_kind(line)
    if kind == 'company_name':
        words = line.split()
        if not 0 < len(words) <= 4 or any(c in line for c in ':;,()/') or line.lower().strip() in _GENERIC:
            return None
        if not all(w[0].isupper() or w[0].isdigit() or w.lower() in _NAME_JOINERS for w in words):
            return None
    value = normalise(kind, line)
    return (kind, value) if value else None


def normalise(kind: str, value: str) -> str | None:
    """The stored form of ``value``, or None when it cannot be one of ``kind``."""
    v = (value or '').strip().lower()
    if kind == 'email':
        return v if _EMAIL_RE.match(v) and len(v) <= 320 else None
    if kind == 'domain':
        v = norm_host(v.lstrip('@'))
        if _domains(v) & SHARED_HOSTS:
            return None
        return v if _DOMAIN_RE.match(v) and len(v) <= 253 else None
    if kind == 'company_name':
        v = ' '.join(v.split())
        return v if 0 < len(v) <= 300 else None
    if kind == 'linkedin_url':
        v = norm_url(v)
        return v if 'linkedin.com/' in v and len(v) <= 500 else None
    return None


def _domains(host: str) -> set[str]:
    """``eu.acme.com`` -> {eu.acme.com, acme.com}: a domain rule covers its subdomains."""
    parts = host.split('.')
    return {'.'.join(parts[i:]) for i in range(len(parts) - 1)} if host else set()


def row_keys(cells: dict) -> set[tuple[str, str]]:
    """Every (kind, value) a company or person row can be excluded by."""
    keys: set[tuple[str, str]] = set()
    email = normalise('email', cells.get('email') or '')
    if email:
        keys.add(('email', email))
        keys |= {('domain', d) for d in _domains(email.rsplit('@', 1)[1])}
    for k in ('domain', 'website', 'company_domain'):
        keys |= {('domain', d) for d in _domains(norm_host(cells.get(k)))}
    for k in ('name', 'company_name'):
        if name := normalise('company_name', cells.get(k) or ''):
            keys.add(('company_name', name))
    if li := normalise('linkedin_url', cells.get('linkedin_url') or ''):
        keys.add(('linkedin_url', li))
    return keys


def is_excluded(rules: Rules, cells: dict) -> bool:
    return bool(rules) and not rules.isdisjoint(row_keys(cells))


def load_rules(conn, project_id) -> Rules:
    """The project's rules, and the other exact aliases (#937) of each person or account a rule names: an opt-out
    of an email also covers the LinkedIn URL and the other emails we know for that person, in this project only."""
    # ponytail: loads the project's whole list per call; cache per job if lists grow past ~100k
    rules = frozenset(
        (r['kind'], r['value'])
        for r in conn.execute('SELECT kind, value FROM exclusions WHERE project_id = %s', (project_id,)).fetchall()
    )
    from app.audience.follow import with_aliases  # it imports this module

    return with_aliases(conn, project_id, rules)


def list_rules(conn, list_id) -> Rules:
    """The rules of the list's project."""
    row = conn.execute('SELECT project_id FROM lists WHERE id = %s', (list_id,)).fetchone()
    return load_rules(conn, row['project_id']) if row and row['project_id'] else frozenset()
