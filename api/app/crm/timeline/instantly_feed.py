"""Instantly's outcomes as timeline facts (#932). The feed is ``outcomes.changes`` read page by page from a cursor
that moves in the transaction that records the page, so a crash records nothing and moves nothing.

A fact is dropped here only when it can never be due. The write-back start moves forward only (``set_enabled``: NULL
on off, ``now()`` on off to on; a portal change: ``now()``), so a fact older than the owner's start, or older than a
read that found the switch off, stays outside however the user turns it on later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

from app.core import scope as scopes
from app.core.db import get_conn
from app.crm.timeline import ledger
from app.crm.timeline.facts import Context, Kind, Person, Source, TimelineFact, normalise_message_id
from app.integrations.instantly_sync import outcomes

logger = structlog.get_logger()

SOURCE = Source.INSTANTLY
PROVIDER = 'Instantly'
PAGE = 200
MAX_PAGES = 5  # per project and sweep: a first pass over a long history takes a few sweeps


class Admit(StrEnum):
    RECORD = 'record'
    DROP = 'drop'


@dataclass(frozen=True, slots=True)
class Windows:
    """Read once per sweep, in one statement, with ``read_at`` the database's ``now()``. It keys on the switch and
    the start only, with no status filter: a revoked grant keeps both, and so does a reconnect to the same portal, so
    what happened while it was revoked becomes due again and must be recorded."""

    read_at: datetime
    since: dict[str, datetime]  # owner id -> write-back start, for the switches that are on
    org_start: dict[str, datetime]  # organization -> the earliest start of its live members with the switch on


def read_windows() -> Windows:
    """As System."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.read_at, h.user_id::text AS user_id, h.crm_write_back_since AS since,
                      m.organization_id::text AS organization_id
               FROM (SELECT now() AS read_at) t
               LEFT JOIN user_oauth_connections h ON h.provider = 'hubspot' AND h.crm_write_back
                    AND h.crm_write_back_since IS NOT NULL
               LEFT JOIN zb.live_members m ON m.user_id = h.user_id"""
        ).fetchall()
    since: dict[str, datetime] = {}
    org_start: dict[str, datetime] = {}
    for r in rows:
        if r['user_id'] is None:
            continue
        since[r['user_id']] = r['since']
        org = r['organization_id']
        if org and (org not in org_start or r['since'] < org_start[org]):
            org_start[org] = r['since']
    return Windows(rows[0]['read_at'], since, org_start)


def admit(occurred_at: datetime, owner: str | None, org: str, w: Windows) -> Admit:
    """Pure. ``DROP`` when the fact can never be due. A fact after ``read_at`` is always recorded: a switch turned on
    after the read could cover it. Without an owner it is recorded (as skipped) only when somebody in the
    organization expects external history."""
    if owner is None:
        start = w.org_start.get(org)
        return Admit.RECORD if start is not None and occurred_at >= start else Admit.DROP
    start = w.since.get(owner)
    if start is None:
        return Admit.DROP if occurred_at <= w.read_at else Admit.RECORD
    return Admit.DROP if occurred_at < start else Admit.RECORD


def dedupe_key(f: outcomes.OutcomeFact) -> str:
    """One physical event once in a project. Instantly sends one step to one lead once, so a second ``sent`` of a known
    step is a webhook retried with a new time (#930); a send with no step is told apart by its id only."""
    if f.kind == 'sent' and f.step is not None:
        return f'instantly:sent:{f.campaign_id}:{f.email}:{f.step}'
    if f.kind == 'unsubscribed':
        return f'instantly:optout:{f.campaign_id}:{f.email}'
    return f'instantly:{f.id}'


# The kinds and natures are compared as their values: the vocabulary is #930's, and a reader sees only ``outcomes``.
_KINDS = {'sent': Kind.OUTGOING_EMAIL, 'unsubscribed': Kind.OPT_OUT}


def kind_of(f: outcomes.OutcomeFact) -> Kind | None:
    """``None`` for what is not logged: a bounce, a label, and an autoresponder. A reply is classed as the native stop
    classes it (#931): an opt-out reply is the email the person wrote; the opt-out note is Instantly's unsubscribe."""
    if f.kind == 'reply':
        return Kind.INCOMING_EMAIL if f.reply_class in ('human', 'opt_out') else None
    return _KINDS.get(f.kind)


def to_fact(f: outcomes.OutcomeFact, org: str, project_id: str, names: dict, cells: dict) -> TimelineFact | None:
    kind = kind_of(f)
    if kind is None:
        return None
    key = dedupe_key(f)
    message_id = normalise_message_id(f.message_id)
    if kind is Kind.INCOMING_EMAIL and message_id:
        key = f'msg:{message_id}'
    return TimelineFact(
        source=SOURCE,
        source_id=f.id,
        organization_id=org,
        project_id=project_id,
        owner_id=f.enrolled_by,
        kind=kind,
        email=f.email,
        occurred_at=f.occurred_at,
        dedupe_key=key,
        message_id=message_id,
        person=Person(
            first_name=cells.get('first_name'),
            last_name=cells.get('last_name'),
            title=cells.get('title'),
            company_name=cells.get('company_name'),
        ),
        context=Context(
            provider=PROVIDER,
            campaign=f.campaign_name,
            project=names['project'],
            list=names['lists'].get(f.list_id) if f.list_id else None,
            step=f.step,
        ),
    )


def _names(conn, project_id: str, items: list[outcomes.OutcomeFact]) -> dict:
    project = conn.execute('SELECT name FROM projects WHERE id = %s', (project_id,)).fetchone()
    lists = {i.list_id for i in items if i.list_id}
    named = conn.execute('SELECT id::text, name FROM lists WHERE id = ANY(%s::uuid[])', (list(lists),)).fetchall()
    return {'project': project['name'] if project else '', 'lists': {r['id']: r['name'] for r in named}}


def _cells(conn, items: list[outcomes.OutcomeFact]) -> dict[str, dict]:
    rows = [i.row_id for i in items if i.row_id]
    found = conn.execute('SELECT id::text, cells FROM list_rows WHERE id = ANY(%s::uuid[])', (rows,)).fetchall()
    return {r['id']: r['cells'] for r in found}


def _fact(i: outcomes.OutcomeFact, org: str, project_id: str, names: dict, cells: dict) -> TimelineFact | None:
    """A fact the ledger refuses (an address that is not one) is left out with a log line: raising would roll the page
    back and stop the project's feed for good."""
    try:
        return to_fact(i, org, project_id, names, cells.get(i.row_id or '', {}))
    except ValueError:
        logger.warning('crm.timeline.fact_refused', project_id=project_id, source_id=i.id)
        return None


def ingest(project_id: str, org_id: str, w: Windows, *, max_pages: int = MAX_PAGES) -> int:
    """Read the project's feed from its cursor, a transaction per page, as ``ForOrg`` of its organization. Returns
    the rows recorded."""
    recorded = 0
    for _ in range(max_pages):
        with scopes.acting_for(org_id, 'crm timeline ingest'), get_conn() as conn:
            after = ledger.cursor(conn, project_id, org_id, SOURCE.value)
            page = outcomes.changes(conn, project_id, after=after, limit=PAGE)
            kept = [i for i in page.items if admit(i.occurred_at, i.enrolled_by, org_id, w) is Admit.RECORD]
            names, cells = _names(conn, project_id, kept), _cells(conn, kept)
            facts = [f for i in kept if (f := _fact(i, org_id, project_id, names, cells)) is not None]
            recorded += ledger.record(conn, facts)
            ledger.advance(conn, project_id, SOURCE.value, page.next_after)
        if len(page.items) < PAGE:
            break
    return recorded


def projects(w: Windows) -> list[tuple[str, str]]:
    """As System: ``(project, organization)`` of every Instantly connection in an organization where somebody expects
    external history. The other projects keep their cursor where it is."""
    if not w.org_start:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT DISTINCT project_id::text, organization_id::text FROM instantly_connections'
            ' WHERE organization_id = ANY(%s::uuid[]) ORDER BY 1',
            (list(w.org_start),),
        ).fetchall()
    return [(r['project_id'], r['organization_id']) for r in rows]
