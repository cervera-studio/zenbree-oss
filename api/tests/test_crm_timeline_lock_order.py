"""``ledger.record`` takes its advisory locks in one order, so two sources recording overlapping facts never deadlock."""

from datetime import UTC, datetime

from app.crm.timeline import facts, ledger


class _Conn:
    def __init__(self):
        self.locked: list[str] = []

    def execute(self, sql, args=None):
        if 'pg_advisory_xact_lock' in sql:
            self.locked.append(args[0])
        return self

    def fetchone(self):
        return None


def _fact(project, key):
    return facts.TimelineFact(
        facts.Source.INSTANTLY, f'{project}-{key}', 'o', project, None, facts.Kind.OUTGOING_EMAIL, 'ana@acme.test',
        datetime(2026, 1, 1, tzinfo=UTC), key, None, facts.Person(), facts.Context('instantly', 'c', 'p', None, 1),
    )  # fmt: skip


def test_the_locks_are_taken_in_project_and_key_order_whatever_the_page_order():
    conn = _Conn()
    page = [_fact('p2', 'a'), _fact('p1', 'z'), _fact('p1', 'b')]
    ledger.record(conn, page)
    assert conn.locked == ['p1:b', 'p1:z', 'p2:a']
    other = _Conn()
    ledger.record(other, list(reversed(page)))
    assert other.locked == conn.locked
