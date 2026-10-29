"""Record Instantly facts and apply what each one means natively (#931), in the connection's organization only.

Each fact's effect is applied once: ``applied_effect`` keeps the effect applied, so a later fact of the person never
runs an earlier stop again, and a user's undo (a deleted exclusion, a new enrollment) holds. A fact whose class moves
(an autoresponder Instantly then calls human, a bounce a lead status makes hard) has a new effect, applied then.
An effect that fails rolls back alone, the fact stays recorded, and the reconcile pass tries it again, up to
``MAX_ATTEMPTS`` failures in a row. A new vote on the fact starts a new count (``store._adopt``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import psycopg
import structlog

from app.integrations import webhooks
from app.integrations.instantly_sync import outcomes, store
from app.integrations.instantly_sync.vocabulary import Act, Effect, Fact, Ignored
from app.outreach import coordination
from app.outreach.coordination import Person, Signal, SignalKind
from app.outreach.reply_targets import TARGET_COLUMNS

logger = structlog.get_logger()

MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class _Effect:
    kind: SignalKind
    stop_as: Literal['replied', 'excluded']
    exclude: bool
    stopped_reason: str


_EFFECTS: dict[Effect, _Effect] = {
    'auto_reply': _Effect(SignalKind.AUTO_REPLY, 'replied', False, ''),
    'human_reply': _Effect(SignalKind.HUMAN_REPLY, 'replied', False, 'replied'),
    'opt_out_reply': _Effect(SignalKind.OPT_OUT, 'replied', False, 'unsubscribed'),
    'unsubscribed': _Effect(SignalKind.OPT_OUT, 'excluded', True, 'unsubscribed'),
    'bounced': _Effect(SignalKind.HARD_BOUNCE, 'excluded', True, 'excluded'),
}


@dataclass(slots=True)
class Effects:
    recorded: store.Recorded
    stopped: list[tuple[list[dict], str]] = field(default_factory=list)

    def announce(self) -> None:
        for targets, reason in self.stopped:
            webhooks.sequence_stopped(targets, reason)


def record_and_apply(conn, binding: store.Binding, items: list[Fact | Ignored]) -> Effects:
    done = Effects(store.record(conn, binding, items))
    if done.recorded.changed:
        rows = conn.execute(
            'SELECT DISTINCT email FROM instantly_events WHERE id = ANY(%s::uuid[])', (done.recorded.changed,)
        ).fetchall()
        _apply_due(conn, binding, [r['email'] for r in rows], done)
    return done


def retry(conn, binding: store.Binding) -> Effects:
    """For the reconcile pass: the effects that failed, and the replies no side voted on once the key may not read
    emails (no vote will come, so they count as human)."""
    store.lock(conn, binding.connection_id)
    done = Effects(store.Recorded())
    rows = conn.execute(
        """SELECT DISTINCT email FROM instantly_events WHERE connection_id = %(connection)s
           AND ((effect_attempts > 0 AND effect_attempts < %(max)s)
                OR (%(refused)s AND reply_nature = 'unclassified' AND our_class IS NULL AND applied_effect IS NULL))""",
        {
            'connection': binding.connection_id,
            'max': MAX_ATTEMPTS,
            'refused': outcomes.emails_refused(conn, binding.project_id),
        },
    ).fetchall()
    if rows:
        _apply_due(conn, binding, [r['email'] for r in rows], done)
    return done


def _apply_due(conn, binding: store.Binding, emails: list[str], done: Effects) -> None:
    state = {
        r['id']: r
        for r in conn.execute(
            'SELECT id::text, applied_effect, effect_attempts FROM instantly_events'
            ' WHERE project_id = %s AND email = ANY(%s)',
            (binding.project_id, emails),
        )
    }
    due = [
        (act, _signal(conn, binding, Person(binding.project_id, email=email), act))
        for email, acts in outcomes.for_emails(conn, binding.project_id, emails).items()
        for act in acts
        if state[act.fact_id]['applied_effect'] != act.effect and state[act.fact_id]['effect_attempts'] < MAX_ATTEMPTS
    ]
    # Before the savepoints: an act that rolls back releases the locks taken inside it.
    coordination.lock(conn, [signal for _, signal in due])
    for act, signal in due:
        try:
            with conn.transaction():
                stopped = _apply(conn, act, signal)
        except psycopg.OperationalError:
            raise  # a lock or a lost connection: the delivery or the page is retried whole, so no stop is lost
        except Exception as exc:
            _failed(conn, binding, act, state[act.fact_id]['effect_attempts'] + 1, exc)
            continue
        if stopped:
            done.stopped.append((stopped, _EFFECTS[act.effect].stopped_reason))


def _signal(conn, binding: store.Binding, person: Person, act: Act) -> Signal:
    effect = _EFFECTS[act.effect]
    return Signal(
        effect.kind,
        person,
        f'instantly:{act.fact_id}',
        act.at,
        _wrote_to(conn, binding, person.email) if effect.exclude else (),
        stop_as=effect.stop_as,
        exclude=effect.exclude,
        source_lead_id=act.lead_id,
        wrote_in=[binding.project_id] if effect.exclude else (),
    )


def _apply(conn, act: Act, signal: Signal) -> list[dict]:
    stopped = coordination.apply(conn, signal).stopped
    conn.execute(
        'UPDATE instantly_events SET applied_effect = %s, effect_attempts = 0, effect_error = NULL WHERE id = %s',
        (act.effect, act.fact_id),
    )
    return stopped


def _failed(conn, binding: store.Binding, act: Act, attempts: int, exc: Exception) -> None:
    error = type(exc).__name__  # never the message: it may carry the address
    conn.execute(
        'UPDATE instantly_events SET effect_attempts = %s, effect_error = %s WHERE id = %s',
        (attempts, error, act.fact_id),
    )
    logger.error(
        'instantly.effects.gave_up' if attempts >= MAX_ATTEMPTS else 'instantly.effects.failed',
        connection_id=binding.connection_id,
        fact_id=act.fact_id,
        effect=act.effect,
        attempts=attempts,
        error_type=error,
    )


def _wrote_to(conn, binding: store.Binding, email: str | None) -> list:
    return conn.execute(
        f"""SELECT {TARGET_COLUMNS} FROM outreach_campaigns c JOIN outreach_campaign_targets t ON t.campaign_id = c.id
            WHERE c.organization_id = %s AND lower(btrim(t.email)) = %s""",
        (binding.organization_id, email),
    ).fetchall()
