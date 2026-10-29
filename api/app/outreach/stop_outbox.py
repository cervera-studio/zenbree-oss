from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from app.integrations import instantly_store
from app.integrations.instantly_store import HELD

StopReason = Literal['human_reply', 'opt_out', 'hard_bounce']
ConfirmedBy = Literal['provider', 'not_enrolled', 'user']

MAX_ATTEMPTS = 10
BASE_BACKOFF = timedelta(minutes=1)
MAX_BACKOFF = timedelta(hours=4)

# Literals, not a parameter: the planner then proves the predicate of instantly_leads_held_idx.
_HELD = ', '.join(f"'{outcome}'" for outcome in HELD)


class StopStatus(StrEnum):
    PENDING = 'pending'
    FAILED = 'failed'
    CONFIRMED = 'confirmed'


@dataclass(frozen=True, slots=True)
class Stopped:
    """Instantly no longer sends to the lead: deleted now, already gone, or already stopped by Instantly."""


@dataclass(frozen=True, slots=True)
class NotEnrolled:
    """The lead never reached Instantly: nothing to stop."""


@dataclass(frozen=True, slots=True)
class Later:
    code: str


@dataclass(frozen=True, slots=True)
class Refused:
    code: str


@dataclass(frozen=True, slots=True)
class Retry:
    """The user asks for a failed stop again."""


@dataclass(frozen=True, slots=True)
class ConfirmedByHand:
    user_id: str


type Event = Stopped | NotEnrolled | Later | Refused | Retry | ConfirmedByHand


@dataclass(frozen=True, slots=True)
class Stop:
    id: str
    status: StopStatus
    attempts: int
    xmin: str | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class Change:
    status: StopStatus
    attempts: int
    next_attempt_at: datetime | None
    last_error_code: str | None
    confirmed_by: ConfirmedBy | None = None
    confirmed_user_id: str | None = None


def backoff(attempts: int) -> timedelta:
    return min(MAX_BACKOFF, BASE_BACKOFF * 2**attempts)


def transition(stop: Stop, event: Event, now: datetime) -> Change | None:
    tried = stop.attempts + 1
    match stop.status, event:
        case StopStatus.PENDING, Stopped():
            return Change(StopStatus.CONFIRMED, tried, None, None, 'provider')
        case StopStatus.PENDING, NotEnrolled():
            return Change(StopStatus.CONFIRMED, tried, None, None, 'not_enrolled')
        case StopStatus.PENDING, Refused(code):
            return Change(StopStatus.FAILED, tried, None, code)
        case StopStatus.PENDING, Later(code) if tried >= MAX_ATTEMPTS:
            return Change(StopStatus.FAILED, tried, None, code)
        case StopStatus.PENDING, Later(code):
            return Change(StopStatus.PENDING, tried, now + backoff(stop.attempts), code)
        case StopStatus.FAILED, Retry():
            return Change(StopStatus.PENDING, 0, now, None)
        case StopStatus.FAILED, ConfirmedByHand(user_id):
            return Change(StopStatus.CONFIRMED, stop.attempts, None, None, 'user', user_id)
    return None


def settle(conn, stop: Stop, event: Event, now: datetime) -> Stop | None:
    change = transition(stop, event, now)
    if change is None:
        return None
    row = conn.execute(
        """UPDATE outreach_stops
              SET status = %(status)s, attempts = %(attempts)s,
                  next_attempt_at = coalesce(%(next)s, next_attempt_at), last_error_code = %(code)s,
                  confirmed_at = CASE WHEN %(status)s = 'confirmed' THEN %(now)s END,
                  confirmed_by = %(by)s, confirmed_user_id = %(user)s, updated_at = %(now)s
            WHERE id = %(id)s AND status = %(was)s AND attempts = %(was_attempts)s
              AND (%(xmin)s::xid IS NULL OR xmin = %(xmin)s::xid)
        RETURNING id::text, status, attempts, xmin::text AS xmin""",
        {
            'status': change.status.value,
            'attempts': change.attempts,
            'next': change.next_attempt_at,
            'code': change.last_error_code,
            'by': change.confirmed_by,
            'user': change.confirmed_user_id,
            'now': now,
            'id': stop.id,
            'was': stop.status.value,
            'was_attempts': stop.attempts,
            'xmin': stop.xmin,
        },
    ).fetchone()
    return _stop(row) if row else None


def _stop(row) -> Stop:
    return Stop(row['id'], StopStatus(row['status']), row['attempts'], row['xmin'])


def request(
    conn, *, project_id: str, emails: list[str], reason: StopReason, cause: str, source_lead_id: str | None = None
) -> int:
    if not emails:
        return 0
    if source_lead_id:
        _keep_lead_with_own_event(conn, source_lead_id, project_id)
    return len(
        conn.execute(
            f"""INSERT INTO outreach_stops (organization_id, project_id, connection_id, lead_id, email, reason, cause,
                                            keep_lead, status, last_error_code)
                SELECT l.organization_id, l.project_id, c.id, l.id, l.email, %(reason)s, %(cause)s,
                       l.id IS NOT DISTINCT FROM %(source)s::uuid,
                       CASE WHEN born_failed.code IS NULL THEN 'pending' ELSE 'failed' END, born_failed.code
                  FROM instantly_leads l JOIN instantly_connections c ON c.project_id = l.project_id
                 CROSS JOIN LATERAL (SELECT CASE WHEN c.status = 'disconnected' THEN 'INSTANTLY.DISCONNECTED'
                                                 WHEN c.status <> 'connected' THEN 'INSTANTLY.INVALID_KEY'
                                                 WHEN l.workspace_id <> c.workspace_id
                                                 THEN 'INSTANTLY.WORKSPACE_CHANGED' END AS code) born_failed
                 WHERE l.project_id = %(project)s AND l.email = ANY(%(emails)s) AND l.outcome IN ({_HELD})
                ON CONFLICT (lead_id) DO NOTHING RETURNING id""",
            {'reason': reason, 'cause': cause, 'source': source_lead_id, 'project': project_id, 'emails': emails},
        ).fetchall()
    )


def _keep_lead_with_own_event(conn, lead_id: str, project_id: str) -> None:
    conn.execute(
        'UPDATE outreach_stops SET keep_lead = true WHERE lead_id = %s AND project_id = %s AND NOT keep_lead'
        " AND status <> 'confirmed'",
        (lead_id, project_id),
    )


_STOP = 's.id::text, s.status, s.attempts, s.xmin::text AS xmin'

DUE_FOR = (
    "EXISTS (SELECT 1 FROM outreach_stops d WHERE d.connection_id = {} AND d.status = 'pending'"
    ' AND d.next_attempt_at <= now())'
)


@dataclass(frozen=True, slots=True)
class Due:
    stop: Stop
    email: str
    keep_lead: bool
    outcome: str
    instantly_lead_id: str | None
    campaign_id: str
    workspace_id: str


def due(conn, connection_id: str, limit: int) -> list[Due]:
    rows = conn.execute(
        f"""SELECT {_STOP}, s.email, s.keep_lead, l.outcome, l.instantly_lead_id, l.campaign_id, l.workspace_id
              FROM outreach_stops s JOIN instantly_leads l ON l.id = s.lead_id
             WHERE s.connection_id = %s AND s.status = 'pending' AND s.next_attempt_at <= now()
             ORDER BY s.next_attempt_at, s.id LIMIT %s""",
        (connection_id, limit),
    ).fetchall()
    return [
        Due(
            _stop(r),
            r['email'],
            r['keep_lead'],
            r['outcome'],
            r['instantly_lead_id'],
            r['campaign_id'],
            r['workspace_id'],
        )
        for r in rows
    ]


def unchanged(conn, stop: Stop) -> bool:
    """The stop is still the pending row version that a pass read, and still lets its lead be deleted."""
    row = conn.execute(
        "SELECT 1 FROM outreach_stops WHERE id = %s AND status = 'pending' AND xmin = %s::xid AND NOT keep_lead",
        (stop.id, stop.xmin),
    ).fetchone()
    return row is not None


def fail_connection(conn, connection_id: str, code: str) -> int:
    return conn.execute(
        "UPDATE outreach_stops SET status = 'failed', last_error_code = %s, updated_at = now()"
        " WHERE connection_id = %s AND status = 'pending'",
        (code, connection_id),
    ).rowcount


def refuse_key(conn, connection_id: str, connected_at: datetime, code: str) -> None:
    """Instantly refused the key of the connection made at ``connected_at`` (``INVALID_KEY``, or ``PLAN_REQUIRED``):
    its pending stops fail, so the user sees them, and a refused key sets the connection to error. A reconnect keeps
    the row and moves ``connected_at``: an answer to the old key then writes nothing, and the stops stay with the new
    key. Every caller that sees Instantly refuse a key calls this."""
    if not instantly_store.lock_connected(conn, connection_id, connected_at):
        return
    fail_connection(conn, connection_id, code)
    if code == 'INSTANTLY.INVALID_KEY':
        instantly_store.mark_error(conn, connection_id, 'Instantly refused the API key')


def rearm_connection(conn, connection_id: str, workspace_id: str) -> int:
    """A connect: the stops of a lead in another workspace fail, as no key of this one can stop it, and every other
    open stop is attempted again from the start with the new key. A pending one too: the new row version makes an
    answer to the old key, still in flight, settle nothing (``settle`` matches ``xmin``). Returns the stops re-armed."""
    conn.execute(
        "UPDATE outreach_stops s SET status = 'failed', last_error_code = 'INSTANTLY.WORKSPACE_CHANGED',"
        ' updated_at = now() FROM instantly_leads l'
        " WHERE l.id = s.lead_id AND s.connection_id = %s AND s.status IN ('pending', 'failed')"
        ' AND l.workspace_id <> %s',
        (connection_id, workspace_id),
    )
    return conn.execute(
        "UPDATE outreach_stops s SET status = 'pending', attempts = 0, next_attempt_at = now(), last_error_code = NULL,"
        ' updated_at = now() FROM instantly_leads l'
        " WHERE l.id = s.lead_id AND s.connection_id = %s AND s.status IN ('pending', 'failed') AND l.workspace_id = %s",
        (connection_id, workspace_id),
    ).rowcount


def find(conn, project_id: str, stop_id: str) -> Stop | None:
    row = conn.execute(
        f'SELECT {_STOP} FROM outreach_stops s WHERE s.id = %s AND s.project_id = %s', (stop_id, project_id)
    ).fetchone()
    return _stop(row) if row else None


VIEW = """SELECT s.id, s.email, s.reason, s.status, s.attempts, s.last_error_code AS error_code,
                 CASE WHEN s.status = 'pending' THEN s.next_attempt_at END AS next_attempt_at,
                 s.created_at, s.confirmed_at, l.campaign_id
            FROM outreach_stops s JOIN instantly_leads l ON l.id = s.lead_id"""


def view(conn, project_id: str, stop_id: str) -> dict:
    return conn.execute(f'{VIEW} WHERE s.id = %s AND s.project_id = %s', (stop_id, project_id)).fetchone()


def open_stops(conn, project_id: str, limit: int) -> dict:
    items = conn.execute(
        f"""{VIEW} WHERE s.project_id = %s AND s.status <> 'confirmed'
             ORDER BY s.status = 'failed' DESC, s.created_at, s.id LIMIT %s""",
        (project_id, limit),
    ).fetchall()
    counts = conn.execute(
        "SELECT count(*) FILTER (WHERE status = 'pending') AS pending, count(*) FILTER (WHERE status = 'failed') AS failed"
        ' FROM outreach_stops WHERE project_id = %s',
        (project_id,),
    ).fetchone()
    return {'items': items, **counts}
