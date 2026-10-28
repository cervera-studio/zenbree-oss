"""What a CSV import would do with its rows: the review step counts it, the worker runs it."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from app.core.locale import Locale
from app.exclusions.rules import is_excluded, load_rules
from app.lists.rows import _csv_safe, dedupe_key

# The file is the user's to read, so it is written in the language saved on the user (#699).
REASONS: dict[Locale, dict[str, str]] = {
    'en': {
        'header': 'Reason',
        'excluded': 'In Exclusions',
        'in_lists': 'Already in your lists',
        'in_file': 'Duplicate in the file',
        'no_identity': 'No name, domain or LinkedIn',
    },
    'it': {
        'header': 'Motivo',
        'excluded': 'In Esclusioni',
        'in_lists': 'Già nelle tue liste',
        'in_file': 'Duplicata nel file',
        'no_identity': 'Senza nome, dominio o LinkedIn',
    },
    'es': {
        'header': 'Motivo',
        'excluded': 'En Exclusiones',
        'in_lists': 'Ya en tus listas',
        'in_file': 'Duplicada en el archivo',
        'no_identity': 'Sin nombre, dominio ni LinkedIn',
    },
    'fr': {
        'header': 'Motif',
        'excluded': 'Dans Exclusions',
        'in_lists': 'Déjà dans vos listes',
        'in_file': 'Doublon dans le fichier',
        'no_identity': 'Sans nom, domaine ni LinkedIn',
    },
}


@dataclass
class Plan:
    keep: list[dict] = field(default_factory=list)
    discarded: list[tuple[int, str]] = field(default_factory=list)  # (record index, REASONS key)

    def count(self, reason: str) -> int:
        return sum(r == reason for _, r in self.discarded)


def existing_keys(conn, project_id: str, entity: str, keys: list[str]) -> set[str]:
    """The ``keys`` already in a live list of the project (a project is a client): the same ``dedupe_key`` rule rows merge on."""
    found = conn.execute(
        'SELECT DISTINCT r.dedupe_key FROM list_rows r JOIN lists l ON l.id = r.list_id'
        ' WHERE l.project_id = %s AND l.entity = %s AND l.deleted_at IS NULL AND r.dedupe_key = ANY(%s)',
        (project_id, entity, keys),
    )
    return {r['dedupe_key'] for r in found}


def make_plan(conn, project_id: str, entity: str, rows: list[tuple[int, dict]], skipped: list[int]) -> Plan:
    """Exclusions first, then rows already in the project's lists, then repeats inside the file (merged into
    the first one, whose values win, as ``add_rows`` merges)."""
    rules = load_rules(conn, project_id)
    plan = Plan(discarded=[(i, 'no_identity') for i in skipped])
    keyed = []
    for i, cells in rows:
        if is_excluded(rules, cells):
            plan.discarded.append((i, 'excluded'))
        else:
            keyed.append((i, cells, dedupe_key(entity, cells)))
    known = existing_keys(conn, project_id, entity, [k for _, _, k in keyed if k])
    first: dict[str | None, dict] = {}
    for i, cells, key in keyed:
        if key in known:
            plan.discarded.append((i, 'in_lists'))
        elif key in first:
            plan.discarded.append((i, 'in_file'))
            for k, v in cells.items():
                first[key].setdefault(k, v)
        else:
            first[key] = dict(cells)
            plan.keep.append(first[key])
    return plan


def discarded_csv(records: list[list[str]], discarded: list[tuple[int, str]], locale: Locale) -> str:
    """The user's own rows that were not imported, in file order, with the reason in a last column."""
    buf = io.StringIO()
    w = csv.writer(buf)
    reasons = REASONS[locale]
    w.writerow([*(_csv_safe(h) for h in records[0]), reasons['header']])
    for i, reason in sorted(discarded):
        w.writerow([*(_csv_safe(v) for v in records[i]), reasons[reason]])
    return buf.getvalue()
