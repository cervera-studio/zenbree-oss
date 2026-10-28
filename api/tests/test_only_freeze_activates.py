import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
FREEZE = APP / 'outreach' / 'freeze.py'
COMPUTED_STATUS = {
    APP / 'outreach' / 'holds.py': 'resume_paused: a paused person goes on, with the snapshot of their activation',
    APP / 'outreach' / 'coordination.py': '_stop: replied or excluded only (a Literal)',
    APP / 'outreach' / 'dispatcher.py': "_settle: target_status 'excluded' only",
}

_TABLE = r'(?:ONLY\s+)?(?:\w+\.)?outreach_campaign_targets\b'
_SET = r'\s+SET\b(?P<set>.*?)(?=\bFROM\b|\bWHERE\b|\bRETURNING\b|$)'
_UPDATE = re.compile(rf'\bUPDATE\s+{_TABLE}(?:\s+(?:AS\s+)?(?!SET\b)\w+)?{_SET}', re.IGNORECASE | re.DOTALL)
_UPSERT = re.compile(
    rf'\bINSERT\s+INTO\s+{_TABLE}.*?\bON\s+CONFLICT\b.*?\bDO\s+UPDATE{_SET}', re.IGNORECASE | re.DOTALL
)
_STATUS = re.compile(r"(?<![\w%(.])(?:\w+\.)?status\s*=\s*(?P<value>'[^']*'|[^,\s]+)", re.IGNORECASE)
_STATUS_LIST = re.compile(r'\([^()]*\bstatus\b[^()]*\)\s*=', re.IGNORECASE)
_INSERT = re.compile(rf'\bINSERT\s+INTO\s+{_TABLE}(?:\s+(?:AS\s+)?\w+)?\s*\((?P<columns>[^)]*)\)', re.IGNORECASE)
_FREEZING = 'zenbree.freezing'


def _text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(v.value if isinstance(v, ast.Constant) else '{}' for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        return None if left is None or right is None else left + right
    return None


def _sql(tree: ast.Module) -> list[tuple[str, int]]:
    found, inside_a_whole = [], set()
    for node in ast.walk(tree):
        if id(node) in inside_a_whole or not isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
            continue
        if (text := _text(node)) is not None:
            found.append((text, node.lineno))
            inside_a_whole |= {id(child) for child in ast.walk(node) if child is not node}
    return found


def _uses_freezing(tree: ast.Module) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == 'freezing')
        or (isinstance(node, ast.Attribute) and node.attr == 'freezing')
        or (isinstance(node, ast.ImportFrom) and any(a.name == 'freezing' for a in node.names))
    ]


def _writes(path: Path, source: str) -> list[str]:
    where = path.relative_to(APP.parent)
    out = []
    tree = ast.parse(source)
    for text, line in _sql(tree):
        for update in [*_UPDATE.finditer(text), *_UPSERT.finditer(text)]:
            for status in _STATUS.finditer(update['set']):
                value = status['value']
                if value == "'active'" and path != FREEZE:
                    out.append(f'{where}:{line} sets status to active')
                elif not value.startswith("'") and path not in COMPUTED_STATUS and path != FREEZE:
                    out.append(f'{where}:{line} sets status to {value}')
            if _STATUS_LIST.search(update['set']) and path != FREEZE:
                out.append(f'{where}:{line} sets status in a column list')
        out.extend(
            f'{where}:{line} inserts a status'
            for insert in _INSERT.finditer(text)
            if re.search(r'\bstatus\b', insert['columns'])
        )
        if _FREEZING in text and path != FREEZE:
            out.append(f'{where}:{line} sets {_FREEZING}')
    if path != FREEZE:
        out.extend(f'{where}:{line} uses freezing' for line in _uses_freezing(tree))
    return out


def _found(files: dict[Path, str]) -> list[str]:
    return [w for path, source in files.items() for w in _writes(path, source)]


def test_only_freeze_makes_a_target_active():
    found = _found({path: path.read_text() for path in sorted(APP.rglob('*.py'))})
    assert not found, (
        'insert targets as draft (the default) and call app.outreach.freeze.freeze to make them active;'
        f' a status from an expression needs a reason in COMPUTED_STATUS; only freeze sets {_FREEZING} or uses freezing(): {found}'
    )


def test_the_guard_sees_the_writes_it_forbids():
    other = APP / 'outreach' / 'routes.py'
    sources = {
        other: '\n'.join(
            (
                'a = "UPDATE outreach_campaign_targets SET status = \'active\' WHERE campaign_id = %s"',
                'b = "UPDATE outreach_campaign_targets t SET next_step = 1, status = %s WHERE t.id = %s"',
                'c = f"INSERT INTO outreach_campaign_targets (campaign_id, status) VALUES ({x}, %s)"',
                'd = "SELECT 1 FROM outreach_campaign_targets WHERE status = \'active\' FOR UPDATE"',
                'e = "UPDATE outreach_campaign_targets SET paused_until = NULL WHERE status = \'active\'"',
                'f = "UPDATE outreach_campaign_targets AS t SET status = \'active\' WHERE t.id = %s"',
                'g = "UPDATE ONLY public.outreach_campaign_targets SET status = \'active\'"',
                'h = "UPDATE outreach_campaign_targets SET " + "status = \'active\' WHERE id = %s"',
                'i = "UPDATE outreach_campaign_targets SET (status, next_step) = (\'active\', 1)"',
                'j = "INSERT INTO outreach_campaign_targets (id) VALUES (%s) ON CONFLICT (id) DO UPDATE SET status = \'active\'"',
                "k = \"SELECT set_config('zenbree.freezing', 'on', true)\"",
                'with freezing(conn):',
                '    conn.execute(sql)',
                'with freeze.freezing(conn):',
                '    conn.execute(sql)',
            )
        )
    }
    assert [w.split(' ', 1)[1] for w in _found(sources)] == [
        'sets status to active',
        'sets status to %s',
        'inserts a status',
        'sets status to active',
        'sets status to active',
        'sets status to active',
        'sets status in a column list',
        'sets status to active',
        'sets zenbree.freezing',
        'uses freezing',
        'uses freezing',
    ]
