"""What a lead sent to Instantly carries (#929): the supported lead fields, and the list columns mapped to them.

Instantly's ``leads/add`` takes ``email`` and these standard fields, plus ``custom_variables`` (any name, a string,
number, boolean or null). Only the public cells of a row are read (``app.lists.rows.public_cells``): vendor
plumbing, ``*_id`` and the raw contact statuses never go out. Sending a custom variable also adds it to the
campaign's variables in Instantly (the API reference says so), which the preview tells the user.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.lists.rows import STATUS_KEYS, VENDOR_KEYS, is_internal, public_cells
from app.outreach.fields import sanitize
from app.people.person_name import first_and_last

# Instantly field -> the list column that fills it unless the user maps another.
DEFAULT_MAPPING = {
    'first_name': 'first_name',
    'last_name': 'last_name',
    'company_name': 'company_name',
    'job_title': 'title',
    'website': 'company_domain',
    'phone': 'phone',
}
FIELDS = (*DEFAULT_MAPPING, 'personalization')
MAX_CUSTOM = 20
_VARIABLE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class Mapping(BaseModel):
    """``fields``: Instantly field to list column key (absent = the default, empty = leave it out).
    ``custom``: list column keys sent as custom variables of the same name."""

    fields: dict[str, str] = Field(default_factory=dict, max_length=len(FIELDS))
    custom: list[str] = Field(default_factory=list, max_length=MAX_CUSTOM)


def sendable(key: str) -> bool:
    return not is_internal(key) and key not in VENDOR_KEYS and key not in STATUS_KEYS and key != 'email'


def check(mapping: Mapping, columns: set[str]) -> str | None:
    """A reason the mapping cannot be used, or None: an unknown Instantly field, or a column the list lacks or that
    must not leave Zenbree."""
    if any(f not in FIELDS for f in mapping.fields):
        return 'unsupported field'
    for key in [*mapping.fields.values(), *mapping.custom]:
        if key and (key not in columns or not sendable(key)):
            return f'column {key} cannot be sent'
    if any(not _VARIABLE.match(k) for k in mapping.custom):
        return 'a custom variable name must be lower snake case'
    return None


def clean_email(cells: dict) -> str:
    email = str(cells.get('email') or '').strip().lower()
    return email if len(email) <= 320 and _EMAIL.match(email) else ''


def _text(value: object) -> str | None:
    return sanitize(value) or None


def lead(cells: dict, email: str, mapping: Mapping) -> dict:
    """The ``leads/add`` object of one row."""
    cells = public_cells(cells)
    first, last = first_and_last(cells)
    out: dict = {'email': email}
    names = {'first_name': first, 'last_name': last}
    for field_name, default in DEFAULT_MAPPING.items():
        key = mapping.fields.get(field_name, default)
        if not key:
            continue
        # The name columns fall back on `full_name`, as a campaign target does.
        value = names[field_name] if key == default and field_name in names else cells.get(key)
        if text := _text(value):
            out[field_name] = text
    personalization = mapping.fields.get('personalization')
    if personalization and (text := _text(cells.get(personalization))):
        out['personalization'] = text
    custom = {k: v for k in mapping.custom if (v := _text(cells.get(k))) is not None}
    if custom:
        out['custom_variables'] = custom
    return out
