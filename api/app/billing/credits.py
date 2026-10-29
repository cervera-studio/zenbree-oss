"""Credit holds (#660): the only Python code that reserves, grows, settles and releases credits, and that reads
whether a hold is still live. Every hold is pay-if-found: the caller holds what the work may cost, then keeps what
it spent and gives the rest back.

The functions take the caller's connection, so a runner settles in the same transaction that writes its outcome.
They never open a connection and never call ``balance.changed``: that runs after the caller's commit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Payer:
    org_id: str
    user_id: str  # who spent
    project_id: str | None  # whose hard monthly cap (#307) bounds the hold


@dataclass(frozen=True, slots=True)
class Hold:
    id: str
    amount: int


@dataclass(frozen=True, slots=True)
class Refused:
    capped: bool  # the project's hard monthly cap refused it, not the wallet


def reserve(
    conn,
    payer: Payer,
    want: int,
    *,
    need: int | None = None,
    unit: int = 1,
    reference_type: str,
    reference_id: str | None,
    tool_name: str | None = None,
    session_id: str | None = None,
    description: str,
) -> Hold | Refused:
    """Holds up to ``want`` credits in whole ``unit``s, bounded by the wallet and by the project's cap room.
    Refused when that is less than ``need`` (default ``want``), or nothing at all."""
    need = want if need is None else need
    for _ in range(2):  # another hold of the project may take room between the read and the locked recheck
        amount = min(want, _room(conn, payer) // unit * unit)
        if amount < max(need, 1):
            break
        rid = conn.execute(
            'SELECT reserve_project_credits(%s, %s, %s, %s, %s, %s, %s, %s, %s) AS r',
            (
                payer.project_id,
                payer.org_id,
                payer.user_id,
                amount,
                reference_type,
                reference_id,
                tool_name,
                session_id,
                description,
            ),
        ).fetchone()['r']
        if rid:
            return Hold(str(rid), amount)
    return _refused(conn, payer, need)


def grow(conn, payer: Payer, hold: Hold, want: int, *, need: int) -> Hold | Refused:
    """Grows ``hold`` by up to ``want`` credits, bounded as ``reserve`` bounds a new hold, so a run keeps one hold.
    Refused when that is less than ``need``, or when the hold is no longer reserved."""
    # A hold taken in an earlier month moves into this one when it grows, so this month's cap room must cover it too.
    carried = conn.execute(
        'SELECT coalesce(sum(amount), 0) AS n FROM credit_transactions'
        " WHERE id = %s AND created_at < date_trunc('month', now())",
        (hold.id,),
    ).fetchone()['n']
    for _ in range(2):  # as in reserve: another hold of the project may take room before the locked recheck
        amount = min(want, _room(conn, payer, carried))
        if amount < max(need, 1):
            break
        grown = conn.execute('SELECT grow_reservation(%s, %s) AS a', (hold.id, amount)).fetchone()['a']
        if grown is not None:
            return Hold(hold.id, grown)
    return _refused(conn, payer, need + carried)


def can_hold(conn, payer: Payer, need: int) -> bool:
    """Whether ``reserve`` would hold ``need`` credits now, by its own rule, with nothing held: for a caller that must
    not queue work that a reserve would refuse."""
    return _room(conn, payer) >= max(need, 1)


def _room(conn, payer: Payer, carried: int = 0) -> int:
    room = conn.execute(
        'SELECT (SELECT credits_remaining FROM user_credits WHERE organization_id = %s) AS wallet,'
        ' project_cap_left(%s) AS cap',
        (payer.org_id, payer.project_id),
    ).fetchone()
    wallet = room['wallet'] or 0
    return wallet if room['cap'] is None else min(wallet, room['cap'] - carried)


def _refused(conn, payer: Payer, need: int) -> Refused:
    # One rule for every caller, also when the wallet or the month moved under the locked recheck: the cap
    # refused it when the project's room this month is less than the least the caller can use.
    capped = conn.execute('SELECT project_cap_left(%s) < %s AS c', (payer.project_id, need)).fetchone()['c']
    return Refused(capped=bool(capped))


def live(conn, hold_id: str) -> bool:
    """The hold is still reserved, locked until the caller's commit: not released by the reaper or a delete."""
    row = conn.execute('SELECT status FROM credit_transactions WHERE id = %s FOR UPDATE', (hold_id,)).fetchone()
    return bool(row and row['status'] == 'reserved')


def find_hold(conn, hold_id: str) -> Hold:
    """The hold ``hold_id`` as ``reserve`` returned it, for a holder that keeps only its id between transactions."""
    row = conn.execute('SELECT amount FROM credit_transactions WHERE id = %s', (hold_id,)).fetchone()
    return Hold(hold_id, row['amount'] if row else 0)


def close(conn, hold_id: str) -> int | None:
    """Settles a reserved hold at what its ``row_charges`` cost, by the rule the reaper uses (``settle_hold``).
    None, with nothing changed, when an earlier close or the reaper closed it."""
    return conn.execute('SELECT settle_hold(%s) AS spent', (hold_id,)).fetchone()['spent']


def kept(conn, hold_id: str) -> int:
    """What a closed hold kept, as its settle wrote it in the ledger: 0 when it was released."""
    row = conn.execute(
        'SELECT coalesce(settlement_net_credits, 0) AS n FROM credit_transactions WHERE id = %s', (hold_id,)
    ).fetchone()
    return row['n'] if row else 0
