"""CSV file -> List rows keyed by the canonical company/person keys.

Ported from the earlier implementation ``leads/use_cases/parse_lead_csv.py`` (header aliases, delimiter
sniff, lossy decode), but unknown headers become extra columns instead of
being dropped: an uploaded list keeps the user's own data.
"""

from __future__ import annotations

import csv
import io
import itertools
import re
import unicodedata

from app.lists.lookups import import_statuses
from app.lists.rows import CANONICAL, SCORE_KEY, dedupe_key, is_internal

MAX_BYTES = 5_000_000
MAX_ROWS = 5_000
MAX_COLUMNS = 60
MAX_CELL_CHARS = 2_000

_JUNK = re.compile(r'[^a-z0-9]')

# Compact header (lowercase, ascii, no separators) -> canonical key. The canonical
# keys themselves are added below. "Company LinkedIn URL" is not an alias on a
# person list on purpose: it would overwrite the person's own profile.
_ALIASES: dict[str, dict[str, str]] = {
    'person': {
        'firstname': 'first_name',
        'givenname': 'first_name',
        'nome': 'first_name',
        'lastname': 'last_name',
        'surname': 'last_name',
        'familyname': 'last_name',
        'cognome': 'last_name',
        'name': 'full_name',
        'contactname': 'full_name',
        'nomecompleto': 'full_name',
        'company': 'company_name',
        'organization': 'company_name',
        'organizationname': 'company_name',
        'account': 'company_name',
        'accountname': 'company_name',
        'employer': 'company_name',
        'azienda': 'company_name',
        'societa': 'company_name',
        'domain': 'company_domain',
        'website': 'company_domain',
        'companywebsite': 'company_domain',
        'sitoweb': 'company_domain',
        'linkedin': 'linkedin_url',
        'personlinkedinurl': 'linkedin_url',
        'linkedinprofile': 'linkedin_url',
        'linkedinprofileurl': 'linkedin_url',
        'profileurl': 'linkedin_url',
        'jobtitle': 'title',
        'role': 'title',
        'position': 'title',
        'ruolo': 'title',
        'emailaddress': 'email',
        'workemail': 'email',
        'citta': 'city',
        'town': 'city',
        'paese': 'country',
        'nazione': 'country',
    },
    'company': {
        'company': 'name',
        'companyname': 'name',
        'organization': 'name',
        'organizationname': 'name',
        'account': 'name',
        'accountname': 'name',
        'azienda': 'name',
        'societa': 'name',
        'ragionesociale': 'name',
        'companydomain': 'domain',
        'url': 'website',
        'companywebsite': 'website',
        'sitoweb': 'website',
        'linkedin': 'linkedin_url',
        'companylinkedinurl': 'linkedin_url',
        'sector': 'industry',
        'settore': 'industry',
        'employeecount': 'employees',
        'numberofemployees': 'employees',
        'headcount': 'employees',
        'dipendenti': 'employees',
        'citta': 'city',
        'town': 'city',
        'paese': 'country',
        'nazione': 'country',
        'descrizione': 'description',
    },
}
for _entity, _keys in CANONICAL.items():
    _ALIASES[_entity].update({_JUNK.sub('', k): k for k in _keys})
    # `source` names the vendor that wrote a value: the user's own Source column is plain data.
    _ALIASES[_entity]['source'] = 'csv_source'
    _ALIASES[_entity].update({'telefono': 'phone', 'phonenumber': 'phone', 'tel': 'phone'})


# Targets a header can be mapped to by hand (import dialog): `source` stays the vendor's.
TARGETS = {entity: [k for k in keys if k != 'source'] for entity, keys in CANONICAL.items()}


class CsvError(ValueError):
    """The file cannot become a list. ``code`` says which cause (``LIST.CSV_*``): the web shows its own text."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def column_key(entity: str, header: str) -> str:
    """The cell key a header maps to; '' drops the column."""
    h = unicodedata.normalize('NFKD', header).encode('ascii', 'ignore').decode().lower()
    if alias := _ALIASES[entity].get(_JUNK.sub('', h)):
        return alias
    slug = re.sub(r'[^a-z0-9]+', '_', h).strip('_')[:40]
    if slug == SCORE_KEY:
        return ''
    # `*_id` keys are vendor ids enrichment trusts (person_id): a user's "CRM ID" must not pose as one.
    return f'{slug.removesuffix("_id")}_ref' if is_internal(slug) else slug


def headers(content: bytes) -> tuple[list[str], list[str], int]:
    """(headers, samples, rows) as the import dialog shows them before mapping: the header row, the
    first non-empty value under each header (≤80 chars) and the number of non-blank data rows."""
    records = _records(content)
    heads = records[0][:MAX_COLUMNS]
    samples = [
        next((r[i].strip()[:80] for r in records[1:] if len(r) > i and r[i].strip()), '') for i in range(len(heads))
    ]
    return heads, samples, sum(any(c.strip() for c in r) for r in records[1:])


def parse_csv(content: bytes, entity: str, mapping: list[str] | None = None) -> tuple[list[dict], int]:
    """(rows, skipped): ``skipped`` counts rows with nothing to identify them (no dedupe key).
    ``mapping`` (import dialog) names the key per column: a TARGETS key, '' to drop it, anything
    else keeps the automatic one. Rows carry no ``source``: uploaded values are the user's
    (the route marks them ``manual``)."""
    _, rows, skipped = read_csv(content, entity, mapping)
    return [cells for _, cells in rows], len(skipped)


def read_csv(
    content: bytes, entity: str, mapping: list[str] | None = None
) -> tuple[list[list[str]], list[tuple[int, dict]], list[int]]:
    """(records, rows, skipped) as ``parse_csv`` reads them, but each row and each skipped row carries its
    index in ``records`` (0 is the header), so a discarded row can be written back as the user sent it."""
    records = _records(content)
    keys = [column_key(entity, h) for h in records[0][:MAX_COLUMNS]]
    for i, choice in enumerate((mapping or [])[: len(keys)]):
        if choice == '' or choice in TARGETS[entity]:
            keys[i] = choice
    if not set(keys) & set(CANONICAL[entity]):
        raise CsvError(
            'LIST.CSV_NO_COLUMN', f'no column matches a known {entity} field (e.g. {", ".join(CANONICAL[entity][:3])})'
        )
    if len(records) - 1 > MAX_ROWS:
        raise CsvError('LIST.CSV_TOO_MANY_ROWS', f'at most {MAX_ROWS} rows per file')

    rows: list[tuple[int, dict]] = []
    skipped: list[int] = []
    for i, record in enumerate(records[1:], 1):
        cells: dict[str, str] = {}
        for key, value in zip(keys, record, strict=False):
            value = value.strip()[:MAX_CELL_CHARS]
            if key and value and not cells.get(key):  # repeated columns: first non-empty wins
                cells[key] = value
        if not cells:
            continue
        import_statuses(cells)
        if dedupe_key(entity, cells) is None:
            skipped.append(i)
            continue
        rows.append((i, cells))
    return records, rows, skipped


def _records(content: bytes) -> list[list[str]]:
    text = _decode(content).replace('\x00', '')
    try:
        reader = csv.reader(io.StringIO(text, newline=''), delimiter=_delimiter(text))
        records = list(itertools.islice(reader, MAX_ROWS + 2))  # header + rows + one to detect overflow
    except csv.Error as e:
        raise CsvError('LIST.CSV_UNREADABLE', f'unreadable CSV: {e}') from None
    if not records:
        raise CsvError('LIST.CSV_EMPTY', 'the file is empty')
    return records


def _decode(content: bytes) -> str:
    """UTF-8 (with or without BOM), else cp1252: Italian Excel's "CSV (delimitato dal separatore di elenco)"."""
    try:
        return content.decode('utf-8-sig')
    except UnicodeDecodeError:
        return content.decode('cp1252', errors='replace')


def _delimiter(text: str) -> str:
    """Comma, semicolon or tab; quoted headers like ``"Name, full";Company`` are read as quoted."""
    try:
        return csv.Sniffer().sniff(text[:4096].rsplit('\n', 1)[0], delimiters=',;\t').delimiter  # whole lines
    except csv.Error:  # one column, or too little to tell
        return max(',;\t', key=text.split('\n', 1)[0].count)
