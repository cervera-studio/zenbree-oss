"""The AST scans behind tests/test_scope_entry_points.py. Each resolves the names of one file before it looks for a
use, so an alias (``import ... as``), a local variable or a ``getattr`` with a constant counts as the name itself.

The limit no AST walk closes: a helper that receives the dependency or the reason as a parameter. The override scan
refuses a key it cannot resolve to a name of the file, and the reason scan refuses a reason that is not written out,
so that helper fails the scan instead of passing it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

OVERRIDES = 'dependency_overrides'


def _own_exprs(stmt: ast.stmt) -> Iterator[ast.AST]:
    """The expressions of ``stmt`` itself, not of the statements nested in its body."""
    for _, value in ast.iter_fields(stmt):
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, ast.AST) and not isinstance(item, ast.stmt | ast.excepthandler):
                yield item


def _assigned(tree: ast.AST) -> Iterator[tuple[ast.expr, ast.AST]]:
    """(target, value) of every binding of a plain name: ``=``, ``:=``, an annotated ``=`` and a ``for`` loop."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                yield target, node.value
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
            yield node.target, node.value
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            yield node.target, node.iter


def _target_names(target: ast.expr) -> Iterator[str]:
    """The names a target binds: not the ones it reads, as ``app`` and ``dep`` in ``app.overrides[dep] = ...``."""
    for node in ast.walk(target):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.id


@dataclass
class Names:
    """What the names of one file stand for: ``canon`` maps a local name to the protected name it holds."""

    protected: frozenset[str]
    canon: dict[str, str] = field(default_factory=dict)
    holders: set[str] = field(default_factory=set)  # local names bound to an app's dependency_overrides
    bound: set[str] = field(default_factory=set)  # every name the file binds

    @classmethod
    def of(cls, tree: ast.AST, protected: frozenset[str]) -> Names:
        names = cls(protected)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.bound |= {alias.asname or alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.bound.add(alias.asname or alias.name)
                    if alias.name in protected:
                        names.canon[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.bound.add(node.name)
        for _ in range(3):  # an alias of an alias: a few passes reach a fixed point in any real file
            for target, value in _assigned(tree):
                held = names.mentions(value)
                touches = names.touches_overrides(value)
                for name in _target_names(target):
                    names.bound.add(name)
                    if held:
                        names.canon[name] = min(held)
                    if touches:
                        names.holders.add(name)
        return names

    def mentions(self, node: ast.AST) -> set[str]:
        """The protected names ``node`` uses: as a name, an attribute, or a constant string (``getattr``)."""
        found = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in self.canon:
                found.add(self.canon[sub.id])
            elif isinstance(sub, ast.Name) and sub.id in self.protected:
                found.add(sub.id)
            elif isinstance(sub, ast.Attribute) and sub.attr in self.protected:
                found.add(sub.attr)
            elif isinstance(sub, ast.Constant) and sub.value in self.protected:
                found.add(sub.value)
        return found

    def touches_overrides(self, node: ast.AST) -> bool:
        return any(
            (isinstance(sub, ast.Attribute) and sub.attr == OVERRIDES)
            or (isinstance(sub, ast.Name) and sub.id in self.holders)
            for sub in ast.walk(node)
        )

    def is_overrides(self, node: ast.AST) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == OVERRIDES) or (
            isinstance(node, ast.Name) and node.id in self.holders
        )


def _keys(stmt: ast.stmt, names: Names) -> Iterator[ast.expr]:
    """The dependencies ``stmt`` writes into ``dependency_overrides``: a subscript, the first argument or the dict
    keys of one of its methods (``update``, ``__setitem__``, ``pop``) or of a call it is handed to
    (``monkeypatch.setitem``, ``patch.dict``), and the right side of ``|=`` or ``=``."""
    for expr in _own_exprs(stmt):
        for node in ast.walk(expr):
            if isinstance(node, ast.Subscript) and names.is_overrides(node.value):
                yield node.slice
                continue
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and names.is_overrides(node.func.value):
                args = list(node.args)
            elif node.args and names.is_overrides(node.args[0]):
                args = list(node.args[1:])
            else:
                continue
            for i, arg in enumerate([*args, *(k.value for k in node.keywords)]):
                if isinstance(arg, ast.Dict):
                    yield from (k for k in arg.keys if k is not None)
                elif i == 0:
                    yield arg
    if not isinstance(stmt, ast.Assign | ast.AugAssign):
        return
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    if any(names.is_overrides(t) for t in targets):
        value = stmt.value
        yield from (k for k in value.keys if k is not None) if isinstance(value, ast.Dict) else [value]


def overrides(tree: ast.AST, protected: frozenset[str]) -> list[tuple[int, str]]:
    """(line, why) for each write of a protected dependency into ``dependency_overrides``, and each key that does
    not resolve to a name of the file (a parameter, a computed ``getattr``)."""
    names = Names.of(tree, protected)
    found = []
    for stmt in ast.walk(tree):
        if not isinstance(stmt, ast.stmt):
            continue
        for key in _keys(stmt, names):
            if held := names.mentions(key):
                found.append((stmt.lineno, f'overrides {", ".join(sorted(held))}'))
            elif not _resolves(key, names):
                found.append((stmt.lineno, f'overrides {ast.unparse(key)}, which the scan cannot resolve'))
    return sorted(set(found))


def _resolves(key: ast.expr, names: Names) -> bool:
    if isinstance(key, ast.Name):
        return key.id in names.bound
    if isinstance(key, ast.Attribute):
        return _resolves(key.value, names) if isinstance(key.value, ast.Name | ast.Attribute) else False
    return False


# ── enter_member: the membership read a request makes ──

ENTER = 'enter_member'
_REDEFINE = re.compile(r'\b(CREATE|ALTER|DROP)\b[^;]*\bFUNCTION\s+zb\s*\.\s*enter_member\b', re.IGNORECASE | re.DOTALL)
_PATCHERS = {'setattr', 'patch', 'object', 'setitem'}


def _guarded_fakes(tree: ast.AST) -> set[str]:
    """Functions of the file that call ``no_membership_read``: a fake that refuses to run with a test database."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(
            isinstance(c, ast.Call) and getattr(c.func, 'id', getattr(c.func, 'attr', None)) == 'no_membership_read'
            for c in ast.walk(node)
        )
    }


def _names_enter(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.split('.')[-1] == ENTER) or (
        isinstance(node, ast.Attribute) and node.attr == ENTER
    )


def membership_fakes(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, why) for each replacement of ``enter_member`` that is not a guarded fake, and each SQL string that
    redefines ``zb.enter_member``."""
    guarded = _guarded_fakes(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', None)) in _PATCHERS:
            args = [*node.args, *(k.value for k in node.keywords)]
            if any(_names_enter(a) for a in args):
                fake = args[-1] if len(args) > 1 else None
                if not (isinstance(fake, ast.Name) and fake.id in guarded):
                    found.append((node.lineno, 'replaces enter_member with no no_membership_read guard'))
        elif isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and t.attr == ENTER for t in targets):
                found.append((node.lineno, 'assigns enter_member'))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _REDEFINE.search(node.value):
            found.append((node.lineno, 'redefines zb.enter_member'))
    return sorted(set(found))


# ── SystemReason: who may cross organizations ──

ENTRIES = frozenset({'system', 'public', 'enter_system_for_request', 'Public', 'system_task'})
SCOPES = frozenset({'System', 'Member', 'ForOrg'})
PRIVATE = frozenset({'_entered', '_set', '_SCOPE', '_OPEN'})
REASON = 'SystemReason'
#: (module, function) that pass on a reason another call wrote out: ``public(SystemReason.X)`` and
#: ``system_task(..., reason=SystemReason.X)`` are scanned instead.
FORWARDERS = frozenset(
    {('auth/middleware.py', 'public'), ('auth/middleware.py', '__call__'), ('core/tasks.py', '_published')}
)


@dataclass(frozen=True)
class ReasonUse:
    module: str
    line: int
    reason: str


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return None


def reasons(tree: ast.AST, module: str) -> tuple[list[ReasonUse], list[str]]:
    """The reasons ``module`` names, and what the scan refuses in it."""
    protected = ENTRIES | SCOPES | {REASON}
    names = Names.of(tree, protected)
    parents = _parents(tree)
    scope_modules = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == 'app.core'
        for alias in node.names
        if alias.name == 'scope'
    } | {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == 'app.core.scope' and alias.asname
    }

    def resolved(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return names.canon.get(node.id, node.id if node.id in protected else None)
        if isinstance(node, ast.Attribute) and node.attr in protected:
            return node.attr
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'getattr' and len(node.args) > 1:
            value = node.args[1]
            return value.value if isinstance(value, ast.Constant) and value.value in protected else None
        return None

    uses, refused = [], []
    for node in ast.walk(tree):
        where = f'app/{module}:{getattr(node, "lineno", 0)}'
        if isinstance(node, ast.Attribute) and resolved(node.value) == REASON:
            uses.append(ReasonUse(module, node.lineno, node.attr))
        elif isinstance(node, ast.Subscript) and resolved(node.value) == REASON:
            refused.append(f'{where} SystemReason[...]: write the member out')
        elif isinstance(node, ast.Call) and resolved(node.func) == REASON:
            refused.append(f'{where} SystemReason(...): write the member out')
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, 'id', None) == 'getattr'
            and node.args
            and resolved(node.args[0]) == REASON
        ):
            refused.append(f'{where} getattr(SystemReason, ...): write the member out')
        if not isinstance(node, ast.Call):
            continue
        called = resolved(node.func)
        if called in SCOPES:
            refused.append(f'{where} {called}(...) outside app/core/scope.py: use system, acting_for or acting_as')
        elif called in ENTRIES and (module, _function(node, parents)) not in FORWARDERS:
            named = next((k.value for k in node.keywords if k.arg == 'reason'), None)
            reason = named or (node.args[0] if node.args else None)
            literal = isinstance(reason, ast.Attribute) and resolved(reason.value) == REASON
            unscoped = called in {'public', 'Public'} and isinstance(reason, ast.Constant) and reason.value is None
            if not (literal or unscoped):
                refused.append(f'{where} {called}({ast.unparse(reason) if reason else ""}): write SystemReason.X out')
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'app.core.scope':
            refused += [f'app/{module}:{node.lineno} imports {a.name}' for a in node.names if a.name in PRIVATE]
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in PRIVATE
            and getattr(node.value, 'id', None) in scope_modules
        ):
            refused.append(f'app/{module}:{node.lineno} uses scope.{node.attr}')
    return uses, refused


#: Text no app SQL needs, wherever it stands: each changes who a statement runs as or what it may see, or clears
#: the session. Each token names a statement, not a word, so prompt text passes: ``set local_query``, "Set local
#: filters first", "Discard companies that ...", "Session authorization failed" and the ``discarded`` column.
#: ``RESET ALL`` and ``DISCARD ...`` take no argument, so they count only as a whole statement, with a start and an
#: end: "Reset all filters." starts its text as the statement does, and "If none match, discard all" ends it. A hole
#: ends the statement only right after the token (``RESET ALL{x}``): after a space it is prose (``Discard all {entity}
#: that ...``).
_STARTS = r"(?:^|;|\{\}|\bbegin\b|\bthen\b|\belse\b|\bloop\b|\bexecute (?:format ?\( ?)?') ?"
_ENDS_HERE = r"(?=\{\}| ?(?:;|'|$))"
ANYWHERE = re.compile(
    r'\bset_config\b|\bzb\.enter_|\brow_security\b|\breset role\b|\bset (session )?zb\.|\breset zb\.'
    r'|\bsession_authorization\b|\b(re)?set (session |local )?session authorization\b'
    r'|\bset local ((role|session|row_security|search_path)\b|zb\.|\{\}|%s|%\(\w+\)s)'
    rf'|{_STARTS}(discard (all|plans|sequences|temp|temporary)|reset all){_ENDS_HERE}',
    re.IGNORECASE,
)
#: ``SET ROLE`` and ``SET {hole}`` have look-alikes: the SET clause of an UPDATE (``UPDATE t SET role = %s``,
#: ``ON CONFLICT ... DO UPDATE SET {sets}``). Either passes only as an assignment in a statement an UPDATE opened.
_SET = re.compile(r'\bset (?P<session>session )?(?P<what>role\b|\{\}|%s|%\(\w+\)s) ?(?P<then>=|to\b)?', re.IGNORECASE)
_UPDATE = re.compile(r'\bupdate\b', re.IGNORECASE)
#: A statement that ends the transaction the scoped checkout's SET LOCAL ROLE opened: the next statement would run as
#: the login role with no scope. Read only in what ``execute`` runs, as Lua and prose end blocks with ``end`` too. A
#: PL/pgSQL statement also follows BEGIN, THEN, ELSE or LOOP.
_ENDS = re.compile(
    r'(^|;|\bbegin\b|\bthen\b|\belse\b|\bloop\b) ?(commit|rollback|end|abort)(?P<work> (work|transaction))?'
    r'( and (no )?chain)? ?(;|$)',
    re.IGNORECASE,
)
_DOLLAR_QUOTE = re.compile(r'\$\w*\$')  # $$ or a tagged $body$: the quote of a PL/pgSQL body
_EXECUTES = frozenset({'execute', 'executemany', 'stream'})  # psycopg's Cursor.stream runs its query too
_COMMENT = re.compile(r'--[^\n]*|/\*.*?\*/', re.DOTALL)
ENDS_TRANSACTION = frozenset({'commit', 'rollback'})
_HOLE = '{}'


class _Text:
    """The SQL a string expression of one file spells: constants, f-strings, ``+`` and ``%``, ``str.format``,
    ``join``, and psycopg's ``sql.SQL`` / ``Identifier`` / ``Literal`` / ``Composed``. A value it cannot read is a
    hole, so the text around it still matches. A name bound once to a string reads as that string."""

    def __init__(self, tree: ast.AST) -> None:
        self._bound: dict[str, list[ast.AST]] = {}
        for target, value in _assigned(tree):
            if isinstance(target, ast.Name):
                self._bound.setdefault(target.id, []).append(value)

    def of(self, node: ast.AST, seen: frozenset[str] = frozenset()) -> str | None:
        match node:
            case ast.Constant(value=str() as value):
                return value
            case ast.JoinedStr(values=values):
                return ''.join(self._part(v, seen) for v in values)
            case ast.FormattedValue(value=value):
                return self._part(value, seen)
            case ast.BinOp(left=left, op=ast.Add(), right=right):
                texts = (self.of(left, seen), self.of(right, seen))
                return None if texts == (None, None) else ''.join(t or _HOLE for t in texts)
            case ast.BinOp(left=left, op=ast.Mod(), right=right):
                return self._percent(left, right, seen)
            case ast.Name(id=name) if name not in seen and len(self._bound.get(name, ())) == 1:
                return self.of(self._bound[name][0], seen | {name})
            case ast.Call(func=func, args=args, keywords=keywords):
                return self._call(func, args, keywords, seen)
        return None

    def _percent(self, left: ast.expr, right: ast.expr, seen: frozenset[str]) -> str | None:
        """``'SET LOCAL %s' % ('zb.org_id',)``: the right operand fills the holes, as ``format`` does."""
        if (base := self.of(left, seen)) is None:
            return None
        filled = iter([self._part(i, seen) for i in (right.elts if isinstance(right, ast.Tuple) else [right])])
        return re.sub(r'%%|%\(\w+\)[sdr]|%[sdr]', lambda m: m[0] if m[0] == '%%' else next(filled, _HOLE), base)

    def _part(self, node: ast.AST, seen: frozenset[str]) -> str:
        text = self.of(node, seen)
        return _HOLE if text is None else text

    def _call(
        self, func: ast.expr, args: list[ast.expr], keywords: list[ast.keyword], seen: frozenset[str]
    ) -> str | None:
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        items = args[0].elts if args and isinstance(args[0], ast.List | ast.Tuple) else []
        if name == 'SQL' and args:
            return self.of(args[0], seen)
        if name == 'Identifier':
            return '.'.join(self._part(a, seen) for a in args)
        if name == 'Literal' and args:
            return f"'{self._part(args[0], seen)}'"
        if name == 'Composed':
            return ''.join(self._part(i, seen) for i in items)
        if isinstance(func, ast.Attribute) and name == 'join' and (glue := self.of(func.value, seen)) is not None:
            return glue.join(self._part(i, seen) for i in items)
        if isinstance(func, ast.Attribute) and name == 'format' and (base := self.of(func.value, seen)) is not None:
            filled = iter([self._part(a, seen) for a in args])
            named = {k.arg: self._part(k.value, seen) for k in keywords if k.arg}
            return re.sub(r'\{(\w*)[^{}]*\}', lambda m: named.get(m[1]) or next(filled, _HOLE), base)
        return None


def _docstrings(tree: ast.AST) -> set[ast.AST]:
    """Bare string statements: a docstring is prose, and a string no code uses never reaches the database."""
    return {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant | ast.JoinedStr)
    }


def _flat(sql: str) -> str:
    """One space for each run of whitespace or comment, and no double quotes: ``SET/**/"ROLE"`` reads ``SET ROLE``."""
    return ' '.join(_COMMENT.sub(' ', sql).replace('"', '').split())


def _sets_a_column(flat: str, found: re.Match[str]) -> bool:
    """``SET role =`` or ``SET {hole} =`` after an UPDATE in the same statement: ``UPDATE t SET a = 1; SET role = x``
    is a second statement. A hole with no ``=`` or ``TO`` after it is a SET clause held apart
    (``clause = f'SET {assignments}'``), but no UPDATE clause starts ``SET SESSION``."""
    if found['session']:
        return False
    if found['what'].lower() != 'role' and not found['then']:
        return True
    statement = flat[flat.rfind(';', 0, found.start()) + 1 : found.start()]
    return found['then'] == '=' and _UPDATE.search(statement) is not None


def _ended(flat: str) -> re.Match[str] | None:
    """In a dollar-quoted body a bare ``END;`` closes the PL/pgSQL block, so there only COMMIT, ROLLBACK, ABORT and
    ``END WORK`` or ``END TRANSACTION`` count."""
    body = _DOLLAR_QUOTE.search(flat) is not None
    for found in _ENDS.finditer(flat):
        if not (body and found[2].lower() == 'end' and not found['work']):
            return found
    return None


def scope_switches(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, SQL) for each statement that switches the role or a ``zb.*`` setting, or ends the transaction."""
    return sorted(set(role_switches(tree)) | set(statement_ends(tree)))


def role_switches(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, SQL) for each string expression whose SQL switches the role or a ``zb.*`` setting."""
    text = _Text(tree)
    parents = _parents(tree)
    skip = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if node in skip or not isinstance(node, ast.expr) or (spelled := text.of(node)) is None:
            continue
        if text.of(parents[node]) is not None:  # the enclosing expression reports the whole statement
            continue
        flat = _flat(spelled)
        switch = ANYWHERE.search(flat) or next((m for m in _SET.finditer(flat) if not _sets_a_column(flat, m)), None)
        if switch:
            found.append((node.lineno, switch[0].strip(' ;')))
    return sorted(set(found))


def statement_ends(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, SQL) for each ``execute`` whose SQL ends the transaction: COMMIT, ROLLBACK, END or ABORT."""
    text = _Text(tree)
    return sorted(
        {
            (node.args[0].lineno, end[0].strip(' ;'))
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, 'attr', None) in _EXECUTES
            and node.args
            and (spelled := text.of(node.args[0])) is not None
            and (end := _ended(_flat(spelled)))
        }
    )


def transaction_ends(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, name) for each use of ``commit`` or ``rollback`` as an attribute, called or not (``fn = conn.commit``,
    ``to_thread(conn.commit)``), and ``getattr`` with either name: the pool owns the transaction."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ENDS_TRANSACTION:
            found.append((node.lineno, f'.{node.attr}'))
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, 'id', None) == 'getattr'
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in ENDS_TRANSACTION
        ):
            found.append((node.lineno, f"getattr(..., '{node.args[1].value}')"))
    return sorted(set(found))


_POOL = ('app.core.db._pool',)  # a direct checkout skips the scoped one


def _is_raw(name: str) -> bool:
    """``psycopg.connect`` and ``(Async)Connection.connect``, however the class is imported (``psycopg.connection``)."""
    parts = name.split('.')
    return parts[0] == 'psycopg' and (
        parts[1:] == ['connect'] or parts[-2:] in (['Connection', 'connect'], ['AsyncConnection', 'connect'])
    )


def raw_connections(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, what) for each way to a connection that skips ``get_conn``: ``psycopg.connect``,
    ``(Async)Connection.connect``, anything from ``psycopg_pool`` and the pool of ``app.core.db``, also through an
    alias or an import of the name."""
    canon: dict[str, str] = {}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                canon[alias.asname or top] = alias.name if alias.asname else top
                if alias.name.split('.')[0] == 'psycopg_pool':
                    found.append((node.lineno, f'import {alias.name}'))
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module.split('.')[0] in {'psycopg', 'psycopg_pool'} or node.module in {'app.core', 'app.core.db'})
        ):
            for alias in node.names:
                canon[alias.asname or alias.name] = f'{node.module}.{alias.name}'
                if (
                    node.module.startswith('psycopg_pool')
                    or _is_raw(f'{node.module}.{alias.name}')
                    or f'{node.module}.{alias.name}' in _POOL
                ):
                    found.append((node.lineno, f'from {node.module} import {alias.name}'))

    def resolved(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return canon.get(node.id)
        if isinstance(node, ast.Attribute) and (base := resolved(node.value)):
            return f'{base}.{node.attr}'
        return None

    for _ in range(3):  # an alias of an alias
        for target, value in _assigned(tree):
            if isinstance(target, ast.Name) and (held := resolved(value)):
                canon[target.id] = held
    found += [
        (node.lineno, name)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and (name := resolved(node))
        and (_is_raw(name) or name.startswith('psycopg_pool.') or name in _POOL)
    ]
    return sorted(set(found))


def enclosing(tree: ast.AST, line: int) -> str | None:
    """The qualified name (``Class.method``) of the innermost function around ``line``, or None at module level."""
    best: tuple[int, str] | None = None

    def visit(node: ast.AST, prefix: str) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                name = f'{prefix}{child.name}'
                end = child.end_lineno or child.lineno
                if (
                    not isinstance(child, ast.ClassDef)
                    and child.lineno <= line <= end
                    and (best is None or child.lineno >= best[0])
                ):
                    best = (child.lineno, name)
                visit(child, f'{name}.')
            else:
                visit(child, prefix)

    visit(tree, '')
    return best[1] if best else None


# ── E9: a hand-off to another thread carries the scope ──

#: Attribute calls that run a function on another thread, and the position of that function: an executor's
#: ``submit`` and ``map``, and the loop's ``run_in_executor``. ``asyncio.to_thread`` copies the context itself.
_HANDOFF_METHODS = {'submit': 0, 'map': 0, 'run_in_executor': 1}
#: ``threading`` classes that start a thread, and the position of the function: ``Thread(group, target)``,
#: ``Timer(interval, function)``. Resolved through the import, so a pydantic ``class Thread`` is not one.
_THREADS = {'threading.Thread': (1, 'target'), 'threading.Timer': (1, 'function')}
#: What carries the scope to that thread: ``contextvars.copy_context().run`` or ``scope.for_another_thread().run``.
_CARRIERS = frozenset({'copy_context', 'for_another_thread'})


def _carries_the_scope(fn: ast.expr | None) -> bool:
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr == 'run'
        and isinstance(fn.value, ast.Call)
        and getattr(fn.value.func, 'id', getattr(fn.value.func, 'attr', None)) in _CARRIERS
    )


def _threading_names(tree: ast.AST) -> dict[str, str]:
    """Local name -> ``threading.<name>`` or ``threading`` for each import of the module or of its classes."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.asname or a.name: a.name for a in node.names if a.name == 'threading'}
        elif isinstance(node, ast.ImportFrom) and node.module == 'threading':
            names |= {a.asname or a.name: f'threading.{a.name}' for a in node.names}
    return names


def scope_drops(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, call) for each hand-off to another thread that does not carry the context. The thread starts with an
    empty context, so its checkout raises ``ScopeMissing``, or runs in a scope it entered itself instead of the
    caller's."""
    threading_names = _threading_names(tree)

    def thread_class(func: ast.expr) -> str | None:
        if isinstance(func, ast.Name):
            return threading_names.get(func.id)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = threading_names.get(func.value.id)
            return f'{module}.{func.attr}' if module == 'threading' else None
        return None

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn: ast.expr | None
        if (cls := thread_class(node.func)) in _THREADS:
            at, keyword = _THREADS[cls]
            fn = next((k.value for k in node.keywords if k.arg == keyword), None)
            fn = fn or (node.args[at] if len(node.args) > at else None)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _HANDOFF_METHODS:
            at = _HANDOFF_METHODS[node.func.attr]
            fn = node.args[at] if len(node.args) > at else None
        else:
            continue
        if not _carries_the_scope(fn):
            found.append((node.lineno, ast.unparse(node.func)))
    return sorted(set(found))
