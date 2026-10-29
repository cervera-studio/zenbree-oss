"""The one writer of ``instantly_events`` (#930): binds each fact to a lead Zenbree enrolled and records it once.

The webhook route and the beat both call :func:`record`, as ``ForOrg`` of the connection's organization. Both take
the connection's advisory lock first, so a delivery and an API page about the same lead run one after the other,
and the second finds the first's row. A fact is recorded only for a lead this project holds in Instantly
(``instantly_store.HELD``) with the same workspace, campaign and email: another project's lead in a shared workspace,
or a person added in Instantly by hand, is counted as ignored and stored nowhere.

How two sources make one fact: a webhook ``sent`` or ``reply`` has no id, an API email has one. A fact of one source
takes the other source's row of the same lead and kind within ``MATCH_WINDOW`` (the nearest, same step when both
know it) and adds its id to it; the row's ``id`` never changes, so a consumer that wrote it once never writes it again.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from app.integrations.instantly_store import HELD
from app.integrations.instantly_sync.vocabulary import (
    MATCH_WINDOW,
    EventKind,
    Fact,
    Ignored,
    ReplyNature,
    Source,
)
from app.outreach.inbound import ReplyClass, safer

_RANK = {'observed': 0, 'record': 1, 'event': 2}


@dataclass(frozen=True, slots=True)
class Binding:
    """The connection a callback or a beat pass records for. Built from a stored row, never from a payload."""

    organization_id: str
    project_id: str
    connection_id: str
    workspace_id: str


@dataclass(slots=True)
class Recorded:
    changed: list[str] = field(default_factory=list)  # ids of the facts inserted or enriched
    duplicate: int = 0
    ignored: Counter[str] = field(default_factory=Counter)


def record(conn, binding: Binding, items: list[Fact | Ignored]) -> Recorded:
    """Idempotent: the same items a second time change nothing. The caller holds ``acting_for`` the binding's
    organization and commits right after; never across a call to Instantly."""
    done = Recorded()
    lock(conn, binding.connection_id)
    for item in items:
        if isinstance(item, Ignored):
            done.ignored[item.reason] += 1
            continue
        if item.workspace_id and item.workspace_id != binding.workspace_id:
            done.ignored['workspace'] += 1
            continue
        lead = _lead(conn, binding, item)
        if lead is None:
            done.ignored['unknown_lead'] += 1
            continue
        changed = _apply(conn, binding, lead, item)
        if changed:
            done.changed.append(changed)
        else:
            done.duplicate += 1
    if done.ignored:
        _count_ignored(conn, binding.connection_id, done.ignored)
    return done


def lock(conn, connection_id: str) -> None:
    """The connection's facts, until the transaction ends: whoever records or applies them holds it."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('instantly_events:' || %s, 0))", (connection_id,))


def _count_ignored(conn, connection_id: str, ignored: Counter[str]) -> None:
    row = conn.execute('SELECT ignored FROM instantly_sync_state WHERE connection_id = %s', (connection_id,)).fetchone()
    if row is not None:
        total = Counter(row['ignored']) + ignored
        conn.execute(
            'UPDATE instantly_sync_state SET ignored = %s WHERE connection_id = %s', (Jsonb(dict(total)), connection_id)
        )


def _lead(conn, binding: Binding, fact: Fact) -> str | None:
    row = conn.execute(
        """SELECT id::text FROM instantly_leads
           WHERE project_id = %s AND workspace_id = %s AND campaign_id = %s AND email = %s AND outcome = ANY(%s)
           ORDER BY created_at DESC LIMIT 1""",
        (binding.project_id, binding.workspace_id, fact.campaign_id, fact.email, list(HELD)),
    ).fetchone()
    return row['id'] if row else None


def _apply(conn, binding: Binding, lead: str, fact: Fact) -> str | None:
    if fact.kind in (EventKind.BOUNCED, EventKind.UNSUBSCRIBED):
        return _insert(conn, binding, lead, fact) or _sharpen(conn, binding, fact)
    if fact.kind is EventKind.INTEREST:
        latest = conn.execute(
            """SELECT label FROM instantly_events WHERE lead_id = %s AND kind = 'interest'
               ORDER BY occurred_at DESC, changed DESC LIMIT 1""",
            (lead,),
        ).fetchone()
        # The webhook and the lead's label tell one change twice: a label is new only when it differs from the last.
        return None if latest and latest['label'] == fact.label else _insert(conn, binding, lead, fact)
    if fact.source is Source.WEBHOOK:
        if _exists(conn, binding, 'push_key', fact.key):
            return None
        return _adopt(conn, lead, fact, own='push_key', other='provider_ref') or _insert(conn, binding, lead, fact)
    if fact.provider_ref and _exists(conn, binding, 'provider_ref', fact.provider_ref):
        return None
    return _adopt(conn, lead, fact, own='provider_ref', other='push_key') or _insert(conn, binding, lead, fact)


def _exists(conn, binding: Binding, column: str, value: str) -> bool:
    query = 'SELECT 1 FROM instantly_events WHERE connection_id = %s AND ' + column + ' = %s'
    return conn.execute(query, (binding.connection_id, value)).fetchone() is not None


def _insert(conn, binding: Binding, lead: str, fact: Fact) -> str | None:
    row = conn.execute(
        """INSERT INTO instantly_events
               (organization_id, project_id, connection_id, lead_id, workspace_id, campaign_id, email, kind,
                reply_nature, label, step, fact_key, push_key, provider_ref, message_id, occurred_at, time_basis,
                our_class, hard_bounce)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (connection_id, fact_key) DO NOTHING RETURNING id::text""",
        (
            binding.organization_id,
            binding.project_id,
            binding.connection_id,
            lead,
            binding.workspace_id,
            fact.campaign_id,
            fact.email,
            fact.kind,
            fact.reply_nature,
            fact.label,
            fact.step,
            fact.key,
            fact.key if fact.source is Source.WEBHOOK and fact.kind in (EventKind.SENT, EventKind.REPLY) else None,
            fact.provider_ref,
            fact.message_id,
            fact.occurred_at,
            fact.time_basis,
            fact.our_class,
            fact.hard_bounce,
        ),
    ).fetchone()
    return row['id'] if row else None


_MORE_EXACT = "array_position(ARRAY['observed', 'record', 'event'], time_basis) - 1 < %(rank)s"


def _sharpen(conn, binding: Binding, fact: Fact) -> str | None:
    """A bounce or an unsubscribe already on record gains a more exact time (a lead's status first, the webhook
    later): the time a consumer shows is the event's."""
    row = conn.execute(
        f"""UPDATE instantly_events
               SET occurred_at = CASE WHEN {_MORE_EXACT} THEN %(at)s ELSE occurred_at END,
                   time_basis = CASE WHEN {_MORE_EXACT} THEN %(basis)s ELSE time_basis END,
                   hard_bounce = hard_bounce OR %(hard)s,
                   changed = nextval('instantly_events_changed_seq')
            WHERE connection_id = %(connection)s AND fact_key = %(key)s
              AND ({_MORE_EXACT} OR (%(hard)s AND NOT hard_bounce))
            RETURNING id::text""",
        {
            'rank': _RANK[fact.time_basis],
            'at': fact.occurred_at,
            'basis': fact.time_basis,
            'hard': fact.hard_bounce,
            'connection': binding.connection_id,
            'key': fact.key,
        },
    ).fetchone()
    return row['id'] if row else None


def _adopt(conn, lead: str, fact: Fact, *, own: str, other: str) -> str | None:
    """Add ``fact``'s id to the other source's row of the same message, if there is one."""
    found = conn.execute(
        f"""SELECT id::text, time_basis, reply_nature, our_class FROM instantly_events
            WHERE lead_id = %(lead)s AND kind = %(kind)s AND {own} IS NULL AND {other} IS NOT NULL
              AND occurred_at BETWEEN %(at)s - %(window)s AND %(at)s + %(window)s
              AND (step IS NULL OR %(step)s::int IS NULL OR step = %(step)s)
            ORDER BY abs(extract(epoch FROM occurred_at - %(at)s)), id LIMIT 1""",
        {'lead': lead, 'kind': fact.kind, 'at': fact.occurred_at, 'window': MATCH_WINDOW, 'step': fact.step},
    ).fetchone()
    if found is None:
        return None
    exact = _RANK[fact.time_basis] > _RANK[found['time_basis']]
    nature = found['reply_nature']
    if nature == ReplyNature.UNCLASSIFIED and fact.reply_nature in (ReplyNature.HUMAN, ReplyNature.AUTO):
        nature = fact.reply_nature
    ours = safer(ReplyClass(found['our_class']) if found['our_class'] else None, fact.our_class)
    # A new vote can give the fact a new effect (an autoresponder Instantly calls human), with attempts of its own
    # even after the old effect gave up (effects.MAX_ATTEMPTS).
    new_vote = (nature, ours) != (found['reply_nature'], found['our_class'])
    conn.execute(
        f"""UPDATE instantly_events SET {own} = %(key)s, message_id = coalesce(message_id, %(message)s),
                   step = coalesce(step, %(step)s), reply_nature = %(nature)s, our_class = %(ours)s,
                   occurred_at = CASE WHEN %(exact)s THEN %(at)s ELSE occurred_at END,
                   time_basis = CASE WHEN %(exact)s THEN %(basis)s ELSE time_basis END,
                   effect_attempts = CASE WHEN %(new_vote)s THEN 0 ELSE effect_attempts END,
                   effect_error = CASE WHEN %(new_vote)s THEN NULL ELSE effect_error END,
                   changed = nextval('instantly_events_changed_seq')
            WHERE id = %(id)s""",
        {
            'key': fact.key if own == 'push_key' else fact.provider_ref,
            'message': fact.message_id,
            'step': fact.step,
            'nature': nature,
            'ours': ours,
            'exact': exact,
            'new_vote': new_vote,
            'at': fact.occurred_at,
            'basis': fact.time_basis,
            'id': found['id'],
        },
    )
    return found['id']
