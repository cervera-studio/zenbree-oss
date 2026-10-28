"""LLM Provider router — env-based selection + automatic failover.

Chain: DeepSeek → Anthropic (both via Cloudflare AI Gateway when configured).
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from app.llm.base import (
    CHARS_PER_TOKEN,
    CONSERVATIVE_CONTEXT_WINDOW,
    LLMProvider,
    LLMResponse,
    ProviderAPIError,
    ProviderRateLimitError,
    TokenUsage,
    declared_context_window,
)
from app.llm.json_output import JSONOutputError
from app.llm.metering import get_metering_context, record_usage
from app.llm.pricing import cost_usd, get_price

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from app.llm.base import LLMEvent

logger = structlog.get_logger()
_providers: dict[str, LLMProvider] = {}

# Provider __init__ failures (e.g. missing env vars) are cached so the
# factory doesn't pay constructor cost on every retry. Keyed identically
# to ``_providers`` with a ``__init_failed__:`` prefix.
_failed_inits: dict[str, BaseException] = {}

# Dedupe set for ``llm_provider_init_failed`` / ``llm_primary_init_failed``
# warnings. ``get_fallback_chain`` is called per-request and re-walks every
# factory; without dedup, a single misconfig (e.g. AZURE_FOUNDRY_ENDPOINT
# empty on staging) emits one warning per Azure-backed factory per request,
# turning a cold misconfig into per-request log spam. Keying by a
# ``(provider_class, error_message)`` signature drops duplicates while
# letting genuinely-new failure modes still light up the log.
_logged_init_failures: set[str] = set()


# ── Context-window prefilter ─────────────────────────────────────────────────
#
# Doc-upload (#tonight): a 200K-token PDF would silently fail on Claude
# Sonnet 4.6 (200K window) once you add 13K-17K of system prompt + tool
# schema + history; the request would 4xx mid-chain and the failover
# hop would re-bill the same prompt against the next provider. Pre-call
# we estimate ``system + history + tools + user_message`` and skip any
# provider whose declared window can't host it with a 10% output
# headroom. Skipping leaves the rest of the chain in order — short-
# context calls still see the full chain unchanged.
#
# The registry itself moved to ``app.llm.base`` : the
# history-replay window is a second consumer of the same number, and a private
# copy per consumer drifts the moment a provider is flipped to a longer-context
# variant.

# Output headroom: skip a provider if pre-call tokens exceed
# ``window * (1 - HEADROOM)``. 10% leaves room for assistant output
# AND any cache-creation accounting overhead Anthropic charges on the
# first turn of a session.
_CONTEXT_WINDOW_HEADROOM_RATIO = 0.1

# Token estimation: the repo's one yardstick, declared in ``base`` so the
# prompt-cache decision, this prefilter and the history budget all convert
# with the same number.
_CHARS_PER_TOKEN = CHARS_PER_TOKEN


def _get_provider_window(provider: LLMProvider) -> int | None:
    """Return the configured min context window for ``provider``.

    ``None`` for unregistered providers — no override on the chain is the
    safer default HERE than guessing low and skipping the only viable
    provider. That is why this reads ``declared_context_window`` and not
    ``provider.context_window()``, which answers with a conservative number
    instead of admitting it does not know (see ``base``).
    """
    return declared_context_window(provider)


def _estimate_tokens(value: object) -> int:
    """Coarse char/4 token estimator for prefilter purposes.

    Walks any nested combination of strings, lists, and dicts that the
    LLM payload can take (Anthropic block-list system prompt, tool
    schema dicts, message content blocks). Numbers and other scalars
    are stringified — they show up in tool input/output as JSON
    fragments and we want them counted at JSON-encoded length.

    This is deliberately *not* a tokenizer. Dragging tiktoken /
    anthropic.count_tokens onto the request hot path would add 5-30 ms
    of CPU per call for a guard rail that just decides which provider
    to *try first*. The one-shot bias is to over-count slightly, so a
    near-window prompt skips early rather than hitting a real 4xx.
    """
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value) // _CHARS_PER_TOKEN
    if isinstance(value, (int, float, bool)):
        return len(str(value)) // _CHARS_PER_TOKEN
    if isinstance(value, (list, tuple)):
        return sum(_estimate_tokens(item) for item in value)
    if isinstance(value, dict):
        # Approximate JSON encoding cost without paying the json.dumps
        # round trip when the dict is small. For anything non-trivial
        # fall back to dumps so nested tool-schema descriptions are
        # accounted for.
        try:
            return len(json.dumps(value, ensure_ascii=False, default=str)) // _CHARS_PER_TOKEN
        except TypeError, ValueError:
            return sum(_estimate_tokens(k) + _estimate_tokens(v) for k, v in value.items())
    # Unknown type — stringify defensively.
    return len(str(value)) // _CHARS_PER_TOKEN


def _estimate_call_tokens(
    *,
    system: object,
    messages: object,
    tools: object,
) -> int:
    """Estimate ``system + history + tools + user_message`` for one call.

    Inputs are the same shapes accepted by ``LLMProvider.chat`` /
    ``chat_stream``. Each is summed independently so the caller can
    later log per-component breakdowns if a session keeps tripping
    the prefilter. We don't single out the *latest* user message vs
    history because the provider sees them as one flat ``messages``
    list — what matters is the total input charge.
    """
    return _estimate_tokens(system) + _estimate_tokens(messages) + _estimate_tokens(tools)


def _filter_chain_by_context(
    chain: list[LLMProvider],
    *,
    estimated_tokens: int,
) -> list[LLMProvider]:
    """Return ``chain`` with providers whose window is too small dropped.

    A provider is kept if:
      * it has no registered window (unknown providers default open —
        we'd rather hit a real 4xx than starve the chain), OR
      * ``estimated_tokens <= window * (1 - HEADROOM)``.

    Existing failover behaviour for short-context calls is unchanged
    (every provider keeps its slot when the prompt fits everywhere).
    Skipped providers are logged once at warning so a session that
    silently routes around the primary surfaces in Grafana / BetterStack.
    """
    if estimated_tokens <= 0:
        return chain
    kept: list[LLMProvider] = []
    skipped: list[tuple[str, int]] = []
    for provider in chain:
        window = _get_provider_window(provider)
        if window is None:
            kept.append(provider)
            continue
        budget = int(window * (1 - _CONTEXT_WINDOW_HEADROOM_RATIO))
        if estimated_tokens <= budget:
            kept.append(provider)
        else:
            skipped.append((type(provider).__name__, window))

    # Never starve the chain — if every provider was skipped, fall back
    # to the original chain. Better to attempt a likely-failing call
    # and surface the provider-side 4xx (with its actual window data)
    # than to refuse the request on our heuristic alone.
    if not kept:
        logger.warning(
            'llm_router.context_window_skipped_all',
            estimated_tokens=estimated_tokens,
            skipped=[name for name, _ in skipped],
        )
        return chain

    if skipped:
        logger.info(
            'llm_router.context_window_skip',
            estimated_tokens=estimated_tokens,
            skipped=[{'provider': n, 'window': w} for n, w in skipped],
            kept=[type(p).__name__ for p in kept],
        )

    return kept


def _get_anthropic() -> LLMProvider:
    fail_key = '__init_failed__:anthropic'
    if fail_key in _failed_inits:
        raise _failed_inits[fail_key]
    if 'anthropic' not in _providers:
        from app.core.config import settings
        from app.llm.anthropic import AnthropicProvider

        try:
            # ponytail: the earlier implementation only used Anthropic as an explicit primary; here it
            # is the fallback, so a missing key drops it instead of 401-ing.
            if not settings.anthropic_api_key:
                raise RuntimeError('ANTHROPIC_API_KEY is not configured')
            _providers['anthropic'] = AnthropicProvider()
        except Exception as e:
            _failed_inits[fail_key] = e
            raise
    return _providers['anthropic']


def _get_deepseek_direct() -> LLMProvider:
    """DeepSeek via api.deepseek.com (or the CF gateway's ``/deepseek``).
    Init failures (missing DEEPSEEK_API_KEY) are cached so the chain
    fast-fails to the next provider without re-paying init per request."""
    fail_key = '__init_failed__:deepseek-direct'
    if fail_key in _failed_inits:
        raise _failed_inits[fail_key]
    if 'deepseek-direct' not in _providers:
        from app.llm.deepseek import DeepSeekProvider

        try:
            _providers['deepseek-direct'] = DeepSeekProvider()
        except Exception as e:
            _failed_inits[fail_key] = e
            raise
    return _providers['deepseek-direct']


# Ordered failover chain: DeepSeek (cheap, primary) → Anthropic (fallback).
_FALLBACK_CHAIN_FACTORIES = [
    _get_deepseek_direct,
    _get_anthropic,
]

# For LLM_PRIMARY_PROVIDER env override
_PRIMARY_FACTORIES = {
    'anthropic': _get_anthropic,
    'deepseek': _get_deepseek_direct,
    'deepseek-direct': _get_deepseek_direct,
}


def get_provider(*, primary: str | None = None) -> LLMProvider:
    """Provider for non-chat LLM calls (scraping, classification, enrichment).

    Reads ``LLM_PRIMARY_PROVIDER`` from settings (default ``deepseek``).
    Pass ``primary`` to override per-call.  Chat-path code should use
    ``get_chat_provider`` instead.
    """
    return ResilientProvider(get_fallback_chain(primary=primary))


def get_chat_provider() -> LLMProvider:
    """Provider for LLM calls on the chat path (gates, adapters, tool handlers).

    Reads ``CHAT_PRIMARY_PROVIDER`` so satellite calls use the same model
    family as the agent loop.
    """
    from app.core.config import settings

    return get_provider(primary=settings.chat_primary_provider)


def get_fallback_chain(primary: str | None = None) -> list[LLMProvider]:
    # Explicit ``primary=`` override wins over settings — enables per-endpoint
    # routing (``get_chat_provider`` passes ``chat_primary_provider``, so chat
    # and non-chat calls can run on different primaries) without a global
    # settings flip.
    if primary is None:
        from app.core.config import settings

        primary = settings.llm_primary_provider
    chain: list[LLMProvider] = []

    # Put the requested primary first
    primary_factory = _PRIMARY_FACTORIES.get(primary)
    if primary_factory:
        try:
            chain.append(primary_factory())
        except Exception as e:
            _warn_once('llm_primary_init_failed', e, provider=primary)

    # Add remaining providers from the default chain (skip duplicates)
    chain_ids = {id(p) for p in chain}
    for factory in _FALLBACK_CHAIN_FACTORIES:
        try:
            p = factory()
            if id(p) not in chain_ids:
                chain.append(p)
                chain_ids.add(id(p))
        except Exception as e:
            _warn_once('llm_provider_init_failed', e)

    return chain


def _warn_once(event: str, exc: BaseException, **extra: object) -> None:
    """Emit a warning frame the first time a given init failure is seen,
    silently skip thereafter. Dedup signature is ``event:type:message`` so
    distinct providers with distinct messages still log, but the same Azure
    misconfig hitting 4 factories per request only logs once per process.
    """
    sig = f'{event}:{type(exc).__name__}:{str(exc)[:200]}'
    if sig in _logged_init_failures:
        return
    _logged_init_failures.add(sig)
    logger.warning(event, error=str(exc), **extra)


def _estimate_call_cost_usd(chain: list[LLMProvider], *, estimated_tokens: int, max_tokens: int) -> float:
    """Upper bound on what one call can cost, in USD.

    Prices the estimated input plus a *full* ``max_tokens`` output on the
    dearest model that could serve the call, because failover can land on any
    provider in the chain. Deliberately conservative: this is what the org cost
    cap reserves before the call (#817), and an over-estimate is refunded on
    settle, whereas an under-estimate is real overshoot.
    """
    models = [getattr(p, '_default_model', '') or '' for p in chain] or ['']
    worst = 0.0
    for model in models:
        price = get_price(model)
        cost = (estimated_tokens * price.input + max_tokens * price.output) / 1_000_000
        worst = max(worst, cost)
    return worst


# ponytail: the earlier implementation's spend port (per-user/org USD caps) is dropped; credits are
# reserved per job in M1. Kept as no-op seams so the call flow stays verbatim.
@dataclass(frozen=True)
class _Reservation:
    reserved_usd: float


async def _enforce_spend_cap(*, estimated_cost_usd: float) -> _Reservation | None:
    return None


async def _settle_org_reservation(reservation: _Reservation | None, *, actual_cost_usd: float) -> None:
    return None


def _emit_llm_call(
    *,
    provider: str,
    model: str,
    usage: TokenUsage | None,
    cost_usd: float,
    latency_ms: int,
) -> None:
    """the earlier implementation published an ``llm.call`` event here; the event bus is gone, so it
    is a structlog line (BetterStack ingests it)."""
    ctx = get_metering_context()
    tokens = usage or TokenUsage()
    logger.info(
        'llm.call',
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        provider=provider,
        model=model,
        input_tokens=tokens.input_tokens,
        output_tokens=tokens.output_tokens,
        cache_read_tokens=tokens.cache_read_tokens,
        cache_creation_tokens=tokens.cache_creation_tokens,
        cost_usd=cost_usd,
        duration_ms=latency_ms,
    )


# Event types that, once yielded to the consumer, cannot be retracted: the
# consumer (``turn_stream``) acts on them — a ``tool_end`` is appended,
# announced as an SSE ``tool_start`` frame and later charged; server-tool
# events are announced and charged on the spot. ``tool_start`` and
# ``tool_delta`` are deliberately ABSENT: the consumer has no branch for
# either (its SSE ``tool_start`` frame is born from the provider's
# ``tool_end``, not from the provider event of the same name), so a provider
# that dies between ``tool_start`` and ``tool_end`` has left nothing on the
# wire and nothing in the consumer's state — failover is safe there. ``done``
# is also absent — it never precedes a provider failure. See
# ``_StreamAttempt.has_emitted`` (#2320).
_NON_RETRACTABLE_EVENT_TYPES = frozenset(
    {
        'tool_end',
        'server_tool_use',
        'web_search_result',
    }
)


class _StreamAttempt:
    """Books one ``chat_stream`` attempt: writes its usage row and carries the
    cost the org reservation settles against.

    ``chat_stream`` is an async generator, so a consumer that stops pulling
    (client disconnect, ``break``) leaves it suspended at the ``yield`` and the
    code after the ``async for`` never runs. Booking from there loses the call:
    no usage row, and a settle at ``0.0`` refunds the whole hold for a call the
    provider already billed, which makes the cap bypassable by hanging up. So
    the generator books from its ``finally`` instead, via :meth:`finish`, which
    both ``GeneratorExit`` and ``CancelledError`` reach (#817, #1208).
    """

    def __init__(
        self,
        *,
        estimated_input_tokens: int,
        org_reserved: bool,
        requested_model: str = '',
    ) -> None:
        self._estimated_input_tokens = estimated_input_tokens
        self._org_reserved = org_reserved
        self._requested_model = requested_model
        self._pending = False
        self._provider = ''
        self._model = ''
        self._start = 0.0
        self._usage: TokenUsage | None = None
        self._done_cost_usd = 0.0
        self._saw_done = False
        self._streamed_chars = 0
        self._emitted_events = 0
        self.cost_usd = 0.0
        # The cause that made the chain advance to the CURRENT provider, carried
        # onto the winning row so failover spend is decomposable (#1294). Set by
        # the loop on each failover; deliberately NOT reset by ``begin`` — it
        # describes why we left the previous provider, which outlives the reset.
        self.failover_cause: str | None = None

    def begin(self, provider: LLMProvider) -> None:
        """A provider is about to stream. Resets the per-attempt state.

        The model is what THIS provider will actually serve: the turn loop forces
        Haiku on the free tier via a ``model`` kwarg, but an OpenAI-compatible
        provider rewrites a ``claude-*`` kwarg to its own default. An abort never
        sees a ``done`` to correct a wrong guess, so ask the provider instead of
        trusting either side blindly."""
        self._pending = True
        self._provider = type(provider).__name__
        self._model = provider.effective_model(self._requested_model)
        self._start = time.monotonic()
        self._usage = None
        self._done_cost_usd = 0.0
        self._saw_done = False
        self._streamed_chars = 0
        self._emitted_events = 0

    def observe(self, event: LLMEvent) -> None:
        """Fold one streamed event into what we know this attempt has cost,
        and into whether it has put anything non-retractable on the wire."""
        if event.type == 'done':
            self._saw_done = True
            self._usage = event.usage
            self._done_cost_usd = event.cost_usd
            self._model = event.model or self._model
            return
        # Non-retractable = a completed tool-family event, or real text.
        # ``_streamed_chars`` alone cannot answer this — a ``tool_end`` can in
        # principle arrive with no delta text at all (#2320), and a tool block
        # on the wire is exactly what must not be re-emitted by a second
        # provider. The ``text_delta`` guard matters in the other direction:
        # ``tool_delta`` carries its input JSON in ``event.text``, and that
        # JSON is discarded by the consumer, so it must feed the pricing
        # counter below WITHOUT tripping the emitted flag.
        if event.type in _NON_RETRACTABLE_EVENT_TYPES or (event.type == 'text_delta' and event.text):
            self._emitted_events += 1
        if event.text:
            self._streamed_chars += len(event.text)

    @property
    def has_emitted(self) -> bool:
        """True once THIS attempt has produced at least one event that was
        delivered to the consumer and cannot be taken back (text on the SSE
        wire, a tool block the dispatcher will charge). ``observe`` runs
        before the ``yield``, but a failure can only be raised from a later
        ``__anext__`` — so by the time an except block reads this, every
        observed event has in fact been handed over.

        The boundary measured here is provider→router, and for text it is
        deliberately conservative: a delivered ``text_delta`` counts even
        when the consumer's deliberation gate (#1984) has not put it on the
        SSE wire yet, because the consumer accumulates it regardless
        (``full_text``, the gate's own buffer flushed later) and nothing
        resets that state on a router-internal failover — a "not on the wire
        yet" exemption would resurface that text duplicated."""
        return self._emitted_events > 0

    def _latency_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def _partial(self) -> tuple[TokenUsage, float]:
        """What the provider has run so far, priced as a FLOOR: the real usage
        split only arrives with ``done``, so an abort has to guess, and guessing
        high is the dangerous direction. The daily spend cap reads these rows, so
        an over-book throttles a legitimate user; an under-book only under-counts.

        Input is therefore booked at the cheapest rate the model could have
        billed those tokens at: ``cache_read`` where the model has one (our
        prompts carry ``cache_control``, so a warm prefix is the normal case),
        full ``input`` where it does not, which is the same rate a completed call
        books for them. A cold Anthropic prompt billed at ``cache_creation``
        under-books its input by up to 12.5x; that is the accepted side of the
        trade. Both token counts stay estimates: input is the pre-call estimate,
        output is what actually reached us.

        Consequence of pinning DeepSeek's cache-hit rate: ``deepseek-v4-flash``
        now HAS a ``cache_read`` (0.006 against an input rate of 0.14), so it
        crosses into the first branch. An aborted flash stream books ~23x lower
        than it did — squarely the under-book this docstring chooses on
        purpose, but a step change in a number somebody watches, so it is
        written down rather than discovered."""
        output_tokens = self._streamed_chars // _CHARS_PER_TOKEN
        price = get_price(self._model)
        if price.cache_read > 0:
            usage = TokenUsage(
                cache_read_tokens=self._estimated_input_tokens,
                output_tokens=output_tokens,
            )
        else:
            usage = TokenUsage(
                input_tokens=self._estimated_input_tokens,
                output_tokens=output_tokens,
            )
        return usage, cost_usd(self._model, usage)

    def complete(self) -> None:
        """The stream ran to exhaustion: book the provider's own numbers."""
        if not self._pending:
            return
        self._pending = False
        self.cost_usd = self._done_cost_usd
        record_usage(
            provider=self._provider,
            model=self._model,
            usage=self._usage,
            cost_usd=self._done_cost_usd,
            latency_ms=self._latency_ms(),
            success=True,
            failover_cause=self.failover_cause,
            org_reserved=self._org_reserved,
        )

    def fail(self, error_class: str) -> None:
        """The provider errored on a path that writes a usage row."""
        if not self._pending:
            return
        self._pending = False
        record_usage(
            provider=self._provider,
            model=self._model,
            usage=None,
            cost_usd=0.0,
            latency_ms=self._latency_ms(),
            success=False,
            error_class=error_class,
            org_reserved=self._org_reserved,
        )

    def fail_mid_stream(self, error_class: str) -> None:
        """The provider errored AFTER this attempt delivered events to the
        consumer (#2320). The chain will NOT advance — the error propagates —
        so unlike ``failover`` the attempt is not retried, and unlike ``fail``
        it is not a $0 non-event: the provider ran and billed the partial.
        Books the streamed partial on a failure row via the same floor
        pricing an abandoned attempt uses, and carries the cost so the org
        reservation settles against what actually ran."""
        if not self._pending:
            return
        self._pending = False
        usage, self.cost_usd = self._partial()
        latency_ms = self._latency_ms()
        record_usage(
            provider=self._provider,
            model=self._model,
            usage=usage,
            cost_usd=self.cost_usd,
            latency_ms=latency_ms,
            success=False,
            error_class=error_class,
            failover_cause=self.failover_cause,
            org_reserved=self._org_reserved,
        )
        # Same reasoning as ``finish`` (#1240): the error propagates, so the
        # caller's completion-path emit (which reads ``state.llm_usage`` — set
        # only by ``done``) never fires. Emit here or the usage table and the
        # events-sink cost dashboard diverge by the partial's cost.
        _emit_llm_call(
            provider=self._provider,
            model=self._model,
            usage=usage,
            cost_usd=self.cost_usd,
            latency_ms=latency_ms,
        )

    def failover(self) -> None:
        """The provider errored and we move to the next one. No usage row: the
        pre-existing failover paths never wrote one, and the call is retried."""
        self._pending = False

    def finish(self) -> None:
        """Called from the generator's ``finally``. Books an attempt the
        consumer abandoned mid-stream, at the cost known right now: the full
        cost once ``done`` has been seen, the streamed partial before that.
        No-op when the attempt already booked itself."""
        if not self._pending:
            return
        self._pending = False
        if self._saw_done:
            self.cost_usd = self._done_cost_usd
            usage, error_class = self._usage, None
        else:
            usage, self.cost_usd = self._partial()
            error_class = 'abandoned'
        latency_ms = self._latency_ms()
        logger.warning(
            'llm_stream_abandoned',
            provider=self._provider,
            model=self._model,
            saw_done=self._saw_done,
            cost_usd=round(self.cost_usd, 6),
        )
        record_usage(
            provider=self._provider,
            model=self._model,
            usage=usage,
            cost_usd=self.cost_usd,
            latency_ms=latency_ms,
            success=True,
            error_class=error_class,
            failover_cause=self.failover_cause,
            org_reserved=self._org_reserved,
        )
        # Emit the analytics event from the same place that books the usage
        # row. A consumer that abandons the stream skips the trailing emit in
        # the caller (the code after its ``async for`` never runs under a
        # GeneratorExit), so the usage table would carry the spend while the
        # events-sink cost dashboard would not (#1240).
        _emit_llm_call(
            provider=self._provider,
            model=self._model,
            usage=usage,
            cost_usd=self.cost_usd,
            latency_ms=latency_ms,
        )


def _is_content_filter_error(e: ProviderAPIError) -> bool:
    """True when a 4xx is an Azure content-management rejection
    (``ResponsibleAIPolicy`` / ``content_filter``) rather than a genuine
    bad request.

    These are recoverable: a provider without Azure's content filter (e.g.
    Anthropic) usually completes the same turn, so the chain should fail
    over instead of surfacing a dead 400 to the user. Staging
    DeepSeek-via-Foundry synthesis turn, 2026-06-11: a benign job-search
    summary tripped the filter and broke the user's final message.
    """
    blob = f'{e.raw_message} {e}'.lower()
    return 'content_filter' in blob or 'content management policy' in blob or 'responsibleai' in blob


def _is_transient_for_same_provider_retry(e: Exception) -> bool:
    """True when a failure is worth a short same-provider retry before the
    chain advances (#1294): a 5xx (including the 504 stream-stall) or a
    timeout-shaped exception. Excludes 429 (its own turn-level backoff runs
    elsewhere), the Azure content filter, and every other 4xx.
    """
    if isinstance(e, ProviderRateLimitError):
        return False
    if isinstance(e, ProviderAPIError):
        if _is_content_filter_error(e):
            return False
        return e.status_code is not None and 500 <= e.status_code < 600
    return 'timeout' in type(e).__name__.lower()


async def _call_with_same_provider_retry(provider: LLMProvider, /, **call_kwargs: object) -> LLMResponse:
    """Invoke ``provider.chat`` retrying the SAME provider on a transient
    failure, up to ``settings.llm_same_provider_retries`` times with a short
    backoff, BEFORE the caller advances the fallback chain (#1294).

    Non-transient failures and the final transient failure propagate unchanged,
    so the caller's existing except handling (record + continue / re-raise) is
    untouched. With the retry count at 0 (prod default) the first failure
    re-raises immediately (behaviour identical to no retry at all).
    """
    from app.core.config import settings

    retries = settings.llm_same_provider_retries
    backoff = settings.llm_same_provider_retry_backoff_seconds
    attempt = 0
    while True:
        try:
            return await provider.chat(**call_kwargs)  # type: ignore[arg-type]
        except Exception as e:
            if attempt >= retries or not _is_transient_for_same_provider_retry(e):
                raise
            attempt += 1
            logger.warning(
                'llm_same_provider_retry',
                provider=type(provider).__name__,
                model=getattr(provider, '_default_model', ''),
                attempt=attempt,
                max_attempts=retries,
                error=str(e)[:200],
            )
            await asyncio.sleep(backoff)


def _failover_cause_for(e: Exception, content_filtered: bool = False) -> str:
    """Classify why a provider failure sent the chain to the next provider.

    Buckets the winning row's ``failover_cause`` (#1294): the Azure filter, the
    upstream HTTP status when there is one, an httpx/asyncio timeout by class
    name (no httpx import needed, and it also catches wrapped timeout shapes),
    else a generic error.
    """
    if content_filtered:
        return 'content_filter'
    status = getattr(e, 'status_code', None)
    if status:
        return f'http_{status}'
    if 'timeout' in type(e).__name__.lower():
        return 'timeout'
    return 'error'


class ResilientProvider(LLMProvider):
    """Wraps fallback chain — tries next provider on 5xx / connection errors."""

    def __init__(self, chain: list[LLMProvider] | None = None) -> None:
        self._chain = chain or get_fallback_chain()

    def _apply_json_output(self, kwargs: dict) -> dict | None:
        """Pass ``response_format`` through whole; this provider serves nothing.

        The chain is entered via ``provider.chat``, so the seam of whichever
        provider actually takes the call narrows the grade to what THAT
        provider honours and runs the local check on what is left. Applying
        the base rule here instead would read this class's own (empty)
        ``json_output_modes`` and strip the field before any of them saw it,
        which is how a fallback wrapper silently un-does the request.
        """
        return None

    async def _chat(self, *, system, messages, tools=None, max_tokens=8192, **kwargs) -> LLMResponse:
        # Pre-call context-window prefilter (#tonight, doc-upload): estimate
        # input tokens once, then drop any provider whose window cannot host the
        # prompt with output headroom. Short-context calls are unaffected.
        estimated_tokens = _estimate_call_tokens(
            system=system,
            messages=messages,
            tools=tools,
        )
        chain = _filter_chain_by_context(self._chain, estimated_tokens=estimated_tokens)
        # Enforced ONCE per resilient call, BEFORE the provider loop: it is a
        # tenant quota, not a per-provider one, so the rejection must happen even
        # if the chain would otherwise failover successfully. The reservation is
        # settled in the `finally` below, on the request path (#817).
        estimated_cost_usd = _estimate_call_cost_usd(
            chain,
            estimated_tokens=estimated_tokens,
            max_tokens=max_tokens,
        )
        reservation = await _enforce_spend_cap(estimated_cost_usd=estimated_cost_usd)
        actual_cost_usd = 0.0
        in_flight: LLMProvider | None = None
        start = time.monotonic()
        try:
            last_error: Exception | None = None
            failover_cause: str | None = None
            for provider in chain:
                in_flight = provider
                start = time.monotonic()
                try:
                    resp = await _call_with_same_provider_retry(
                        provider,
                        system=system,
                        messages=messages,
                        tools=tools,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                except ProviderRateLimitError as e:
                    # A 429 on one provider must fail over to the next, not abort
                    # the whole chain, that is what the fallback chain is for.
                    # (A 429 surfaced as ProviderAPIError already falls through
                    # below; keep the two 429 shapes consistent.) If every
                    # provider is rate-limited the loop re-raises the last
                    # ProviderRateLimitError so the turn-level backoff retry runs.
                    record_usage(
                        provider=type(provider).__name__,
                        model=getattr(provider, '_default_model', '') or '',
                        usage=None,
                        cost_usd=0.0,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        success=False,
                        error_class='rate_limited',
                    )
                    failover_cause = 'rate_limited'
                    logger.warning(
                        'llm_failover',
                        failed=type(provider).__name__,
                        model=getattr(provider, '_default_model', ''),
                        status=429,
                        error=str(e)[:200],
                    )
                    last_error = e
                    continue
                except ProviderAPIError as e:
                    _content_filtered = _is_content_filter_error(e)
                    if e.status_code and 400 <= e.status_code < 500 and e.status_code != 429 and not _content_filtered:
                        record_usage(
                            provider=type(provider).__name__,
                            model=getattr(provider, '_default_model', '') or '',
                            usage=None,
                            cost_usd=0.0,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            success=False,
                            error_class=f'http_{e.status_code}',
                        )
                        raise
                    failover_cause = _failover_cause_for(e, _content_filtered)
                    logger.warning(
                        'llm_failover',
                        failed=type(provider).__name__,
                        model=getattr(provider, '_default_model', ''),
                        status=e.status_code,
                        content_filter=_content_filtered,
                        error=str(e)[:200],
                    )
                    last_error = e
                    continue
                except JSONOutputError as e:
                    # Not an outage: the provider answered, and the reply
                    # contradicts the schema the call site declared. Falling
                    # through to the generic handler below would log an
                    # llm_failover, send the same schema down the rest of the
                    # chain at full price, and end on
                    # ``All providers failed ... 503``, which is a call-site
                    # problem wearing the shape of a provider one. Re-raised
                    # for the reason a non-429 4xx is: nothing further down
                    # the chain repairs a request that was wrong when it left.
                    #
                    # Metered like the success path and not like the 4xx one
                    # above. A 429 and a 400 book zero because nothing was
                    # generated; here the provider answered in full and will
                    # invoice it, and only the reply's shape is wrong. The
                    # response comes out attached to the error, from the one
                    # frame that still had it (``LLMProvider.chat``). Booking
                    # zero would drop the tokens from the usage table and
                    # refund the org reservation for a call that was paid for.
                    rejected = getattr(e, 'response', None)
                    actual_cost_usd = getattr(rejected, 'cost_usd', 0.0)
                    # One name for both readers below. The reply carries the
                    # deployment that answered, which is not always the one
                    # this provider defaults to; the usage row and the log
                    # line have to agree on which call they describe.
                    rejected_model = getattr(rejected, 'model', '') or getattr(provider, '_default_model', '') or ''
                    record_usage(
                        provider=type(provider).__name__,
                        model=rejected_model,
                        usage=getattr(rejected, 'usage', None),
                        cost_usd=actual_cost_usd,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        success=False,
                        error_class='json_output',
                        org_reserved=reservation is not None,
                    )
                    logger.warning(
                        'llm_json_output_rejected',
                        provider=type(provider).__name__,
                        model=rejected_model,
                        billed_usd=round(actual_cost_usd, 6),
                        error=str(e)[:200],
                    )
                    raise
                except Exception as e:
                    failover_cause = _failover_cause_for(e)
                    logger.warning('llm_failover', failed=type(provider).__name__, error=str(e)[:200])
                    last_error = e
                    continue
                # Success path: record actual usage from the provider response.
                record_usage(
                    provider=type(provider).__name__,
                    model=resp.model or getattr(provider, '_default_model', '') or '',
                    usage=resp.usage,
                    cost_usd=resp.cost_usd,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    success=True,
                    failover_cause=failover_cause,
                    org_reserved=reservation is not None,
                )
                actual_cost_usd = resp.cost_usd
                return resp
            if isinstance(last_error, ProviderRateLimitError):
                # Whole chain is rate-limited: surface the 429 so the turn-level
                # backoff retry runs, instead of masking it as a generic 503.
                raise last_error
            raise ProviderAPIError(f'All providers failed: {last_error}', status_code=503, raw_message=str(last_error))
        except asyncio.CancelledError:
            # A cancelled non-streaming call is still generated and billed in
            # full by the provider, and we never learn its real cost: book the
            # estimate. Settling at $0 would refund it (#817, #1208).
            actual_cost_usd = reservation.reserved_usd if reservation is not None else estimated_cost_usd
            if in_flight is not None:
                logger.warning(
                    'llm_call_abandoned',
                    provider=type(in_flight).__name__,
                    model=getattr(in_flight, '_default_model', ''),
                    booked_usd=round(actual_cost_usd, 6),
                )
                record_usage(
                    provider=type(in_flight).__name__,
                    model=getattr(in_flight, '_default_model', '') or '',
                    usage=None,
                    cost_usd=actual_cost_usd,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    success=True,
                    error_class='abandoned',
                    org_reserved=reservation is not None,
                )
            raise
        finally:
            # Shielded so a re-delivered cancel cannot cut the settle's Redis write.
            await asyncio.shield(
                _settle_org_reservation(
                    reservation,
                    actual_cost_usd=actual_cost_usd,
                )
            )

    async def _chat_stream(
        self, *, system, messages, tools=None, max_tokens=8192, **kwargs
    ) -> AsyncGenerator[LLMEvent]:
        estimated_tokens = _estimate_call_tokens(
            system=system,
            messages=messages,
            tools=tools,
        )
        chain = _filter_chain_by_context(self._chain, estimated_tokens=estimated_tokens)
        reservation = await _enforce_spend_cap(
            estimated_cost_usd=_estimate_call_cost_usd(
                chain,
                estimated_tokens=estimated_tokens,
                max_tokens=max_tokens,
            ),
        )
        # Booked from the `finally`, so an abandoned generator still records what
        # the provider ran and still settles the hold against it (#817, #1208).
        attempt = _StreamAttempt(
            estimated_input_tokens=estimated_tokens,
            org_reserved=reservation is not None,
            requested_model=str(kwargs.get('model') or ''),
        )
        try:
            last_error: Exception | None = None
            # No same-provider retry here (#1294), and no chain advance once
            # this attempt has delivered an event (#2320): both would restart
            # the stream and re-emit already-yielded output — duplicated text
            # on the wire, and the failed attempt's tool blocks summed with
            # (and charged alongside) the next provider's, under fresh
            # tool_use_ids that defeat the charge-idempotency guard. A
            # provider that fails BEFORE its first delivered event fails over
            # transparently, exactly as before; one that fails after delivery
            # books its partial and re-raises — as itself when it is already a
            # Provider* type, wrapped in a ProviderAPIError(504) otherwise —
            # so it always lands in the turn-level handler, which closes the
            # stream cleanly.
            for provider in chain:
                attempt.begin(provider)
                try:
                    # ``aclosing``: close the provider stream now, not on GC.
                    async with aclosing(
                        provider.chat_stream(
                            system=system, messages=messages, tools=tools, max_tokens=max_tokens, **kwargs
                        )
                    ) as stream:
                        async for event in stream:
                            attempt.observe(event)
                            yield event
                    attempt.complete()
                    return
                except ProviderRateLimitError as e:
                    # A 429 on one provider must fail over to the next, not abort
                    # the whole chain, that is what the fallback chain is for.
                    # (A 429 surfaced as ProviderAPIError already falls through
                    # below; keep the two 429 shapes consistent.) If every
                    # provider is rate-limited the loop re-raises the last
                    # ProviderRateLimitError so the turn-level backoff retry runs.
                    if attempt.has_emitted:
                        attempt.fail_mid_stream('rate_limited')
                        logger.warning(
                            'llm_stream_failed_after_output',
                            failed=type(provider).__name__,
                            model=getattr(provider, '_default_model', ''),
                            status=429,
                            error=str(e)[:200],
                        )
                        raise
                    attempt.fail('rate_limited')
                    attempt.failover_cause = 'rate_limited'
                    logger.warning(
                        'llm_failover',
                        failed=type(provider).__name__,
                        model=getattr(provider, '_default_model', ''),
                        status=429,
                        error=str(e)[:200],
                    )
                    last_error = e
                    continue
                except ProviderAPIError as e:
                    _content_filtered = _is_content_filter_error(e)
                    # Emitted-check FIRST, status split second: a 4xx that
                    # arrives after delivered output still leaves billed
                    # tokens on the wire, so it books the partial via
                    # ``fail_mid_stream`` like every other mid-stream failure
                    # — the $0 ``fail()`` row below is for requests the
                    # provider rejected before running them.
                    if attempt.has_emitted:
                        attempt.fail_mid_stream(
                            _failover_cause_for(e, _content_filtered),
                        )
                        logger.warning(
                            'llm_stream_failed_after_output',
                            failed=type(provider).__name__,
                            model=getattr(provider, '_default_model', ''),
                            status=e.status_code,
                            content_filter=_content_filtered,
                            error=str(e)[:200],
                        )
                        raise
                    if e.status_code and 400 <= e.status_code < 500 and e.status_code != 429 and not _content_filtered:
                        attempt.fail(f'http_{e.status_code}')
                        raise
                    attempt.failover()
                    attempt.failover_cause = _failover_cause_for(e, _content_filtered)
                    logger.warning(
                        'llm_failover_stream',
                        failed=type(provider).__name__,
                        model=getattr(provider, '_default_model', ''),
                        status=e.status_code,
                        content_filter=_content_filtered,
                        error=str(e)[:200],
                    )
                    last_error = e
                except Exception as e:
                    if attempt.has_emitted:
                        attempt.fail_mid_stream(_failover_cause_for(e))
                        logger.warning(
                            'llm_stream_failed_after_output',
                            failed=type(provider).__name__,
                            error=str(e)[:200],
                        )
                        # Wrapped, not raised raw: the turn-level handler
                        # catches the Provider* types only, so a raw
                        # httpx.ReadTimeout escaping mid-answer would sail
                        # past it into the SSE catch-all as a CRASH — no
                        # ``done`` frame, no KG flush, no session_completed.
                        # 504 = the upstream stalled mid-response, which is
                        # what every exception on an open stream amounts to
                        # from the consumer's side.
                        raise ProviderAPIError(
                            f'stream failed after output: {e}',
                            status_code=504,
                            raw_message=str(e),
                        ) from e
                    attempt.failover()
                    attempt.failover_cause = _failover_cause_for(e)
                    logger.warning('llm_failover_stream', failed=type(provider).__name__, error=str(e)[:200])
                    last_error = e
            if isinstance(last_error, ProviderRateLimitError):
                # Whole chain is rate-limited: surface the 429 so the turn-level
                # backoff retry runs, instead of masking it as a generic 503.
                raise last_error
            raise ProviderAPIError(f'All providers failed: {last_error}', status_code=503, raw_message=str(last_error))
        finally:
            # Reached by GeneratorExit (aclose) and CancelledError too, which is
            # the whole point: neither is swallowed, both are re-raised after we
            # have booked the call. `finish` is sync, so no cancellation can be
            # re-delivered into it; the settle is shielded so a cancelled frame
            # cannot cut the Redis write and leave the call unbooked.
            attempt.finish()
            await asyncio.shield(
                _settle_org_reservation(
                    reservation,
                    actual_cost_usd=attempt.cost_usd,
                )
            )

    def context_window(self, model: str = '') -> int:
        """The window of the arm that will actually serve this turn: the head.

        Same rule as the ``supports_*`` methods below, and for the same reason
        — every capability answer here describes the arm the turn is being
        built for, not a hypothetical one.

        This WAS the minimum across the chain, and the minimum answered a
        question nobody asks. Measured on the ``get_fallback_chain`` of the
        time, which still appended ``_get_anthropic``: the minimum was 200K on
        EVERY primary, DeepSeek's 1M arm included — it did not describe the
        turn, it described whichever arm happened to be narrowest among arms
        the turn may never touch. And it moved for the wrong reason: drop that
        unused Anthropic arm and the minimum jumps to 1M, so the answer changed
        because a provider we were not using went away. (That arm is now gone
        from the chain for real, which is exactly the shift described.)

        The head has neither property: it moves only when the arm serving the
        turn moves. If the head fails to init, ``get_fallback_chain`` promotes
        the next arm and this answers for THAT arm.

        What this is NOT is a licence to spend. The one consumer sizing a
        prompt from it, the history replay, clamps whatever comes back to its
        own ceiling and only ever narrows (``_history_replay.
        history_budget_chars``, API#1785) — so a wide head buys nothing
        and a narrow head is protected, which is the direction that matters. It
        also means the head-versus-minimum choice costs nothing either way now:
        both would clamp to the same number on every wide arm.

        Empty chain (every factory failed to init) answers with the
        conservative default rather than raising: the call is going to fail
        for its own reasons, and a sizing helper is not where that surfaces.
        """
        if not self._chain:
            return CONSERVATIVE_CONTEXT_WINDOW
        return self._chain[0].context_window(model)

    def supports_prompt_caching(self) -> bool:
        return self._chain[0].supports_prompt_caching() if self._chain else False

    def supports_tool_use(self) -> bool:
        return self._chain[0].supports_tool_use() if self._chain else False

    def supports_server_tools(self) -> bool:
        return self._chain[0].supports_server_tools() if self._chain else False

    def effective_model(self, requested: str = '') -> str:
        """Delegate to the chain head — the provider that will actually stream.

        The base implementation answers ``requested or self._default_model``,
        and a ResilientProvider has no ``_default_model``, so it parroted the
        caller's guess back. That guess is wrong on every prod turn: the tier
        resolver asks for ``claude-*`` while the head is DeepSeek, whose
        ``effective_model`` rewrites it to ``deepseek-v4-flash``. Callers
        holding the wrapper (everything ``get_provider()`` returns) were told
        the model they asked for, not the one they'd be billed for.
        """
        return self._chain[0].effective_model(requested) if self._chain else requested

    @property
    def primary_provider_name(self) -> str:
        """Class name of the active primary provider (chain head).

        Callers that switch behaviour per primary (e.g. the Kimi-specific
        tool-coax in agent_loop) must read this rather than the global
        ``settings.chat_primary_provider`` env var — per-user PostHog
        gates can flip the runtime primary while the env var stays put,
        so the env-var check silently no-ops for opted-in users.
        """
        return type(self._chain[0]).__name__ if self._chain else ''
