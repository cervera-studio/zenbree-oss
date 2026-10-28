"""find_rows: the rows of the current List that carry a person's name, so a request by name reaches those rows (#560).

The enrich tools take row ids only, and a person nobody named is never searched (#140, #550): a name that matches
no row, or several, comes back as such for the model to tell the user, never as a guess.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.agent.tools.handlers.enrich_email import MAX_PEOPLE, load_rows
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.core.db import get_conn
from app.people.person_name import first_and_last, masked_parts

MAX_SHOWN = 5


def plain(text: str) -> str:
    """Case and accents dropped: «Espòsito» is «esposito»."""
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).casefold()


def ordered(name: str) -> list[str]:
    return re.findall(r'\w+', plain(name))


def words(name: str) -> frozenset[str]:
    return frozenset(ordered(name))


@dataclass(frozen=True)
class Row:
    row_id: str
    label: dict  # {name, title?, company_name?}: what the model shows the user
    contacts: dict  # {has_email, has_phone}: never the contact itself, the result goes to the LLM vendor
    first: frozenset[str]
    last: frozenset[str]  # empty while Apollo masks the surname: then only ``mask`` is known of it
    mask: tuple[str, str] | None  # its visible start and end, without spaces

    def fits(self, name: str) -> bool:
        """``name`` is this row's first name plus what the masked surname could be: «Gino Marchi», «Gino M.»
        or «Gino Ma» for «Gino Ma···i». A surname of several words is masked as one («De Luca» is
        «De····a»), so the other words are read joined, in the order given."""
        if not self.mask or not self.first or not self.first < words(name):
            return False
        surname = ''.join(w for w in ordered(name) if w not in self.first)
        start, end = self.mask
        return start.startswith(surname) or (
            surname.startswith(start) and surname.endswith(end) and len(surname) > len(start) + len(end)
        )


def matching(query: frozenset[str], rows: list[Row]) -> list[Row]:
    """The rows whose name has every word of ``query``, in list order; rows named exactly ``query`` win over
    longer names that contain it («Maria Rossi» is not also «Maria Pia Rossi»)."""
    if not query:
        return []
    hits = [r for r in rows if query <= r.first | r.last]
    return [r for r in hits if r.first | r.last == query] or hits


def _row(r: dict) -> Row:
    first, last = first_and_last(r)
    mask = masked_parts(last)
    return Row(
        row_id=str(r['id']),
        label={'name': ' '.join(filter(None, (first, last))), **{k: r[k] for k in ('title', 'company_name') if r[k]}},
        contacts={'has_email': r['has_email'], 'has_phone': r['has_phone']},
        first=words(first),
        last=frozenset() if mask else words(last),
        mask=(''.join(ordered(mask[0])), ''.join(ordered(mask[1]))) if mask else None,
    )


def _named_rows(list_id: str) -> list[Row]:
    # ponytail: scans every row of the list, like next_unenriched; fine at upload size (5000 rows)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, cells->>'first_name' AS first_name, cells->>'last_name' AS last_name,"
            " cells->>'full_name' AS full_name, cells->>'title' AS title, cells->>'company_name' AS company_name,"
            " coalesce(cells->>'email', '') <> '' AS has_email, coalesce(cells->>'phone', '') <> '' AS has_phone"
            ' FROM list_rows WHERE list_id = %s ORDER BY seq',
            (list_id,),
        ).fetchall()
    return [_row(r) for r in rows]


def _not_counted(row: Row, reason: Literal['masked', 'excluded']) -> dict:
    # An excluded row gets no id: no enrich call may take it.
    return {**({'row_id': row.row_id} if reason == 'masked' else {}), **row.label, 'reason': reason}


async def find_rows(args: dict, ctx: ToolContext) -> ToolResult:
    raw = args.get('names')
    names = [n for n in raw if isinstance(n, str)][:MAX_PEOPLE] if isinstance(raw, list) else []
    rows = await asyncio.to_thread(_named_rows, ctx.list_id) if ctx.list_id else []
    found: dict[str, tuple[list[Row], list[Row]]] = {}
    for name in names:
        hits = matching(words(name), rows)
        found[name] = hits, [r for r in rows if r.fits(name) and r not in hits]
    wanted = list(dict.fromkeys(r.row_id for hits, fits in found.values() for r in [*hits, *fits]))
    # Excluded rows drop out of the count: they never match, nor make a name ambiguous.
    loaded = await asyncio.to_thread(load_rows, ctx.list_id, wanted) if ctx.list_id and wanted else []
    searchable = {p['row_id'] for p in loaded}
    matches = []
    for name, (hits, fits) in found.items():
        counted = [r for r in hits if r.row_id in searchable]
        not_counted = [
            *(_not_counted(r, 'masked') for r in fits if r.row_id in searchable),
            *(_not_counted(r, 'excluded') for r in [*hits, *fits] if r.row_id not in searchable),
        ]
        matches.append(
            {
                'name': name,
                'matches': len(counted),
                'rows': [{'row_id': r.row_id, **r.label, **r.contacts} for r in counted[:MAX_SHOWN]],
                'not_counted': not_counted[:MAX_SHOWN],
            }
        )
    no_list = {} if ctx.list_id else {'no_list': True}
    return ToolResult(ok=True, data={**no_list, 'matches': matches})


register(
    ToolDef(
        name='find_rows',
        description=(
            'Find people of the current List by name, to get the row_ids that enrich_email and enrich_phone take '
            'when the user names people. Free. For each name, matches counts the rows whose name has all its words '
            '(case, accents and word order ignored; a row named exactly that wins over longer names) and rows shows '
            f'up to {MAX_SHOWN} of them. Use a row_id only when its name has exactly 1 match; with more than 1, show '
            'who they are and ask which one. not_counted lists rows the user sees in the list that may be that '
            'person: reason masked = the surname is hidden until its email is found, and its visible letters fit: '
            'ask whether it is that person, and use its row_id only once the user says so; once it is enriched, '
            'name the person as enrich_email data.revealed does, never with the surname the user guessed (after '
            'enrich_phone, which reveals no name, use the masked name); reason excluded = the row is excluded, so '
            'it is never enriched: say so; only the user can include it again, from the list. '
            'Say the person is not in the list only when matches is 0 and not_counted is empty. no_list: there is '
            'no list yet. Never enrich a guess.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'names': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': MAX_PEOPLE},
            },
            'required': ['names'],
        },
        handler=find_rows,
    )
)
