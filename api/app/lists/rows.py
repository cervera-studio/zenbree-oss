"""List / Column / Row writes, shared by the agent loop and the lists routes."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from decimal import Decimal
from typing import Literal
from xml.sax.saxutils import escape

from psycopg.types.json import Jsonb

from app.audience.link import IDENTITY_KEYS, mark_rows

# Canonical keys (docs/contracts-m1.md), in column order.
CANONICAL: dict[str, list[str]] = {
    'company': [
        'name',
        'domain',
        'website',
        'linkedin_url',
        'industry',
        'employees',
        'city',
        'country',
        'description',
        'source',
    ],
    'person': [
        'full_name',
        'first_name',
        'last_name',
        'title',
        'company_name',
        'company_domain',
        'linkedin_url',
        'email',
        'email_status',
        'city',
        'country',
        'source',
    ],
}
SCORE_KEY = 'relevance_score'
# #45: a row is delivered when it is not excluded and has every required column
# (list_columns.config.required, set by an approved plan). A phone counts as its own phone_status (#720: no
# search runs on a row that has a phone, so none would write one), and so does an email its email_status (a hand
# edit clears it). Use on ``list_rows r``.
_MISSING_SQL = (
    "(c.config->>'required')::boolean AND coalesce(r.cells->>c.key, '') = ''"
    " AND NOT (c.key IN ('phone_status', 'email_status')"
    " AND coalesce(r.cells->>replace(c.key, '_status', ''), '') <> ''){}"
)
_GAP_SQL = 'EXISTS (SELECT 1 FROM list_columns c WHERE c.list_id = r.list_id AND ' + _MISSING_SQL + ')'
DELIVERED_SQL = 'NOT r.excluded AND NOT ' + _GAP_SQL.format('')
# #416: a required column the turn left empty, so the turn refunds the row's per-row credits. An exclusion,
# or a cell the user cleared by hand, after the charge keeps the charge: the user read what it bought.
UNFINISHED_SQL = _GAP_SQL.format(" AND coalesce(r.cell_meta->c.key->>'status', '') <> 'manual'")


def missing_required(conn, list_id: str, row_ids: list[str]) -> dict[str, list[str]]:
    """The required columns each of ``row_ids`` still misses (#539), by row id; a complete row is absent."""
    found: dict[str, list[str]] = {}
    for r in conn.execute(
        'SELECT r.id::text AS id, c.key FROM list_rows r JOIN list_columns c ON c.list_id = r.list_id'
        f' WHERE r.list_id = %s AND r.id = ANY(%s::uuid[]) AND {_MISSING_SQL.format("")} ORDER BY r.seq, c.position',
        (list_id, row_ids),
    ):
        found.setdefault(r['id'], []).append(r['key'])
    return found


def norm_host(value: str | None) -> str:
    v = (value or '').strip().lower()
    v = re.sub(r'^[a-z]+://', '', v).split('/')[0].split('?')[0]
    return v.removeprefix('www.')


def norm_url(value: str | None) -> str:
    v = (value or '').strip().lower()
    return re.sub(r'^[a-z]+://(www\.)?', '', v).split('?')[0].rstrip('/')


def dedupe_key(entity: str, cells: dict) -> str | None:
    """company: domain; person: linkedin_url, else email, else name+company."""
    if entity == 'company':
        return (
            norm_host(cells.get('domain') or cells.get('website')) or (cells.get('name') or '').strip().lower() or None
        )
    if li := norm_url(cells.get('linkedin_url')):
        return li
    if email := (cells.get('email') or '').strip().lower():
        return email
    name = cells.get('full_name') or ' '.join(filter(None, [cells.get('first_name'), cells.get('last_name')]))
    company = norm_host(cells.get('company_domain')) or (cells.get('company_name') or '')
    key = f'{name}|{company}'.strip().lower()
    return key if key != '|' else None


PEOPLE_SHOWN = 3  # names a company row carries; ``people_count`` stays the full count


def add_people_counts(conn, session_id: str | None, rows: list[dict]) -> None:
    """#229: on a company list whose chat also has a person list, ``people_count`` = that chat's people
    (not excluded) at the company, and ``people`` = the first PEOPLE_SHOWN of them (#344: row id, list id, name,
    title: no other cell, so no vendor column leaves). Domain to domain when both have one, else name to name
    (case-folded). No person list in the chat: rows get neither."""
    if not session_id:
        return
    people_lists = conn.execute(
        'SELECT l.id FROM lists l'
        ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
        " WHERE s.id = %s AND l.entity = 'person' AND l.deleted_at IS NULL",
        (session_id,),
    ).fetchall()
    if not people_lists:
        return
    from app.lists.people_match import PeopleIndex  # it imports norm_host from here

    index = PeopleIndex()
    # ponytail: reads every person of the chat per rows page (lists cap at a few thousand rows).
    for p in conn.execute(
        "SELECT id, list_id, cells->>'company_domain' AS domain, cells->>'company_name' AS name,"
        " coalesce(nullif(cells->>'full_name', ''), trim(concat_ws(' ', cells->>'first_name', cells->>'last_name')))"
        " AS full_name, cells->>'title' AS title FROM list_rows"
        ' WHERE list_id = ANY(%s) AND NOT excluded ORDER BY seq',
        ([r['id'] for r in people_lists],),
    ):
        person = {'row_id': p['id'], 'list_id': p['list_id'], 'full_name': p['full_name'], 'title': p['title']}
        index.add(person, domain=p['domain'], company_name=p['name'])
    for r in rows:
        people = index.at(r['cells'])
        r['people_count'] = len(people)
        r['people'] = people[:PEOPLE_SHOWN]


def _label(key: str) -> str:
    return key.replace('_', ' ').capitalize()


def ensure_columns(conn, list_id: str, entity: str, keys: set[str]) -> None:
    """Add a static column for every key not yet in the list (canonical order first)."""
    have = {r['key'] for r in conn.execute('SELECT key FROM list_columns WHERE list_id = %s', (list_id,))}
    ordered = [k for k in CANONICAL[entity] if k in keys] + sorted(keys - set(CANONICAL[entity]))
    new = [k for k in ordered if k not in have and not is_internal(k)]
    if not new:
        return
    pos = conn.execute(
        'SELECT coalesce(max(position), -1) AS p FROM list_columns WHERE list_id = %s', (list_id,)
    ).fetchone()['p']
    for i, key in enumerate(new, start=pos + 1):
        kind = 'score' if key == SCORE_KEY else 'static'
        conn.execute(
            'INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, %s, %s, %s, %s)'
            ' ON CONFLICT DO NOTHING',
            (list_id, key, 'Fit' if key == SCORE_KEY else _label(key), kind, i),
        )


def create_list(conn, *, user_id: str, session_id: str | None, name: str, entity: Literal['company', 'person']) -> str:
    row = conn.execute(
        'INSERT INTO lists (organization_id, user_id, chat_session_id, project_id, name, entity)'
        ' VALUES ((SELECT organization_id FROM chat_sessions WHERE id = %s), %s, %s,'
        ' (SELECT project_id FROM chat_sessions WHERE id = %s), %s, %s) RETURNING id',
        (session_id, user_id, session_id, session_id, name[:120] or 'List', entity),
    ).fetchone()
    ensure_columns(conn, row['id'], entity, {SCORE_KEY})
    return str(row['id'])


# Fields where a later source beats the value already in the row. Default: the
# first value wins and later sources only fill gaps.
# ponytail: one entry; grow it per field as vendors prove better on that field.
FIELD_OWNER = {'phone': 'google_maps'}  # the Maps number is the one the business answers


def merge_cells(old: dict, new: dict, old_meta: dict | None = None) -> tuple[dict, dict]:
    """Merge ``new`` into ``old`` field by field. Returns (cells patch, cell_meta patch)
    for ``||``: the meta names the provider of every value ``new`` contributed.
    A FIELD_OWNER value replaces only one another vendor wrote; keys with
    non-vendor meta (AI cells, ``manual`` edits) are never touched. ``source``
    becomes the list of all providers, comma-separated, in arrival order."""
    src = new.get('source')
    old_meta = old_meta or {}
    cells: dict = {}
    meta: dict = {}
    for k, v in new.items():
        if k == 'source' or old.get(k) == v or (k in old_meta and 'provider' not in old_meta[k]):
            continue
        owned = src and FIELD_OWNER.get(k) == src and k in old_meta
        if k not in old or owned:
            cells[k] = v
            if src:
                meta[k] = {'status': 'filled', 'provider': src}
    seen = [p.strip() for p in str(old.get('source') or '').split(',') if p.strip()]
    if src and src not in seen:
        cells['source'] = ', '.join([*seen, src])
    return cells, meta


def add_rows(conn, list_id: str, entity: str, rows: list[dict]) -> list[str]:
    """Insert rows, merging into an existing row with the same dedupe_key
    (see ``merge_cells``). Returns ids of NEW rows only."""
    new_ids: list[str] = []
    touched: list[str] = []  # new rows and merged ones: the identity (#937) follows their email, profile and domain
    keys: set[str] = set()
    dedupe_keys = [dedupe_key(entity, {k: v for k, v in c.items() if v not in (None, '')}) for c in rows]
    # In id order, as the end-of-turn refund and the saved-search refill lock: vendor order could deadlock with them.
    conn.execute(
        'SELECT id FROM list_rows WHERE list_id = %s AND dedupe_key = ANY(%s) ORDER BY id FOR NO KEY UPDATE',
        (list_id, [k for k in dedupe_keys if k]),
    )
    for cells in rows:
        cells = {k: v for k, v in cells.items() if v not in (None, '')}
        if not cells:
            continue
        keys |= cells.keys()
        key = dedupe_key(entity, cells)
        _, meta = merge_cells({}, cells)
        r = conn.execute(
            'INSERT INTO list_rows (list_id, cells, cell_meta, dedupe_key) VALUES (%s, %s, %s, %s)'
            ' ON CONFLICT (list_id, dedupe_key) DO NOTHING RETURNING id',
            (list_id, Jsonb(cells), Jsonb(meta), key),
        ).fetchone()
        if r:
            new_ids.append(str(r['id']))
            touched.append(str(r['id']))
            continue
        old = conn.execute(
            'SELECT id, cells, cell_meta FROM list_rows WHERE list_id = %s AND dedupe_key = %s FOR NO KEY UPDATE',
            (list_id, key),
        ).fetchone()
        patch, meta = merge_cells(old['cells'], cells, old['cell_meta'])
        if not patch and not meta:
            continue
        conn.execute(
            'UPDATE list_rows SET cells = cells || %s, cell_meta = cell_meta || %s WHERE id = %s',
            (Jsonb(patch), Jsonb(meta), old['id']),
        )
        touched.append(str(old['id']))
    if keys:
        ensure_columns(conn, list_id, entity, keys)
    mark_rows(conn, touched)
    return new_ids


def apply_row_updates(conn, list_id: str, entity: str, updates: list[dict]) -> list[str]:
    """Patch cells of rows that belong to ``list_id``; others are ignored. An update with ``if_empty: <key>`` patches
    only a row whose ``<key>`` cell is still empty, checked in the same statement, so a lookup that found nothing
    never erases what a concurrent one wrote (#507)."""
    landed: list[str] = []
    keys: set[str] = set()
    for u in updates:
        cells = u.get('cells') or {}
        if not cells or not u.get('row_id'):
            continue
        row = conn.execute(
            'UPDATE list_rows SET cells = cells || %s WHERE id = %s AND list_id = %s'
            " AND (%s::text IS NULL OR coalesce(cells->>%s, '') = '') RETURNING id::text",
            (Jsonb(cells), u['row_id'], list_id, u.get('if_empty'), u.get('if_empty')),
        ).fetchone()
        if row:
            landed.append(row['id'])
            keys |= cells.keys()
    if keys:
        ensure_columns(conn, list_id, entity, keys)
    if keys & IDENTITY_KEYS:
        mark_rows(conn, landed)
    return landed


# Spreadsheet formula injection: Excel runs a cell starting with these. In a ';' locale (it, de, fr…) it also
# splits an unquoted value on ';', so each segment after a separator or line break starts a cell too (#427),
# and a quote before the formula character is read as the opening of a quoted segment, so it counts as space.
# The skipped run excludes the separators themselves: each one starts its own attempt, so a run of tabs stays linear.
_FORMULA_START = re.compile(r'(^|[;\t\r\n])((?:[^\S\t\r\n]|")*)(?=[=+\-@])')


def _csv_safe(value) -> str:
    s = _FORMULA_START.sub(r"\1\2'", '' if value is None else str(value))
    return "'" + s if s[:1] in ('\t', '\r') else s


def is_internal(key: str) -> bool:
    """Vendor ids (person_id, provider_id…) stay in cells for enrichment, never in exports."""
    return key.endswith('_id')


# Which vendor wrote a row or value: plumbing for merges and costs.
VENDOR_KEYS = frozenset({'source', 'email_source', 'phone_source'})
# The raw contact verdicts. The web reads the state from ``lookups`` (app.lists.lookups), never from these cells;
# their columns stay in the column list, because the web names them as markers of its email and phone columns.
STATUS_KEYS = frozenset({'email_status', 'phone_status'})


def public_cells(cells: dict) -> dict:
    """The cells the web may read: no vendor plumbing and no raw contact status. Build ``lookups`` from the raw
    cells first (``row_view.with_lookups``). The CSV export keeps both (``export_routes``)."""
    return {k: v for k, v in cells.items() if k not in VENDOR_KEYS and k not in STATUS_KEYS}


def public_columns(columns: list[dict]) -> list[dict]:
    """The columns a user sees, in the list API and in exports: no vendor ids or vendor names."""
    return [c for c in columns if not is_internal(c['key']) and c['key'] not in VENDOR_KEYS]


def _values(columns: list[dict], rows: list[dict]) -> list[list]:
    return [[{**r['cells'], SCORE_KEY: r.get('relevance_score')}.get(c['key']) for c in columns] for r in rows]


def to_csv(columns: list[dict], rows: list[dict]) -> str:
    """columns: [{key,label}] in order; rows: [{cells, relevance_score}]."""
    columns = public_columns(columns)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_csv_safe(c['label']) for c in columns])  # labels come from user-chosen keys too
    for values in _values(columns, rows):
        w.writerow([_csv_safe(v) for v in values])
    return buf.getvalue()


# ponytail: a one-sheet SpreadsheetML package by hand (stdlib zipfile), no styles; openpyxl if exports need formatting.
_XLSX_PARTS = {
    '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="xl/workbook.xml"'
    ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/></Relationships>',
    'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="List" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="worksheets/sheet1.xml"'
    ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>',
}
_XML_ILLEGAL = re.compile('[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')
_XLSX_MAX_CHARS = 32_767  # Excel's cell limit


def _column_letter(i: int) -> str:
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx_cell(ref: str, value) -> str:
    if value is None or value == '':
        return ''
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    # An inline string is never evaluated: no formula injection to neutralise, unlike CSV.
    text = escape(_XML_ILLEGAL.sub('', str(value))[:_XLSX_MAX_CHARS])
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def to_xlsx(columns: list[dict], rows: list[dict]) -> bytes:
    """Same columns and values as ``to_csv``, as an Excel workbook."""
    columns = public_columns(columns)
    table = [[c['label'] for c in columns], *_values(columns, rows)]
    sheet = ''.join(
        f'<row r="{n}">' + ''.join(_xlsx_cell(f'{_column_letter(j)}{n}', v) for j, v in enumerate(values)) + '</row>'
        for n, values in enumerate(table, start=1)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, xml in _XLSX_PARTS.items():
            z.writestr(name, xml)
        z.writestr(
            'xl/worksheets/sheet1.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet}'
            '</sheetData></worksheet>',
        )
    return buf.getvalue()


def fill_credits_sql(lists_where: str) -> str:
    """Net credits charged by the settled AI column fills of the lists matching ``lists_where`` (on ``fl``):
    each run's hold (``reference_id`` = ``<list_id>:<key>``, see ``ai_columns._open``) minus its refunded slack (#239)."""
    return (
        '(SELECT coalesce(sum(t.settlement_net_credits), 0)::int FROM lists fl JOIN credit_transactions t'
        " ON t.organization_id = fl.organization_id AND t.reference_type = 'ai_column'"
        # a uuid has no ':', so the prefix is exact; LIKE lets idx_credit_tx_reference serve it ('%%': always run with params)
        " AND t.reference_id IS NOT NULL AND t.reference_id LIKE fl.id::text || ':%%'"
        f" WHERE {lists_where} AND t.type = 'deduction' AND t.status = 'committed')"
    )
