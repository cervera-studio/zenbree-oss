"""#660: a statement that locks list rows never reads their charge state. A lock wait re-reads the row it waited on,
but the row_charges sub-select keeps the statement's old snapshot, so a turn that just committed stays invisible."""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
CHARGE_READS = re.compile(r'\b(PENDING_KEYS_SQL|ROW_COLS|VISIBLE_CELLS_SQL|CHARGES_SQL|OPEN_EMAIL_SQL|refunded_sql)\b')
LOCKS = re.compile(r'\b(FOR (NO KEY )?UPDATE|FOR (KEY )?SHARE|RETURNING|UPDATE|DELETE)\b')


def _offences() -> list[str]:
    found = []
    for path in APP.rglob('*.py'):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'execute':
                sql = ast.get_source_segment(source, node.args[0]) if node.args else None
                if sql and CHARGE_READS.search(sql) and LOCKS.search(sql):
                    found.append(f'{path.relative_to(APP.parent)}:{node.lineno}')
    return found


def test_no_statement_that_locks_a_row_reads_its_charges():
    assert _offences() == [], (
        'lock the rows in one statement (FOR UPDATE, or UPDATE ... RETURNING id), then read the charge state in a'
        ' later statement of the same transaction, as lists/row_view.py read_row does'
    )
