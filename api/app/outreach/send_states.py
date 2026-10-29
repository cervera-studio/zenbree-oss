"""The moves of ``outreach_campaign_sends.status``, in one table.

Every status write names its target here: a join keeps its own SQL and splices ``guard``, a write by id calls
``move``. A write whose row is no longer in a source state changes nothing. The keys equal the table's CHECK
(``tests/test_send_states_sql.py``).
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog

logger = structlog.get_logger()

ALLOWED: dict[str, frozenset[str]] = {
    'awaiting_approval': frozenset({'approved', 'skipped'}),
    'approved': frozenset({'sending', 'refused', 'skipped'}),
    'failed': frozenset({'sending', 'refused', 'skipped'}),
    # 'approved': a rate limit gives the attempt back. No self-loop: a claim guards on sources('sending').
    'sending': frozenset({'sent', 'failed', 'refused', 'skipped', 'approved'}),
    'skipped': frozenset({'awaiting_approval'}),  # undo of a reject
    'sent': frozenset(),
    'refused': frozenset(),
}


UNTAKEN = frozenset({'awaiting_approval', 'approved'})


def sources(dst: str) -> frozenset[str]:
    return frozenset(src for src, targets in ALLOWED.items() if dst in targets)


def guard(dst: str, *, frm: Iterable[str] | None = None, column: str = 'status') -> str:
    """The predicate that the row may move to ``dst``. ``frm`` narrows the sources; literals, not a parameter, so
    the planner still proves the predicates of the partial status indexes."""
    allowed = sources(dst)
    picked = allowed if frm is None else frozenset(frm)
    if not picked or not picked <= allowed:
        raise ValueError(f'outreach_campaign_sends: no move from {sorted(picked - allowed) or "anything"} to {dst}')
    return f'{column} IN ({", ".join(f"{s!r}" for s in sorted(picked))})'


def move(conn, send_id, frm: Iterable[str], dst: str, *, set_: str = '', params: tuple = ()) -> bool:
    """Whether send ``send_id`` moved from one of ``frm`` to ``dst``. ``set_`` is constant SQL; ``params`` fill it.

    ``frm`` has no default: ``sources(dst)`` is often wider than the states one writer leaves (``approved`` is also
    reached from ``awaiting_approval``), and a write by id must not move a row another writer moved first."""
    row = conn.execute(
        f'UPDATE outreach_campaign_sends SET status = %s{", " + set_ if set_ else ""}'
        f' WHERE id = %s AND {guard(dst, frm=frm)} RETURNING id',
        (dst, *params, send_id),
    ).fetchone()
    if row is None:
        logger.warning('outreach.send.move_refused', send_id=str(send_id), to=dst)
    return row is not None


def keep_sending(conn, send_id, code: str) -> bool:
    """An outcome no one knows: the send stays ``sending`` (never resent) and keeps the code. Not a move."""
    return bool(
        conn.execute(
            "UPDATE outreach_campaign_sends SET error_code = %s WHERE id = %s AND status = 'sending'", (code, send_id)
        ).rowcount
    )
