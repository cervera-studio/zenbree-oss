""":func:`apply` runs in the caller's transaction and scope (the filing organization's, or ``System(OPT_OUT)`` for
an opt-out), so the native stop, the exclusions and the queued Instantly stops commit together or not at all.

Idempotent: every write is guarded by a status or ON CONFLICT, and the autoresponder's pause ends relative to the
signal's own time, so a signal applied twice writes the same rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from app.audience.follow import alias_map
from app.exclusions.rules import normalise
from app.outreach import away, holds, open_sends, stop_outbox
from app.outreach.reply_targets import LINKEDIN_KEY, OWNER_PROJECTS, TARGET_COLUMNS


class SignalKind(StrEnum):
    HUMAN_REPLY = 'human_reply'
    AUTO_REPLY = 'auto_reply'
    OPT_OUT = 'opt_out'
    HARD_BOUNCE = 'hard_bounce'


@dataclass(frozen=True, slots=True)
class Person:
    every_owner_in: str | None
    email: str | None = None
    linkedin_url: str | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    kind: SignalKind
    person: Person
    cause: str
    at: datetime
    reach: Sequence[Mapping[str, Any]] = ()
    stop_as: Literal['replied', 'excluded'] = 'replied'
    exclude: bool = False
    owner_id: str | None = None
    fallback_projects: Sequence[str] = ()
    source_lead_id: str | None = None
    wrote_in: Sequence[str] = ()


@dataclass(slots=True)
class Applied:
    stopped: list[dict] = field(default_factory=list)
    paused: list[dict] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    stops_requested: int = 0


_SEND_CODE = {'replied': 'REPLIED', 'excluded': 'UNSUBSCRIBED'}
_REASON: dict[SignalKind, stop_outbox.StopReason] = {
    SignalKind.HUMAN_REPLY: 'human_reply',
    SignalKind.OPT_OUT: 'opt_out',
    SignalKind.HARD_BOUNCE: 'hard_bounce',
}
_EXCLUDES = (SignalKind.OPT_OUT, SignalKind.HARD_BOUNCE)


def lock(conn, signals: Sequence[Signal]) -> None:
    """For a transaction that applies several signals one after the other: every row they write, locked before the
    first one in the order of a single signal (the projects, then the people in id order). One signal at a time would
    lock the people in the order of the signals, against a reject of several people in id order."""
    resolved = [(s, _projects(conn, s)[0]) for s in signals]
    holds.lock_out_enrollments(conn, [p for _, ps in resolved for p in ps], wait=True)
    _lock_in_id_order(conn, [i for s, ps in resolved for i in _targets(conn, s, ps)[1]])


def apply(conn, signal: Signal) -> Applied:
    projects, excluded_in = _projects(conn, signal)
    holds.lock_out_enrollments(conn, projects, wait=True)
    people, ids = _targets(conn, signal, projects)
    if signal.kind is SignalKind.AUTO_REPLY:
        return Applied(paused=_pause(conn, ids, until=away.unknown_return_pause_end(signal.at)))
    done = Applied(stopped=_stop(conn, ids, 'replied' if signal.kind is SignalKind.HUMAN_REPLY else signal.stop_as))
    if _excludes(signal):
        done.excluded = _exclude(conn, signal, signal.reach, excluded_in)
    for p in projects:
        done.stops_requested += stop_outbox.request(
            conn,
            project_id=p,
            emails=sorted(people[p][0]),
            reason=_REASON[signal.kind],
            cause=signal.cause,
            source_lead_id=signal.source_lead_id,
        )
    return done


def _excludes(signal: Signal) -> bool:
    return signal.kind in _EXCLUDES and signal.exclude


def _projects(conn, signal: Signal) -> tuple[list[str], set[str]]:
    """Every project the signal writes in, and those it excludes the person in."""
    excluded_in = _exclusion_projects(conn, signal, signal.reach) if _excludes(signal) else set()
    follow = signal.person.every_owner_in
    reach = {str(t['project_id']) for t in signal.reach}
    return sorted(reach | excluded_in | ({follow} if follow else set())), excluded_in


def _targets(conn, signal: Signal, projects: list[str]) -> tuple[dict[str, tuple[set, set]], list]:
    """The person's keys in each project, and the rows the signal stops or pauses: read after the project locks."""
    reach = signal.reach
    people = {p: _aliases(conn, p, signal.person, [t for t in reach if str(t['project_id']) == p]) for p in projects}
    targets = {str(t['id']): t for t in reach}
    if follow := signal.person.every_owner_in:
        targets |= {str(t['id']): t for t in _person_targets(conn, follow, *people[follow])}
    return people, [t['id'] for t in targets.values()]


def _aliases(conn, project_id: str, person: Person, reach: Sequence[Mapping[str, Any]]) -> tuple[set, set]:
    named = [('email', person.email), ('linkedin_url', person.linkedin_url)]
    named += [(kind, t[kind]) for t in reach for kind in ('email', 'linkedin_url')]
    emails = {v.strip().lower() for k, v in named if k == 'email' and v and v.strip()}
    links = {v.strip() for k, v in named if k == 'linkedin_url' and v and v.strip()}
    for kind, value in alias_map(conn, project_id, named):
        (emails if kind == 'email' else links).add(value)
    return emails, links


def _person_targets(conn, project_id: str, emails: set, links: set) -> list:
    # One arm per key, each with the partial predicate of its index (outreach_campaign_targets_email_key, _linkedin_key):
    # an OR of the two read every open target of the project while the project lock was held.
    # ponytail: under row-level security (a human reply) the planner uses no index on lower() or btrim(), which are not
    # leakproof, so each arm still reads every target of the organization. An index on stored key columns avoids that.
    linkedin = LINKEDIN_KEY.format('t.linkedin_url')
    return conn.execute(
        f"""SELECT {TARGET_COLUMNS} FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id
            WHERE c.project_id = %(project)s AND t.status IN ('active', 'draft', 'paused')
              AND coalesce(btrim(t.email), '') <> '' AND lower(btrim(t.email)) = ANY(%(emails)s)
            UNION
            SELECT {TARGET_COLUMNS} FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id
            WHERE c.project_id = %(project)s AND t.status IN ('active', 'draft', 'paused')
              AND btrim(t.linkedin_url) <> '' AND {linkedin} IN (
                  SELECT {LINKEDIN_KEY.format('u')} FROM unnest(%(links)s::text[]) u)""",
        {'project': project_id, 'emails': sorted(emails), 'links': sorted(links)},
    ).fetchall()


def _lock_in_id_order(conn, ids: list) -> None:
    # An UPDATE locks rows in plan order, a reject in id order (#1132, #1154).
    conn.execute(
        'SELECT 1 FROM outreach_campaign_targets WHERE id = ANY(%s::uuid[]) ORDER BY id FOR NO KEY UPDATE', (ids,)
    )


def _stop(conn, ids: list, status: Literal['replied', 'excluded']) -> list:
    if not ids:
        return []
    _lock_in_id_order(conn, ids)
    stopped = conn.execute(
        'UPDATE outreach_campaign_targets SET status = %(status)s, paused_until = NULL,'
        " replied_at = CASE WHEN %(status)s = 'replied' THEN now() ELSE replied_at END"
        " WHERE id = ANY(%(ids)s::uuid[]) AND status IN ('active', 'draft', 'paused')"
        ' RETURNING id, campaign_id, email, linkedin_url',
        {'status': status, 'ids': ids},
    ).fetchall()
    open_sends.skip(conn, ids, _SEND_CODE[status])
    return stopped


def _pause(conn, ids: list, *, until: datetime) -> list:
    if not ids:
        return []
    _lock_in_id_order(conn, ids)
    return conn.execute(
        "UPDATE outreach_campaign_targets SET status = 'paused', paused_until = greatest(paused_until, %(at)s)"
        " WHERE id = ANY(%(ids)s::uuid[]) AND status IN ('active', 'paused') RETURNING id",
        {'at': until, 'ids': ids},
    ).fetchall()


def _exclusion_projects(conn, signal: Signal, reach: Sequence[Mapping[str, Any]]) -> set[str]:
    """The project of every campaign that wrote to the person (#307: a client's opt-out is that client's); when none
    did, every project the owner runs campaigns in, or ``fallback_projects`` when the owner is gone (#333)."""
    wrote = {str(t['project_id']) for t in reach} | set(signal.wrote_in)
    if wrote:
        return wrote
    if signal.owner_id:
        return {str(r['project_id']) for r in conn.execute(OWNER_PROJECTS, (signal.owner_id,))}
    return set(signal.fallback_projects)


def _exclude(conn, signal: Signal, reach: Sequence[Mapping[str, Any]], projects: set[str]) -> list[tuple[str, str]]:
    keys = {('email', signal.person.email), ('linkedin_url', signal.person.linkedin_url)}
    keys |= {('email', t['email']) for t in reach} | {('linkedin_url', t['linkedin_url']) for t in reach}
    reply_opt_out = signal.stop_as == 'replied'
    origin = 'bounced' if signal.kind is SignalKind.HARD_BOUNCE else 'unsubscribed'
    added = []
    for project in sorted(projects):
        for kind, raw in sorted(keys, key=str):
            if raw and (value := normalise(kind, raw)):
                # An onboarding rule becomes the opt-out's, so a profile edit can't lift it; a claimed
                # row is not reported as added, so the undo never deletes it (ponytail: it keeps the opt-out's origin).
                row = conn.execute(
                    'INSERT INTO exclusions (project_id, user_id, kind, value, reply_opt_out_at, origin)'
                    ' VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END, %s)'
                    ' ON CONFLICT (project_id, kind, value) DO UPDATE SET source = NULL, origin = EXCLUDED.origin'
                    ' WHERE exclusions.source IS NOT NULL RETURNING id, xmax = 0 AS inserted',
                    (project, signal.owner_id, kind, value, reply_opt_out, origin),
                ).fetchone()
                if row and row['inserted']:
                    added.append((str(row['id']), project))
    return added
