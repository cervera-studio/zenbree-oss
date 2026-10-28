"""A field names a column key, not a column: the people of one campaign can come from several lists of its
project (bulk enrollment), and each reads the key from their own row."""

from __future__ import annotations

import re
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from app.lists.row_view import VISIBLE_CELLS_SQL
from app.lists.rows import DELIVERED_SQL, STATUS_KEYS, VENDOR_KEYS, is_internal
from app.outreach.sending import CONTACT_TOKENS

SIGNAL_FIELDS = ('title', 'city', 'country', 'url')
MAX_FIELDS = 20
MAX_VALUE = 500
_NAME_PATTERN = r'^[a-z][A-Za-z0-9]{1,39}$'
_BRACES = re.compile(r'[{}\[\]]')
_CONTACT_KEYS = frozenset(CONTACT_TOKENS) | frozenset(CONTACT_TOKENS.values())


def visible(key: str) -> bool:
    return not is_internal(key) and key not in VENDOR_KEYS and key not in STATUS_KEYS


class FieldSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['column', 'signal']
    key: str = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def _readable(self) -> FieldSource:
        if self.kind == 'signal' and self.key not in SIGNAL_FIELDS:
            raise ValueError(f'a signal field is one of {", ".join(SIGNAL_FIELDS)}')
        if self.kind == 'column' and not visible(self.key):
            raise ValueError(f'the column {self.key} is not one a user sees')
        return self


class CampaignField(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: Annotated[str, StringConstraints(pattern=_NAME_PATTERN)]
    source: FieldSource
    fallback: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None

    @model_validator(mode='after')
    def _not_a_contact_token(self) -> CampaignField:
        if self.token in CONTACT_TOKENS:
            raise ValueError(f'{{{{{self.token}}}}} is a contact token already')
        return self


_FIELDS = TypeAdapter(list[CampaignField])
# A request carries fields unchecked, so that ``parse_fields`` refuses them with CAMPAIGN.INVALID_FIELDS, not a bare 422.
UncheckedFields = Annotated[list[dict], Field(max_length=MAX_FIELDS)]


class FieldsRefused(ValueError):
    code = 'CAMPAIGN.INVALID_FIELDS'


def parse_fields(raw: object) -> list[CampaignField]:
    """The campaign's fields, or ``FieldsRefused`` with the first reason."""
    try:
        fields = _FIELDS.validate_python(raw)
    except ValidationError as e:
        first = e.errors()[0]
        raise FieldsRefused(f'{".".join(map(str, first["loc"]))}: {first["msg"]}'[:300]) from None
    if len(fields) > MAX_FIELDS:
        raise FieldsRefused(f'{len(fields)} fields, at most {MAX_FIELDS}')
    tokens = [f.token for f in fields]
    if dup := next((t for t in tokens if tokens.count(t) > 1), None):
        raise FieldsRefused(f'{{{{{dup}}}}} is used by two fields')
    return fields


class Inputs(NamedTuple):
    cells: dict
    signal: dict
    row_id: str | None = None


class Resolved(NamedTuple):
    values: dict[str, str]
    missing: list[str]
    sources: dict[str, dict]


def sanitize(value: object) -> str:
    if isinstance(value, list):
        value = ', '.join(s for v in value if (s := sanitize(v)))
    if value is None or isinstance(value, dict):
        return ''
    return ' '.join(_BRACES.sub('', str(value)).split())[:MAX_VALUE].rstrip()


def resolve(fields: list[CampaignField], target: dict, inputs: Inputs | None) -> Resolved:
    values = {token: sanitize(target.get(key)) for token, key in CONTACT_TOKENS.items()}
    missing, sources = [], {}
    for f in fields:
        source = {} if inputs is None else inputs.cells if f.source.kind == 'column' else inputs.signal
        if value := sanitize(source.get(f.source.key)):
            sources[f.token] = _provenance(f.source, inputs)
        values[f.token] = value or sanitize(f.fallback)
        if not values[f.token]:
            missing.append(f.token)
    return Resolved(values, missing, sources)


def _provenance(source: FieldSource, inputs: Inputs | None) -> dict:
    signal = inputs.signal if inputs and source.kind == 'signal' else {}
    return {
        'kind': source.kind,
        'key': source.key,
        'row_id': inputs.row_id if inputs and source.kind == 'column' else None,
        'url': signal.get('url'),
        'title': signal.get('title'),
    }


def missing_in_steps(missing: list[str], steps: list[dict]) -> list[str]:
    copy = ' '.join(f'{s["subject"]} {s["body"]}' for s in steps)
    return [t for t in missing if f'{{{{{t}}}}}' in copy]


def sample_inputs(fields: list[CampaignField]) -> Inputs:
    cells = {f.source.key: f'Sample {f.source.key}' for f in fields if f.source.kind == 'column'}
    signal = {f.source.key: f'Sample {f.source.key}' for f in fields if f.source.kind == 'signal'}
    return Inputs(cells, signal)


_INPUTS = f"""
SELECT t.id::text AS target_id, r.id::text AS row_id, {VISIBLE_CELLS_SQL} AS cells, sg.data AS signal
  FROM outreach_campaign_targets t
  LEFT JOIN list_rows r ON r.id = t.row_id AND {DELIVERED_SQL}
       AND r.list_id IN (SELECT id FROM lists WHERE project_id = %(project_id)s)
  LEFT JOIN signals sg ON sg.id = t.signal_id
 WHERE t.campaign_id = %(campaign_id)s AND t.id = ANY(%(target_ids)s::uuid[])"""


def load_inputs(conn, campaign: dict, target_ids: list[str]) -> dict[str, Inputs]:
    rows = conn.execute(
        _INPUTS, {'project_id': campaign['project_id'], 'campaign_id': campaign['id'], 'target_ids': target_ids}
    ).fetchall()
    return {
        r['target_id']: Inputs(
            {k: v for k, v in (r['cells'] or {}).items() if visible(k)}, r['signal'] or {}, r['row_id']
        )
        for r in rows
    }


class CatalogEntry(BaseModel):
    token: str
    source: FieldSource
    label: str | None
    ai: bool
    filled: int


class FieldCatalog(BaseModel):
    people: int
    contact_tokens: list[str]
    columns: list[CatalogEntry]
    signal: list[CatalogEntry]


_COLUMNS = """
SELECT c.key, c.label, c.kind
  FROM list_columns c JOIN lists l ON l.id = c.list_id
 WHERE l.project_id = %(project_id)s
   AND (c.list_id = %(list_id)s OR c.list_id IN (
        SELECT r.list_id FROM outreach_campaign_targets t JOIN list_rows r ON r.id = t.row_id
         WHERE t.campaign_id = %(campaign_id)s))
 ORDER BY c.list_id = %(list_id)s DESC NULLS LAST, c.position"""

_FILLED = f"""
SELECT k.key, count(*) AS n
  FROM outreach_campaign_targets t
  JOIN list_rows r ON r.id = t.row_id AND {DELIVERED_SQL}
       AND r.list_id IN (SELECT id FROM lists WHERE project_id = %(project_id)s)
 CROSS JOIN LATERAL jsonb_each({VISIBLE_CELLS_SQL}) k
 WHERE t.campaign_id = %(campaign_id)s AND jsonb_typeof(k.value) <> 'object'
   AND btrim(k.value #>> '{{}}') NOT IN ('', '[]', 'null')
 GROUP BY k.key"""

_SIGNAL_FILLED = (
    'SELECT count(*) AS people, count(sg.id) AS signals, '
    + ', '.join(f"count(*) FILTER (WHERE btrim(coalesce(sg.data ->> '{k}', '')) <> '') AS {k}" for k in SIGNAL_FIELDS)
    + ' FROM outreach_campaign_targets t LEFT JOIN signals sg ON sg.id = t.signal_id WHERE t.campaign_id = %s'
)


def catalog(conn, campaign: dict) -> dict:
    params = {'project_id': campaign['project_id'], 'campaign_id': campaign['id'], 'list_id': campaign['list_id']}
    filled = {r['key']: r['n'] for r in conn.execute(_FILLED, params)}
    counts = conn.execute(_SIGNAL_FILLED, (campaign['id'],)).fetchone()
    taken: set[str] = set()
    columns, seen = [], set()
    for c in conn.execute(_COLUMNS, params):
        if c['key'] in seen or c['key'] in _CONTACT_KEYS or not visible(c['key']):
            continue
        seen.add(c['key'])
        columns.append(
            {
                'token': _token(c['key'], taken),
                'source': {'kind': 'column', 'key': c['key']},
                'label': c['label'],
                'ai': c['kind'] == 'ai',
                'filled': filled.get(c['key'], 0),
            }
        )
    signal = [
        {
            'token': _token(f'signal_{k}', taken),
            'source': {'kind': 'signal', 'key': k},
            'label': None,
            'ai': False,
            'filled': counts[k],
        }
        for k in SIGNAL_FIELDS
        if counts['signals']
    ]
    return {
        'people': counts['people'],
        'contact_tokens': ['firstName', 'lastName', 'companyName', 'role'],
        'columns': columns,
        'signal': signal,
    }


def _token(key: str, taken: set[str]) -> str:
    words = re.findall(r'[A-Za-z0-9]+', key)
    base = ''.join(w[:1].upper() + w[1:] for w in words)
    base = (base[:1].lower() + base[1:]) if base[:1].isalpha() else f'field{base}'
    base = base[:36]
    token, n = base, 2
    while token in taken or token in CONTACT_TOKENS or len(token) < 2:
        token, n = f'{base}{n}', n + 1
    taken.add(token)
    return token
