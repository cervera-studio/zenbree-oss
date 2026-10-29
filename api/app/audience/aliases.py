"""The stored form of an alias value, in Python.

It is the exclusion normalizer (``app.exclusions.rules.normalise``), so an alias matches the cell it came from
exactly as an exclusion rule does, plus one rule: a role address never names a person. The SQL function
``audience_alias_value`` (``api/db/20261016190050_audience_identity.sql``) does the same for the writers in SQL;
``tests/test_audience_sql.py`` compares the two. A name is never an alias.
"""

from __future__ import annotations

import re

from app.exclusions.rules import normalise

# The kinds an exclusion rule can name; the others have no rule of their own.
RULE_KINDS = ('email', 'linkedin_url', 'domain')
# A shared mailbox is read by everyone behind it: aliasing it would join people who only share an employer.
ROLE_LOCAL_PARTS = frozenset(
    {
        'info', 'sales', 'contact', 'contacts', 'hello', 'hi', 'support', 'help', 'admin', 'office', 'team', 'mail',
        'enquiries', 'inquiries', 'marketing', 'billing', 'accounts', 'service', 'customerservice', 'jobs',
        'careers', 'hr', 'press', 'media', 'webmaster', 'postmaster', 'noreply', 'no-reply', 'donotreply',
        'do-not-reply', 'reception', 'general', 'booking', 'bookings', 'orders',
    }
)  # fmt: skip


def normalize(kind: str, raw: str | None) -> str | None:
    """The stored form of ``raw`` as an alias of ``kind``, or None when it cannot be one."""
    if kind not in RULE_KINDS:
        return None
    value = normalise(kind, raw or '')
    if kind == 'email' and value and value.split('@', 1)[0] in ROLE_LOCAL_PARTS:
        return None
    return value


# Whose page a stored LinkedIn URL is: a person's alias is a profile, an account's a company, school or showcase page,
# each with a slug. The SQL function ``audience_linkedin_fits`` is the same rule.
_HOST = r'^([a-z0-9-]+\.)*linkedin\.com/'
_PAGES = {
    'person': re.compile(_HOST + r'in/[^/]+'),
    'company': re.compile(_HOST + r'(company|school|showcase)/[^/]+'),
}


def linkedin_fits(entity: str, value: str) -> bool:
    return _PAGES[entity].match(value) is not None


def entity_of(kind: str, value: str) -> str:
    """Whose alias a stored ``value`` is: an email names a person, a domain an account, and a LinkedIn URL either,
    by its path. Exclusion rules carry no entity, so following one reads the identities of this entity only: a
    person's opt-out never reaches the account page that shares a URL with it."""
    if kind == 'domain' or (kind == 'linkedin_url' and linkedin_fits('company', value)):
        return 'company'
    return 'person'
