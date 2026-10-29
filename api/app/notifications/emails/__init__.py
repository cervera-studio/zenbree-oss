"""The service emails, one module per interface language (#699).

A language module puts the facts of ``facts`` into words: it holds every sentence of its language, so no
caller compares a locale to choose text. ``tests/test_email_locale.py`` fails when a language of
``app.core.locale`` has no module here, or its module lacks an email."""

from __future__ import annotations

from types import ModuleType

from app.core.locale import Locale
from app.notifications import layout
from app.notifications.emails import en, es, facts, fr, it
from app.notifications.emails.facts import Digest

LANGUAGES: dict[Locale, ModuleType] = {'en': en, 'it': it, 'es': es, 'fr': fr}
# The kinds sent one by one. The others go in the digest or have no email (``service.KINDS``).
BY_DATA = ('reply_urgent', 'search_failed', 'bounces_high', 'mailbox_error', 'payment_failed')
RENDERED = (*BY_DATA, 'search_done', 'low_credits')


def render(kind: str, href: str, data: dict, locale: Locale, org_id: str = '') -> layout.Email:
    text = LANGUAGES[locale]
    if kind == 'search_done':
        return text.search_done(facts.search_done(href, data, org_id))
    if kind == 'low_credits':
        return text.low_credits(facts.low_credits(href, data))
    if kind in BY_DATA:
        return getattr(text, kind)(href, data)
    raise ValueError(kind)  # the kinds whose email is digest or never are not rendered one by one


def digest(d: Digest) -> layout.Email:
    return LANGUAGES[d.locale].digest(d)


def digest_subject(d: Digest) -> str:
    return LANGUAGES[d.locale].digest_subject(d)


def erasure_request(email: str, locale: Locale) -> tuple[str, str]:
    return LANGUAGES[locale].erasure_request(email)
