"""#699: one list of interface languages on the API side, with English as the base."""

import annotationlib
import importlib
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

import app
from app.core.locale import LOCALES, stored_locale
from app.lists import csv_plan
from app.notifications import emails


def test_a_stored_language_is_kept_and_an_unknown_one_reads_as_english():
    assert [stored_locale(locale) for locale in LOCALES] == list(LOCALES)
    assert [stored_locale(value) for value in ('de', '', None, 1)] == ['en'] * 4


def _shape(value):
    """The keys of a table of text, at every depth: what two languages must have in common."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    return len(value) if isinstance(value, tuple) else None


# A mapping keyed by Locale, however it is spelled: dict, Mapping, behind Final, quoted.
_TYPED_BY_LOCALE = re.compile(
    r'^(typing\.)?(Final\[)?((typing|collections\.abc)\.)?(dict|Dict|Mapping|MutableMapping)\[Locale,'
)


def _typed_by_locale(annotation: str) -> bool:
    return bool(_TYPED_BY_LOCALE.match(annotation.replace(' ', '').replace("'", '').replace('"', '')))


def test_a_table_is_recognised_however_its_type_is_spelled():
    typed = ('dict[Locale, str]', 'Mapping[Locale, str]', "Final['dict[Locale, str]']", 'typing.Mapping[Locale, int]')
    assert all(_typed_by_locale(t) for t in typed)
    assert not any(_typed_by_locale(t) for t in ('dict[str, Locale]', 'Locale', 'list[Locale]'))


def _tables() -> dict[str, Mapping]:
    """Every module-level table typed as a mapping keyed by ``Locale`` in ``app``, found by its annotation: a new table is checked
    without being listed here."""
    found = {}
    root = Path(app.__file__).parent
    # Files, not pkgutil: several app folders are namespace packages, which pkgutil does not walk.
    for path in sorted(root.rglob('*.py')):
        parts = path.relative_to(root.parent).with_suffix('').parts
        module = importlib.import_module('.'.join(parts[:-1] if parts[-1] == '__init__' else parts))
        annotations = annotationlib.get_annotations(module, format=annotationlib.Format.STRING)
        for name, annotation in annotations.items():
            if _typed_by_locale(annotation) and isinstance(getattr(module, name, None), Mapping):
                found[f'{module.__name__}.{name}'] = getattr(module, name)
    return found


TABLES = _tables()


def test_the_tables_of_text_are_found():
    assert {'app.notifications.push._COPY', 'app.projects.routes.DEFAULT_NAMES', 'app.agent.language._NAMES'} <= set(
        TABLES
    )


@pytest.mark.parametrize('name', TABLES)
def test_every_table_of_text_has_every_language_with_the_keys_of_english(name):
    table = TABLES[name]
    assert set(table) == set(LOCALES), f'{name}: add the missing language'
    if name == 'app.notifications.emails.LANGUAGES':
        return  # modules, checked one email at a time below
    for locale in LOCALES:
        assert _shape(table[locale]) == _shape(table['en']), f'{name}[{locale!r}] differs from English'


def _texts(module) -> set[str]:
    # The emails a module writes itself, not the helpers it imports from facts.
    return {
        name
        for name, value in vars(module).items()
        if not name.startswith('_') and callable(value) and getattr(value, '__module__', None) == module.__name__
    }


def test_every_language_has_an_email_module_with_every_email_of_english():
    assert set(emails.LANGUAGES) == set(LOCALES), 'add app/notifications/emails/<language>.py and list it in LANGUAGES'
    for locale, module in emails.LANGUAGES.items():
        assert locale == module.LOCALE
        assert _texts(module) >= _texts(emails.en), f'emails/{locale}.py lacks an email that en.py has'


def test_the_discarded_rows_file_names_its_reasons_in_the_given_language():
    records, discarded = [['email'], ['a@x.test']], [(1, 'in_file')]
    assert csv_plan.discarded_csv(records, discarded, 'en') == 'email,Reason\r\na@x.test,Duplicate in the file\r\n'
    assert csv_plan.discarded_csv(records, discarded, 'it') == 'email,Motivo\r\na@x.test,Duplicata nel file\r\n'
