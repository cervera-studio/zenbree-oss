"""Per-row charges (#45, #416, #660): one ``row_charges`` record per row that a chat turn's tool call or a row's
phone click billed, named by the hold that pays for it, and the only code that writes that table.

A charge on a row that misses a required column when it is charged is ``held``: if the row still misses one when
the turn ends, ``refund_unfinished`` refunds it and takes back the cells it bought. Until then, while its hold is
reserved, the list hides those cells (``PENDING_KEYS_SQL``). A charge on a complete row is ``charged`` at once, and
so is a held one whose cells a campaign enrolled (#462, ``finalize``) or whose hold closed (``settle_hold``, also the
reaper's when the turn died): a later plan, exclusion or hand edit never refunds what the user could read."""

from collections.abc import Sequence
from typing import NamedTuple

from app.lists.rows import UNFINISHED_SQL


def _bought(key: str) -> str:
    """On ``list_rows r``: the cell ``key`` (an SQL text expression) still holds what a charge bought. A hand edit
    (``cell_meta`` status ``manual``) replaced it, so no charge hides it, takes it back or reads as refunded."""
    return f"r.cell_meta->{key}->>'status' IS DISTINCT FROM 'manual'"


# The keys of ``list_rows r`` that a held charge bought while its hold is still reserved: no reader sees them yet.
# A statement that locks the row reads them in a later one: a lock wait re-reads the row, not row_charges.
PENDING_KEYS_SQL = (
    'ARRAY(SELECT k FROM row_charges c JOIN credit_transactions h ON h.id = c.hold_id, unnest(c.keys) k'
    f" WHERE c.row_id = r.id AND c.state = 'held' AND h.status = 'reserved' AND {_bought('k')})"
)


def refunded_sql(key: str) -> str:
    """On ``list_rows r``: the latest charge of the cell ``key`` was refunded. A later charge that bought it again
    wins, and so does a hand edit."""
    cell = f"'{key}'"
    return (
        f"((SELECT c.state FROM row_charges c WHERE c.row_id = r.id AND '{key}' = ANY(c.keys)"
        f" ORDER BY c.created_at DESC LIMIT 1) IS NOT DISTINCT FROM 'refunded' AND {_bought(cell)})"
    )


class Refund(NamedTuple):
    """A held charge that ``refund_unfinished`` took back. ``call_id`` is None on one moved from ``cell_meta``
    (#660), which named no call and recorded no credits."""

    call_id: str | None
    row_id: str
    credits: int


def charge(
    conn,
    hold_id: str,
    job_id: str | None,
    call_id: str,
    list_id: str,
    row_ids: Sequence[str],
    keys: Sequence[str],
    credits: int,
) -> None:
    """In the transaction that stores the billed cells: one record of ``credits`` per row of ``list_id`` (#668), paid
    by ``hold_id``, for the billed ``keys`` the row holds. Held when the row is unfinished now, else charged.

    Those cells now hold what the charge bought, so a ``manual`` mark left on one by an earlier hand edit becomes
    ``filled``: else ``_bought`` would read the bought value as the user's, show it while held and keep it refunded."""
    conn.execute(
        'WITH c AS (INSERT INTO row_charges (hold_id, job_id, tool_call_id, row_id, keys, credits, state, settled_at)'
        " SELECT %s, %s, %s, r.id, k.keys, %s, s.state, CASE WHEN s.state = 'charged' THEN now() END"
        ' FROM list_rows r'
        ' CROSS JOIN LATERAL (SELECT ARRAY(SELECT key FROM unnest(%s::text[]) key WHERE r.cells ? key) AS keys) k'
        " CROSS JOIN LATERAL (SELECT CASE WHEN k.keys <> '{}' AND"
        f" {UNFINISHED_SQL} THEN 'held' ELSE 'charged' END AS state) s"
        ' WHERE r.id = ANY(%s::uuid[]) AND r.list_id = %s RETURNING row_id, keys)'
        ' UPDATE list_rows r SET cell_meta = r.cell_meta || t.meta'
        """ FROM (SELECT c.row_id, jsonb_object_agg(k, '{"status": "filled"}'::jsonb) AS meta FROM c"""
        f' JOIN list_rows r ON r.id = c.row_id, unnest(c.keys) k WHERE NOT {_bought("k")} GROUP BY c.row_id) t'
        ' WHERE r.id = t.row_id',
        (hold_id, job_id, call_id, credits, list(keys), list(row_ids), list_id),
    )


def refund_unfinished(conn, hold_id: str) -> list[Refund]:
    """At the end of the turn, with its hold locked: a held charge of ``hold_id`` on a row still unfinished is
    refunded and the row loses the cells it bought, except a cell typed by hand since; every other one is charged.

    The held records are locked first, so a ``finalize`` that committed meanwhile leaves them charged (#462): the
    lock wait rechecks ``state``. A retried refund finds nothing held."""
    locked = conn.execute(
        'SELECT id, tool_call_id, row_id::text, keys, credits, created_at FROM row_charges'
        " WHERE hold_id = %s AND state = 'held' ORDER BY id FOR UPDATE",  # finalize locks by id too: no deadlock
        (hold_id,),
    ).fetchall()
    held = sorted(locked, key=lambda c: (c['created_at'], c['id']))  # refunds_by_call caps the oldest calls first
    unfinished = {
        r['id']
        for r in conn.execute(
            # finalize and the refill lock rows by id too: no deadlock
            f'SELECT r.id::text FROM list_rows r WHERE r.id = ANY(%s::uuid[]) AND {UNFINISHED_SQL}'
            ' ORDER BY r.id FOR NO KEY UPDATE',
            (list({c['row_id'] for c in held}),),
        )
    }
    refunded = [c for c in held if c['row_id'] in unfinished]
    conn.execute(
        "UPDATE row_charges SET state = CASE WHEN id = ANY(%s::uuid[]) THEN 'refunded' ELSE 'charged' END,"
        ' settled_at = now() WHERE id = ANY(%s::uuid[])',
        ([c['id'] for c in refunded], [c['id'] for c in held]),
    )
    conn.execute(
        'UPDATE list_rows r SET cells = r.cells - t.keys, cell_meta = r.cell_meta - t.keys'
        ' FROM (SELECT c.row_id, array_agg(DISTINCT k) AS keys FROM row_charges c'
        ' JOIN list_rows r ON r.id = c.row_id, unnest(c.keys) k'
        f' WHERE c.id = ANY(%s::uuid[]) AND {_bought("k")} GROUP BY c.row_id) t WHERE r.id = t.row_id',
        ([c['id'] for c in refunded],),
    )
    return [Refund(c['tool_call_id'], c['row_id'], c['credits']) for c in refunded]


def settled_lists(conn, hold_id: str) -> list[str]:
    """The list id of every row a charge of ``hold_id`` was held on, once the hold closed: a refunded, kept or
    finalized row shows other cells than the list showed while the holder ran."""
    # A charge final at once settled when it was made (settled_at = created_at); a held one settled later.
    lists = conn.execute(
        'SELECT r.list_id::text FROM list_rows r WHERE r.id IN'
        ' (SELECT row_id FROM row_charges WHERE hold_id = %s AND settled_at > created_at)',
        (hold_id,),
    )
    return [r['list_id'] for r in lists]


def refunds_by_call(refunded: Sequence[Refund], spent: int) -> dict[str, tuple[int, int]]:
    """What each tool call gives back of the charges ``refund_unfinished`` refunded, as (credits, rows), never more
    in all than the turn ``spent``. A call the turn never recorded (it raised after it charged) gives back too."""
    taken: dict[str, tuple[int, set[str]]] = {}
    for refund in refunded:
        if refund.call_id is None:
            continue
        credits, rows = taken.get(refund.call_id, (0, set()))
        taken[refund.call_id] = (credits + refund.credits, rows | {refund.row_id})
    given: dict[str, tuple[int, int]] = {}
    for call_id, (credits, rows) in taken.items():
        if back := min(spent, credits):
            spent -= back
            given[call_id] = (back, len(rows))
    return given


def finalize_hold(conn, hold_id: str) -> None:
    """Makes the held charges of ``hold_id`` final now, as its close will: a holder that never refunds (an
    automation run) can then read in a later step the cells it bought, which ``PENDING_KEYS_SQL`` hides until then.
    Its spend does not change: ``settle_hold`` counts every charge that was not refunded."""
    conn.execute(
        "UPDATE row_charges SET state = 'charged', settled_at = now() WHERE id IN (SELECT id FROM row_charges"
        " WHERE hold_id = %s AND state = 'held' ORDER BY id FOR UPDATE)",  # refund_unfinished's lock order
        (hold_id,),
    )


def finalize(conn, row_ids: Sequence[str]) -> None:
    """Makes every held charge on ``row_ids`` final: their cells left for a campaign, so like a charge on a complete
    row it is never refunded. ``refund_unfinished`` then never refunds them, and ``settled_lists`` still names
    their list."""
    conn.execute(
        "UPDATE row_charges SET state = 'charged', settled_at = now() WHERE id IN (SELECT id FROM row_charges"
        " WHERE row_id = ANY(%s::uuid[]) AND state = 'held' ORDER BY id FOR UPDATE)",  # refund_unfinished's lock order
        (list(row_ids),),
    )


_EMAIL_CELL = "'email'"


def refund_bounced(conn, list_id: str, email: str) -> dict | None:
    """#747: the newest final charge that bought the verified address ``email`` on a row of ``list_id`` (the
    campaign's list) becomes ``refunded``, once: its ``credits``, ``row_id`` and ``keys``, None when there is none.
    The charge and its row are locked, so the row's email cannot change before the caller takes the cells back in the
    same transaction. A charge whose hold is still reserved is the turn's to settle (refunding it here would pay it
    back twice), and an address typed by hand over the bought one was not bought. Rows of other lists that share the
    address are never touched: the exclusion covers them."""
    from app.lists.lookups import verified_email_sql  # lookups reads this module's SQL

    return conn.execute(
        "UPDATE row_charges SET state = 'refunded', settled_at = now() WHERE state = 'charged' AND id = ("
        ' SELECT c.id FROM row_charges c JOIN list_rows r ON r.id = c.row_id'
        ' LEFT JOIN credit_transactions h ON h.id = c.hold_id'
        f" WHERE r.list_id = %s AND lower(btrim(r.cells->>'email')) = %s AND {verified_email_sql('r.cells')}"
        f' AND {_bought(_EMAIL_CELL)}'
        " AND 'email' = ANY(c.keys) AND c.state = 'charged' AND c.credits > 0 AND h.status IS DISTINCT FROM 'reserved'"
        # refund_unfinished's lock order: the charge, then its row
        ' ORDER BY c.created_at DESC LIMIT 1 FOR UPDATE OF c, r) RETURNING credits, row_id::text, keys',
        (list_id, email.strip().lower()),
    ).fetchone()
