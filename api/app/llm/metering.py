"""LLM per-call token + cost telemetry.

Every `ResilientProvider.chat` / `chat_stream` call writes one row to the
`llm_usage` Postgres table via this module. Writes are fire-and-forget —
the LLM hot path never blocks on telemetry, and a failed insert MUST NOT
fail the caller.

Callers (agent loop, reward judge, plan_deck, scrape tasks) identify
themselves by setting a `MeteringContext` on the contextvar before invoking
the provider. Without a context the row is still written with
`endpoint='unknown'` so orphaned calls surface in the dashboard.

This module is the prerequisite for prompt caching (#16), Haiku routing
(#18), core.md trim (#9), and any other optimisation that claims a cost
delta — without a baseline every number is vibes-driven.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass

import structlog

from app.core import scope as scopes
from app.llm.base import TokenUsage

logger = structlog.get_logger()


# ── Caller context ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MeteringContext:
    """Per-request metadata the recorder attaches to each usage row.

    Set from the caller (agent_loop, reward_judge, plan_deck, ...) via
    `set_metering_context()` before invoking `ResilientProvider.chat*`.
    Cleared/overwritten at the next set; the ContextVar is request-scoped
    under asyncio so concurrent sessions don't cross-contaminate.

    Doc-upload buckets (#tonight): four counters surface document-related
    token spend separately from baseline chat tokens. They are populated
    by the caller (chat handler) before invoking the LLM and copied into
    each usage row so dashboards can compare doc-bearing turns to plain
    turns without re-deriving the split from message bodies.

    * ``doc_extraction_tokens`` — tokens spent by the L3 extraction
      adapter that turned the raw upload into plain text. Independent
      from the chat call; provided here so a single dashboard query
      can report total per-session doc cost.
    * ``doc_context_tokens`` — tokens of wrapped document text
      embedded in this turn's user message (post-truncation, after
      ``wrap_attachment``). Used to back-fill the prefilter heuristic
      with real numbers and to debug "model ignored the doc" reports.
    * ``cache_read_tokens`` / ``cache_write_tokens`` — caller-side
      hints about cache effects. Provider-side actuals already land
      on ``TokenUsage``; these are the *expected* numbers from the
      caller's POV. Diverging values across the two sides surface
      cache-key drift bugs without needing a separate trace.
    """

    user_id: str | None = None
    session_id: str | None = None
    # Free-text caller label: '/api/chat', 'reward_judge', 'plan_deck',
    # 'scrape_task', etc. Column is free-text in the schema so new callers
    # don't need a migration.
    endpoint: str = 'unknown'
    turn: int | None = None
    # User's subscription tier ('free' / 'starter' / 'pro' / 'business').
    # Unused since the spend port was dropped; kept so callers copied from the earlier implementation
    # still construct the context unchanged.
    subscription_tier: str | None = None
    doc_extraction_tokens: int = 0
    doc_context_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


# ContextVar defaults must be `None` (ruff B039) — any sentinel instance is
# a mutable default hazard even when the class is frozen. `get_metering_context()`
# substitutes a fresh `MeteringContext()` when nothing has been set yet.
_current_metering_context: ContextVar[MeteringContext | None] = ContextVar(
    '_current_metering_context',
    default=None,
)


def set_metering_context(ctx: MeteringContext) -> None:
    _current_metering_context.set(ctx)


def get_metering_context() -> MeteringContext:
    return _current_metering_context.get() or MeteringContext()


# ── Recording ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UsageRecord:
    provider: str
    model: str
    usage: TokenUsage
    cost_usd: float
    latency_ms: int
    success: bool = True
    error_class: str | None = None
    # When this row is the winning attempt of a resilient call that failed over
    # to reach it, the trigger cause of the failover (``rate_limited``,
    # ``content_filter``, ``http_<code>``, ``timeout``, ``error``). None on the
    # primary provider (no failover) and on failure rows. Lets residual spend on
    # a fallback provider (e.g. Anthropic) be decomposed by cause (#1294).
    failover_cause: str | None = None
    # True when the org cost cap already reserved this call's budget pre-call
    # and will settle it on the request path, so the org counter must not be
    # incremented a second time from here (#817).
    org_reserved: bool = False


def _to_row(rec: UsageRecord, ctx: MeteringContext) -> dict:
    # Column names match the pre-existing `llm_usage` schema
    # (migrations 004 + 014): `turn_number`, `cost_usd` NUMERIC(10,6),
    # `duration_ms`, `cache_read_tokens`, `cache_creation_tokens`.
    # Migration 031 adds `endpoint`, `success`, `error_class` on top.
    # Cost rounded to 6 decimals to fit NUMERIC(10,6) precision (sub-
    # microdollar granularity — finer than any real per-call cost).
    #
    # Provider-reported usage is the source of truth for the four
    # token columns. Caller-side ``ctx.cache_read_tokens`` /
    # ``cache_write_tokens`` are advisory and surfaced as "expected"
    # values in the structlog event below — not in the row, to avoid
    # a migration. Doc-upload counters (extraction + context) are
    # logged-only for the same reason: dashboards consume them via
    # the structlog→Loki pipeline until #297-PR-D adds the columns.
    return {
        'user_id': ctx.user_id,
        'session_id': ctx.session_id,
        'endpoint': ctx.endpoint,
        'turn_number': ctx.turn,
        'provider': rec.provider,
        'model': rec.model,
        'input_tokens': rec.usage.input_tokens,
        'output_tokens': rec.usage.output_tokens,
        'cache_creation_tokens': rec.usage.cache_creation_tokens,
        'cache_read_tokens': rec.usage.cache_read_tokens,
        'cost_usd': round(max(0.0, rec.cost_usd), 6),
        'duration_ms': rec.latency_ms,
        'success': rec.success,
        'error_class': rec.error_class,
        'failover_cause': rec.failover_cause,
    }


#: Writes run on their own pool, never on the caller's loop: a task on a nested
#: ``asyncio.run`` is cancelled at teardown and the row is lost silently (the earlier implementation
#: API#895). Pool threads are non-daemon, so a sync caller still gets its row.
_write_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='llm-metering-write')

_INSERT_SQL = (
    'INSERT INTO llm_usage (user_id, session_id, endpoint, turn_number, provider, model,'
    ' input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, cost_usd,'
    ' duration_ms, success, error_class, failover_cause)'
    ' VALUES (%(user_id)s, %(session_id)s, %(endpoint)s, %(turn_number)s, %(provider)s,'
    ' %(model)s, %(input_tokens)s, %(output_tokens)s, %(cache_creation_tokens)s,'
    ' %(cache_read_tokens)s, %(cost_usd)s, %(duration_ms)s, %(success)s, %(error_class)s,'
    ' %(failover_cause)s)'
)


def _fire_record_usage(rec: UsageRecord) -> None:
    """Fire-and-forget insert into ``llm_usage``. Never raises into caller.

    the earlier implementation also bumped a Redis spend counter and OTEL metrics here; both are gone
    (no spend port, no metrics) — the row and the log line are the record.
    """
    ctx = get_metering_context()
    # Skip user-less background calls: llm_usage.user_id is NOT NULL. #895
    if not ctx.user_id:
        logger.debug('llm_metering.skipped_no_user', provider=rec.provider, model=rec.model)
        return
    row = _to_row(rec, ctx)

    def _write() -> None:
        try:
            from app.billing.cost_scope import cost_scope
            from app.core.db import get_conn

            with cost_scope(), get_conn() as conn:
                conn.execute(_INSERT_SQL, row)
        # BaseException: a scope error (ScopeMissing, ScopeChangeWhileOpen) derives from it, and nobody reads this
        # pool's futures, so one let through would lose the row with no line in the log.
        except BaseException as e:
            logger.error(
                'llm_metering.write_failed',
                provider=rec.provider,
                model=rec.model,
                error_type=type(e).__name__,
                error=str(e),
            )

    try:
        # The caller's scope is the one the row is written in (``cost_scope``), not the pool thread's empty one.
        _write_pool.submit(scopes.for_another_thread().run, _write)
    except RuntimeError as e:
        # The pool refuses new work only once the process is shutting down: the row is lost.
        logger.error('llm_metering.write_not_scheduled', error=str(e))


def record_usage(
    *,
    provider: str,
    model: str,
    usage: TokenUsage | None,
    cost_usd: float,
    latency_ms: int,
    success: bool = True,
    error_class: str | None = None,
    # Set by the resilient router when this winning row was reached via a
    # failover; the trigger cause. None on the primary provider (#1294).
    failover_cause: str | None = None,
    # Set by the resilient router, which reserves org budget before the call
    # and settles it after (#817). Other LLM callers leave it False and are
    # metered against the org counter from here.
    org_reserved: bool = False,
    # Doc-upload buckets (#tonight). Kwargs-only with defaults so every
    # existing caller (`ResilientProvider`, evals, scrape paths) keeps
    # working without changes. Values are echoed into the ``llm_usage``
    # structlog event so Grafana/Loki can pivot on them ahead of the
    # column-add migration.
    doc_extraction_tokens: int = 0,
    doc_context_tokens: int = 0,
    cache_read_tokens_expected: int = 0,
    cache_write_tokens_expected: int = 0,
) -> None:
    """Public entry point called by `ResilientProvider` after every chat.

    The four doc/cache kwargs are advisory — they ride alongside the
    Supabase row insert as a structlog event for now (no migration on
    the doc-upload PR). Callers that want a single point-of-truth
    value should set them on ``MeteringContext`` so they're surfaced
    consistently across both the row and the log.
    """
    # Merge per-call advisory values with anything the contextvar
    # already carries — caller wins for kwargs that are non-zero.
    ctx = get_metering_context()
    merged = {
        'doc_extraction_tokens': doc_extraction_tokens or ctx.doc_extraction_tokens,
        'doc_context_tokens': doc_context_tokens or ctx.doc_context_tokens,
        'cache_read_tokens_expected': cache_read_tokens_expected or ctx.cache_read_tokens,
        'cache_write_tokens_expected': cache_write_tokens_expected or ctx.cache_write_tokens,
    }
    if any(v for v in merged.values()):
        logger.info(
            'llm_usage.advisory',
            provider=provider,
            model=model,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            turn=ctx.turn,
            **merged,
        )

    _fire_record_usage(
        UsageRecord(
            provider=provider,
            model=model,
            usage=usage or TokenUsage(),
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            success=success,
            error_class=error_class,
            failover_cause=failover_cause,
            org_reserved=org_reserved,
        )
    )
