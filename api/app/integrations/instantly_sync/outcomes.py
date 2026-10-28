"""What Instantly did with the people Zenbree enrolled (#930): the read side for #931, #932 and #942.

Policy is the reader's: this module stops, excludes, writes to a CRM and counts nothing. It hides the two sources,
their match, the reply classes and the lineage joins; a reader gets domain values and pages by ``changed``.

- :func:`for_emails`: the stops and autoresponder pauses that each person's facts call for. #931's check.
- :func:`changes`: every fact recorded or enriched after a cursor, once each by ``id``. #932 writes each id once;
  a reply shows only once it is classified, so an autoresponder never reaches a timeline as a reply.
- :func:`coverage`: how facts reach Zenbree and up to when they are complete, so a missing fact reads "not seen yet"
  and a count reads "unknown", not zero (#942).
- :func:`campaign_counts`: the people each Instantly campaign reached in a time range, for the campaign report (#942).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from app.admin.settings import is_disabled
from app.integrations.instantly_sync import counts
from app.integrations.instantly_sync.vocabulary import (
    MATCH_WINDOW,
    Act,
    EventKind,
    FactRow,
    Label,
    Mode,
    ReplyNature,
    fold,
    mode,
    reply_class,
)
from app.outreach.inbound import ReplyClass

# The key may not read emails: a refusal before the last connect was of the key replaced since, and the next pass
# asks the new one.
EMAILS_REFUSED = 's.emails_refused_at IS NOT NULL AND s.emails_refused_at > c.connected_at'


def emails_refused(conn, project_id: str) -> bool:
    row = conn.execute(
        f"""SELECT {EMAILS_REFUSED} AS refused FROM instantly_connections c
            JOIN instantly_sync_state s ON s.connection_id = c.id WHERE c.project_id = %s""",
        (project_id,),
    ).fetchone()
    return bool(row and row['refused'])


def for_emails(conn, project_id: str, emails: list[str]) -> dict[str, tuple[Act, ...]]:
    """Each address (lowercased) that has facts in the project, folded across its campaigns."""
    rows = conn.execute(
        """SELECT id::text, email, kind, occurred_at, reply_nature, our_class, hard_bounce, lead_id::text
           FROM instantly_events
           WHERE project_id = %s AND email = ANY(%s)""",
        (project_id, [e.strip().lower() for e in emails]),
    ).fetchall()
    by_email: dict[str, list[FactRow]] = defaultdict(list)
    for r in rows:
        by_email[r['email']].append(
            FactRow(
                id=r['id'],
                kind=EventKind(r['kind']),
                occurred_at=r['occurred_at'],
                reply_nature=ReplyNature(r['reply_nature']) if r['reply_nature'] else None,
                our_class=ReplyClass(r['our_class']) if r['our_class'] else None,
                hard_bounce=r['hard_bounce'],
                lead_id=r['lead_id'],
            )
        )
    refused = emails_refused(conn, project_id) if by_email else False
    return {email: fold(facts, emails_refused=refused) for email, facts in by_email.items()}


@dataclass(frozen=True, slots=True)
class OutcomeFact:
    id: str  # stable: the source id a consumer writes once
    kind: EventKind
    email: str
    campaign_id: str
    occurred_at: datetime
    reply_nature: ReplyNature | None
    label: Label | None
    step: int | None
    list_id: str | None  # the Zenbree list the person was sent from, when it still exists
    row_id: str | None
    campaign_name: str = ''  # as the enrollment named it
    enrolled_by: str | None = None  # the user who enrolled the person; None once that user is gone
    message_id: str | None = None  # the email's own Message-ID, as the provider gave it
    # A reply's class as the native stop reads it (``fold``): the safer vote, else human once the key may not read
    # emails. None for a reply nobody voted on, and for every other kind.
    reply_class: ReplyClass | None = None


def _reply_class(r: dict) -> ReplyClass | None:
    if r['kind'] != EventKind.REPLY:
        return None
    nature = ReplyNature(r['reply_nature']) if r['reply_nature'] else None
    voted = reply_class(nature, ReplyClass(r['our_class']) if r['our_class'] else None)
    return voted or (ReplyClass.HUMAN if r['refused'] else None)


@dataclass(frozen=True, slots=True)
class Page:
    items: list[OutcomeFact]
    next_after: int


# A reply whose safer vote (``reply_class``) is an autoresponder's, in SQL over the alias given to ``format``. A vote
# only rises, so a row leaves this set and never enters it.
_AUTO = (
    "(({0}.reply_nature IS NOT DISTINCT FROM 'auto' OR {0}.our_class IS NOT DISTINCT FROM 'auto')"
    " AND {0}.reply_nature IS DISTINCT FROM 'human' AND {0}.our_class IS DISTINCT FROM 'human'"
    " AND {0}.our_class IS DISTINCT FROM 'opt_out')"
)


def changes(conn, project_id: str, *, after: int, limit: int = 200) -> Page:
    """The facts recorded or changed after ``after``, once each is settled: a send or a reply once the API listed it
    (a webhook retried with a new time is a second row the API never lists, and only the listing brings a reply's
    Message-ID and Instantly's vote). Settling moves ``changed``, so the fact comes back on a later page. A key that
    may not read emails settles nothing: its webhook sends come unconfirmed (a retried delivery with a new time may
    then come twice), and its replies come one for each mail: the query below takes a copy within ``MATCH_WINDOW`` of
    a listed reply or of one received before it as the same mail, unless that one is an autoresponder, which the feed
    never logs.

    Any change to what this shows must move ``changed``, because a consumer pages on it: a fact that appears with no
    new ``changed`` is lost to every consumer already past it (see ``reconcile._save_refusal``)."""
    rows = conn.execute(
        f"""SELECT * FROM (
             SELECT e.id::text, e.changed, e.kind, e.email, e.campaign_id, e.occurred_at, e.reply_nature, e.label,
                    e.step, e.our_class, e.provider_ref, e.lead_id, e.received_at, b.list_id::text, l.row_id::text,
                    b.campaign_name, b.created_by::text AS enrolled_by, e.message_id,
                    EXISTS (SELECT 1 FROM instantly_sync_state s
                            WHERE s.connection_id = e.connection_id AND s.emails_refused_at IS NOT NULL) AS refused
             FROM instantly_events e
             JOIN instantly_leads l ON l.id = e.lead_id
             JOIN instantly_enrollments b ON b.id = l.enrollment_id
             WHERE e.project_id = %s AND e.changed > %s) f
           WHERE f.kind NOT IN ('sent', 'reply') OR f.provider_ref IS NOT NULL
              OR (f.refused AND (f.kind = 'sent' OR NOT EXISTS (
                  SELECT 1 FROM instantly_events o
                  WHERE o.lead_id = f.lead_id AND o.kind = 'reply' AND o.id::text <> f.id
                    AND o.occurred_at BETWEEN f.occurred_at - %s AND f.occurred_at + %s
                    AND (o.provider_ref IS NOT NULL OR (o.received_at, o.id::text) < (f.received_at, f.id))
                    AND NOT {_AUTO.format('o')})))
           ORDER BY f.changed LIMIT %s""",
        (project_id, after, MATCH_WINDOW, MATCH_WINDOW, limit),
    ).fetchall()
    items = [
        OutcomeFact(
            id=r['id'],
            kind=EventKind(r['kind']),
            email=r['email'],
            campaign_id=r['campaign_id'],
            occurred_at=r['occurred_at'],
            reply_nature=ReplyNature(r['reply_nature']) if r['reply_nature'] else None,
            label=Label(r['label']) if r['label'] else None,
            step=r['step'],
            list_id=r['list_id'],
            row_id=r['row_id'],
            campaign_name=r['campaign_name'],
            enrolled_by=r['enrolled_by'],
            message_id=r['message_id'],
            reply_class=_reply_class(r),
        )
        for r in rows
    ]
    return Page(items=items, next_after=rows[-1]['changed'] if rows else after)


@dataclass(frozen=True, slots=True)
class Coverage:
    mode: Mode
    webhook: str | None  # the current subscription's status, None when there is none
    webhook_refusal: str | None  # why there is no webhook: plan, scope, refused, no_public_url
    emails_refused: bool
    # Every send, reply, bounce, unsubscribe and label up to this instant is recorded; None before a first full pass.
    complete_until: datetime | None
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error_code: str | None
    failing_since: datetime | None


def coverage(conn, project_id: str) -> Coverage | None:
    """None when the project never connected Instantly."""
    row = conn.execute(
        f"""SELECT c.status, s.webhook_refusal, {EMAILS_REFUSED} AS emails_refused, s.sent_watermark, s.received_watermark, s.leads_round_done_at,
                  s.last_success_at, s.last_attempt_at, s.last_error_code, s.failing_since,
                  (SELECT w.status FROM instantly_webhook_subscriptions w
                    WHERE w.connection_id = c.id AND w.status <> 'retired') AS webhook
           FROM instantly_connections c LEFT JOIN instantly_sync_state s ON s.connection_id = c.id
           WHERE c.project_id = %s""",
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    marks = [row['sent_watermark'], row['received_watermark'], row['leads_round_done_at']]
    return Coverage(
        mode=mode(
            connected=row['status'] == 'connected',
            disabled=is_disabled('vendor_disabled:instantly'),
            webhook=row['webhook'],
        ),
        webhook=row['webhook'],
        webhook_refusal=row['webhook_refusal'],
        emails_refused=bool(row['emails_refused']),
        complete_until=None if None in marks else min(m for m in marks if m is not None),
        last_success_at=row['last_success_at'],
        last_attempt_at=row['last_attempt_at'],
        last_error_code=row['last_error_code'],
        failing_since=row['failing_since'],
    )


def campaign_counts(conn, project_id: str, start: datetime, end: datetime) -> list[dict]:
    """``counts.campaigns`` with the key's email access, which decides whether an unvoted reply is human."""
    return counts.campaigns(conn, project_id, start, end, emails_refused=emails_refused(conn, project_id))
