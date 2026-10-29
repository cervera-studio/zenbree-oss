"""The interface languages (#699). This is the one list on the API side: every table of text is keyed by
``Locale`` and ``tests/test_locale.py`` fails when a table lacks one of them.

The web declares the same list in ``web/i18n/config.ts`` and the database in the check on ``"user".locale``.

Adding a language: ``docs/recipes.md``, "Add an interface language"."""

from __future__ import annotations

from typing import Literal, get_args

Locale = Literal['en', 'it', 'es', 'fr']
LOCALES: tuple[Locale, ...] = get_args(Locale)
# English is the base: what a user with no known language reads.
DEFAULT_LOCALE: Locale = 'en'


def stored_locale(value: object) -> Locale:
    """A language read from a row or a request: an unknown one (an old row, a language since removed) is English."""
    return value if value in LOCALES else DEFAULT_LOCALE
