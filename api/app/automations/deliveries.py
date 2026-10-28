"""The signed calls of webhook automations (#1031), in ``automation_deliveries``: the idempotency ledger, the queue of
calls that wait for a run, and the run page's "call that started this run". This module is the table's only writer.

Each call ends in one state. ``refused``: the answer was an error, and a replay of its ``webhook-id`` gets the same one.
``pending``: accepted (202), it waits for the beat. From there ``started``, with the run it started, or ``dropped``
with why it never started. A transient refusal (a rotated secret, a full queue) stores nothing, so the sender's retry
with the same ``webhook-id`` can still succeed.

Every writer locks the automation first: a receipt, the beat's pickup and an edit of the automation never interleave.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import NamedTuple

import structlog
from pydantic import ValidationError

from app.automations import hook, person_match
from app.automations.discard import WAITING_FOR_APPROVAL
from app.automations.models import InboundPerson
from app.automations.run_queue import insert_run
from app.core.db import get_conn
from app.core.scope import acting_for

logger = structlog.get_logger()

BATCH = 100  # automations the beat works on each minute
MAX_PENDING = 100  # calls of one automation that wait for a run; one run starts each beat minute
PENDING_TTL = timedelta(days=7)
# A Standard Webhooks sender retries for about 3 days, so a replay finds its stored refusal for any retry window.
RETENTION = timedelta(days=30)

# The stored refusals: (HTTP status, error code, message). A first answer and its replays both read it.
REFUSALS: dict[str, tuple[int, str, str]] = {
    'automation_paused': (409, 'AUTOMATION.WEBHOOK_PAUSED', 'the automation is paused'),
    'creator_gone': (409, 'AUTOMATION.WEBHOOK_NOT_ACCEPTING', 'the automation takes no calls: its creator left'),
    'invalid_body': (422, 'AUTOMATION.WEBHOOK_INVALID_BODY', 'send a JSON object with email and linkedin_url strings'),
    'no_identifier': (422, 'AUTOMATION.WEBHOOK_NO_IDENTIFIER', 'name the person by email or linkedin_url'),
    'no_match': (422, 'AUTOMATION.WEBHOOK_NO_MATCH', 'nobody of the automation list has that email or LinkedIn URL'),
    'already_worked': (409, 'AUTOMATION.WEBHOOK_ALREADY_WORKED', 'the automation works on this person once'),
}


class Accepted(NamedTuple):
    delivery_id: str


class Refused(NamedTuple):
    code: str  # a key of REFUSALS


class Transient(NamedTuple):
    status: int
    code: str
    message: str
    retry_after: int | None = None


type Answer = Accepted | Refused | Transient

UNAUTHORIZED = Transient(401, 'AUTOMATION.WEBHOOK_UNAUTHORIZED', 'invalid webhook signature')
BACKLOG = Transient(429, 'AUTOMATION.WEBHOOK_BACKLOG', f'{MAX_PENDING} calls wait already: try again later', 60)

_WORKED = 'EXISTS (SELECT 1 FROM automation_rows w WHERE w.automation_id = {automation} AND w.row_id = {row})'
# At receipt the person also counts as taken while a call or the live run has them: one call starts one run.
_TAKEN_AT_RECEIPT = (
    _WORKED.format(automation='%(automation)s', row='%(row)s')
    + ' OR EXISTS (SELECT 1 FROM automation_deliveries d WHERE d.automation_id = %(automation)s AND d.row_id = %(row)s'
    " AND d.state = 'pending') OR EXISTS (SELECT 1 FROM automation_runs r WHERE r.automation_id = %(automation)s"
    " AND r.status IN ('queued', 'running') AND (r.target_row_id = %(row)s OR %(row)s = ANY(r.row_ids)))"
)


def _answer(row: dict) -> Answer:
    return Refused(row['code']) if row['state'] == 'refused' else Accepted(str(row['id']))


def _stored(conn, automation_id: str, webhook_id: str) -> dict | None:
    return conn.execute(
        'SELECT id, state, code FROM automation_deliveries WHERE automation_id = %s AND webhook_id = %s',
        (automation_id, webhook_id),
    ).fetchone()


def _person(body: bytes) -> InboundPerson | None:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return InboundPerson.model_validate(payload)
    except ValidationError:
        return None


def _decide(conn, automation: dict, body: bytes) -> tuple[str | None, person_match.Match | None]:
    """The refusal code of a new call, or ``None``, and the row it names."""
    if not automation['enabled']:
        return 'automation_paused', None
    live = conn.execute(
        'SELECT zb.is_live_member(%s, %s) AS ok', (automation['user_id'], automation['organization_id'])
    ).fetchone()
    if not live['ok']:
        return 'creator_gone', None
    person = _person(body)
    if person is None:
        return 'invalid_body', None
    named = person_match.identifiers(person)
    if not named:
        return 'no_identifier', None
    match = person_match.find(conn, str(automation['project_id']), automation['definition']['list_id'], named)
    if match is None:
        return 'no_match', None
    taken = conn.execute(
        f'SELECT {_TAKEN_AT_RECEIPT} AS taken', {'automation': automation['id'], 'row': match.row_id}
    ).fetchone()
    return ('already_worked' if taken['taken'] else None), match


def record(automation_id: str, webhook_id: str, verified_with: str, body: bytes) -> Answer:
    """One call whose signature checked out against ``verified_with`` (the stored ciphertext), in the automation's
    organization. A repeated ``webhook-id`` gets the stored answer and changes nothing."""
    with get_conn() as conn:
        automation = conn.execute('SELECT * FROM automations WHERE id = %s FOR UPDATE', (automation_id,)).fetchone()
        sealed = conn.execute(
            'SELECT secret_ciphertext FROM automation_webhooks WHERE automation_id = %s', (automation_id,)
        ).fetchone()
        # A rotation or a switch away from webhook since the signature was checked: that secret no longer counts.
        if not automation or not sealed or sealed['secret_ciphertext'] != verified_with:
            return UNAUTHORIZED
        if stored := _stored(conn, automation_id, webhook_id):
            return _answer(stored)
        code, match = _decide(conn, automation, body)
        if code is None and _pending(conn, automation_id) >= MAX_PENDING:
            return BACKLOG
        row = conn.execute(
            'INSERT INTO automation_deliveries (organization_id, automation_id, webhook_id, state, code, matched_by,'
            ' row_id, decided_at) VALUES (%(org)s, %(automation)s, %(webhook)s, %(state)s, %(code)s, %(by)s, %(row)s,'
            " CASE WHEN %(state)s = 'refused' THEN now() END)"
            ' RETURNING id, state, code',
            {
                'org': automation['organization_id'],
                'automation': automation_id,
                'webhook': webhook_id,
                'state': 'pending' if code is None else 'refused',
                'code': code,
                'by': match.matched_by if match else None,
                'row': match.row_id if match else None,
            },
        ).fetchone()
        return _answer(row)


def _pending(conn, automation_id) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM automation_deliveries WHERE automation_id = %s AND state = 'pending'",
        (automation_id,),
    ).fetchone()['n']


def _drop_pending(conn, automation_id, code: str) -> None:
    conn.execute(
        "UPDATE automation_deliveries SET state = 'dropped', code = %s, decided_at = now()"
        " WHERE automation_id = %s AND state = 'pending'",
        (code, automation_id),
    )


def automation_changed(conn, before: dict, after: dict) -> None:
    """An edit under the automation's lock (``service.update``). Pending calls of a paused automation, or of one that
    left the webhook trigger, never start; leaving it also removes the hook."""
    if before['trigger_kind'] == 'webhook' and after['trigger_kind'] != 'webhook':
        _drop_pending(conn, before['id'], 'trigger_changed')
        hook.remove(conn, before['id'])
    elif after['trigger_kind'] == 'webhook' and not after['enabled']:
        _drop_pending(conn, before['id'], 'automation_paused')


def of_run(conn, run_id) -> dict | None:
    return conn.execute(
        'SELECT id, webhook_id, received_at, matched_by, row_id FROM automation_deliveries WHERE run_id = %s',
        (run_id,),
    ).fetchone()


_STARTABLE = (
    "a.enabled AND a.trigger_kind = 'webhook' AND zb.is_live_member(a.user_id, a.organization_id)"
    " AND NOT EXISTS (SELECT 1 FROM automation_runs l WHERE l.automation_id = a.id AND l.status IN ('queued', 'running'))"
    f' AND NOT ({WAITING_FOR_APPROVAL})'
)
# The oldest pending call, and why it can never start: its row left the list, or a run took the person since.
_HEAD = (
    'SELECT d.id, d.row_id, CASE WHEN r.id IS NULL THEN %s WHEN'
    f' {_WORKED.format(automation="d.automation_id", row="d.row_id")} THEN %s END AS never'
    ' FROM automation_deliveries d LEFT JOIN list_rows r ON r.id = d.row_id AND r.list_id = %s'
    " WHERE d.automation_id = %s AND d.state = 'pending' ORDER BY d.received_at LIMIT 1"
)


# Automations whose oldest pending call can start now, oldest first. Held ones are left out here, so they never take
# the batch from the others; start_next checks again under the lock.
_DUE = (
    'SELECT h.automation_id::text, h.organization_id::text FROM (SELECT DISTINCT ON (automation_id) automation_id,'
    " organization_id, received_at FROM automation_deliveries WHERE state = 'pending'"
    f' ORDER BY automation_id, received_at) h JOIN automations a ON a.id = h.automation_id WHERE {_STARTABLE}'
    ' ORDER BY h.received_at LIMIT %s'
)
# Automations with a call past its time, whether or not they get calls or can start.
_OLD = (
    'SELECT DISTINCT automation_id::text, organization_id::text FROM automation_deliveries'
    " WHERE (state = 'pending' AND received_at < now() - %(ttl)s)"
    " OR (state IN ('refused', 'dropped') AND decided_at < now() - %(retention)s) LIMIT %(batch)s"
)


def pickup() -> int:
    """Tidy the old calls, then start the oldest pending call of each automation that may run now: how many started.
    Called in the beat's System scope, which only selects: each automation is worked on as its organization."""
    with get_conn() as conn:
        old = conn.execute(_OLD, {'ttl': PENDING_TTL, 'retention': RETENTION, 'batch': BATCH}).fetchall()
        due = conn.execute(_DUE, (BATCH,)).fetchall()
    for each in old:
        _as_its_organization(each, tidy)
    return sum(_as_its_organization(each, start_next) for each in due)


def _as_its_organization(head: dict, work: Callable[[str], bool]) -> bool:
    # One automation that fails must not stop the others, nor the schedules the beat queues after this.
    try:
        with acting_for(head['organization_id'], 'automation webhook delivery'):
            return work(head['automation_id'])
    except Exception as exc:
        logger.error(
            'automation.webhook_pickup_failed', automation_id=head['automation_id'], error_type=type(exc).__name__
        )
        return False


def _expire(conn, automation_id: str) -> None:
    conn.execute(
        "UPDATE automation_deliveries SET state = 'dropped', code = 'expired', decided_at = now()"
        " WHERE automation_id = %s AND state = 'pending' AND received_at < now() - %s",
        (automation_id, PENDING_TTL),
    )


def tidy(automation_id: str) -> bool:
    """Expire the calls that waited too long and forget the ended ones past retention: whether it got the lock."""
    with get_conn() as conn:
        locked = conn.execute(
            'SELECT 1 FROM automations WHERE id = %s FOR UPDATE SKIP LOCKED', (automation_id,)
        ).fetchone()
        if locked is None:
            return False
        _expire(conn, automation_id)
        conn.execute(
            'DELETE FROM automation_deliveries WHERE automation_id = %s AND state IN (%s, %s)'
            ' AND decided_at < now() - %s',
            (automation_id, 'refused', 'dropped', RETENTION),
        )
        return True


def start_next(automation_id: str) -> bool:
    """Queue a run for the oldest pending call, in one transaction: whether one started. Read again from the rows on
    each try, so a crash or a second beat changes nothing. Calls that can never start are dropped on the way."""
    with get_conn() as conn:
        automation = conn.execute(
            'SELECT * FROM automations WHERE id = %s FOR UPDATE SKIP LOCKED', (automation_id,)
        ).fetchone()
        if automation is None:  # a call, an edit or another beat has it: the next tick
            return False
        _expire(conn, automation_id)
        startable = conn.execute(f'SELECT {_STARTABLE} AS ok FROM automations a WHERE a.id = %s', (automation_id,))
        if not startable.fetchone()['ok']:
            return False
        list_id = automation['definition']['list_id']
        while head := conn.execute(_HEAD, ('person_gone', 'already_worked', list_id, automation_id)).fetchone():
            if head['never']:
                conn.execute(
                    "UPDATE automation_deliveries SET state = 'dropped', code = %s, decided_at = now() WHERE id = %s",
                    (head['never'], head['id']),
                )
                continue
            run = insert_run(conn, automation, None, 'webhook', head['row_id'])
            if run is None:
                return False
            conn.execute(
                "UPDATE automation_deliveries SET state = 'started', run_id = %s, decided_at = now() WHERE id = %s",
                (run['id'], head['id']),
            )
            return True
    return False
