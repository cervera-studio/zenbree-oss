"""
LLM Provider abstraction — the agent loop never imports a provider SDK directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import structlog

from app.llm.json_output import (
    JSON_OBJECT,
    JSON_SCHEMA,
    JSONOutputError,
    check_reply,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger()

# ── Provider-agnostic errors ────────────────────────────────────────────────


class ProviderRateLimitError(Exception):
    """429 — provider throttled the request."""

    def __init__(self, message: str = 'Rate limit exceeded', wait_seconds: int = 30) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds


class ProviderAPIError(Exception):
    """Non-retryable API error from the provider (4xx/5xx)."""

    def __init__(
        self,
        message: str = 'Provider API error',
        *,
        status_code: int | None = None,
        raw_message: str = '',
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw_message = raw_message or message


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """One call's tokens, in four DISJOINT buckets.

    ``input_tokens`` is the prompt billed at the full input rate — it EXCLUDES
    ``cache_read_tokens`` and ``cache_creation_tokens``. Total prompt size is
    the sum of the three. ``pricing.cost_usd`` adds one term per bucket, so an
    adapter that lets two of them overlap bills those tokens twice.

    Anthropic reports this shape natively (``cache_read_input_tokens`` arrives
    BESIDE ``input_tokens``, ``anthropic.py:170-171``). The OpenAI-compatible
    wire format does not — it nests the cached prefix INSIDE ``prompt_tokens``
    — so ``openai_compat._usage_from`` subtracts it on the way in (#3134). Any
    new adapter has to answer the same question before filling these in.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    input: dict
    error: str = ''  # arguments were not a JSON object; input is then {}


@dataclass(frozen=True, slots=True)
class ServerToolUse:
    """Anthropic server-side tool (web_search), stored verbatim in message history."""

    id: str
    name: str
    input: dict
    raw_block: dict = field(default_factory=dict)  # model_dump() of the SDK object


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Results from Anthropic's built-in web_search server tool."""

    tool_use_id: str
    results: list[dict] = field(default_factory=list)  # [{url, title}, ...]
    error: str | None = None
    raw_block: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    server_tool_uses: list[ServerToolUse] = field(default_factory=list)
    web_search_results: list[WebSearchResult] = field(default_factory=list)
    # Plaintext citation spans the model quoted from server web_search
    # results — harvested from each text block's ``citations`` (Anthropic
    # ``web_search_result_location``). Unlike the encrypted
    # ``web_search_results`` bodies, ``cited_text`` is groundable, so this
    # is what the synthesis-grounding check consumes for the server path.
    # Each item: ``{'url': str, 'title': str, 'cited_text': str}``.
    web_search_citations: list[dict] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    model: str = ''
    stop_reason: str = ''  # 'end_turn', 'tool_use', 'pause_turn', 'stop', etc.


@dataclass(frozen=True, slots=True)
class LLMEvent:
    """Streaming event from an LLM provider.

    Types:
        text_delta      — partial text chunk
        tool_start      — tool_call begins (id + name known, input empty)
        tool_delta      — partial tool input JSON
        tool_end        — tool_call complete (full input parsed)
        server_tool_use — server-side tool invocation (Anthropic web_search)
        web_search_result — results from server-side web search
        done            — stream finished; carries final usage + stop_reason
    """

    type: str
    text: str = ''
    tool_call: ToolCall | None = None
    server_tool_use: ServerToolUse | None = None
    web_search_result: WebSearchResult | None = None
    # Set on the ``done`` event: plaintext web_search citation spans for
    # the turn (see :class:`LLMResponse.web_search_citations`).
    web_search_citations: list[dict] = field(default_factory=list)
    usage: TokenUsage | None = None
    stop_reason: str = ''
    cost_usd: float = 0.0
    model: str = ''


# ── Sampling policy  ────────────────────────────────────────
#
# Provider defaults are neither neutral nor equal (Anthropic 1.0, OpenAI 1.0,
# xAI 1.0). The failover chain routes the SAME call to
# gpt-5.4-mini → Claude → grok → DeepSeek, so before this the sampling of a
# turn depended on which arm happened to answer — invisible output variance
# that reads as prompt variance in the evals.
#
# Policy by CALL CLASS, not by call site:
#
#   class                   example call site                     temperature
#   ─────────────────────   ───────────────────────────────────   ───────────
#   agent loop / tool use   chat.use_cases.loop_runner            0.2 (default)
#   synthesis / extraction  chat.tools.handlers.compose_report    0.3 (explicit)
#   creative                chat.tools.handlers.plan_deck         per-stage
#                                                                 (explicit,
#                                                                  0.2-0.7)
#   scraper (out of seam)   scraping.adapters.* (litellm)         0 (explicit)
#
# The default is applied in the SEAM (``LLMProvider.chat`` / ``chat_stream``),
# not in the callers: a provider added to the chain tomorrow is normalised by
# construction and cannot silently reintroduce its own default. Call sites
# override by passing ``temperature=`` — the seam only fills the blank.
#
# ``top_p`` is deliberately NEVER set. Anthropic and OpenAI both advise
# tuning temperature OR top_p, not both, and every arm in our chain honours
# temperature. One knob, one policy.
DEFAULT_TEMPERATURE = 0.2

_SAMPLING_DEFAULTS: dict[str, float] = {'temperature': DEFAULT_TEMPERATURE}


# ── Model capabilities  ─────────────────────────────
#
# Declared here, next to ``supports_prompt_caching`` and friends, because it is
# the same kind of fact: what the arm can do, asked of the provider rather than
# guessed by the caller. Keyed by the model the provider will ACTUALLY serve
# (``effective_model``), not by provider class: ``AzureFoundryProvider`` serves
# gpt / grok / DeepSeek through one class, and a class key gave all of them the
# same 1M window (#3238).
#
# Numbers are the variants we ship against, NOT advertised maximums (Claude has
# a 1M option we do not enable). Flip a deployment to a longer-context variant
# and update its row in the same PR.

CacheMode = Literal['explicit_breakpoints', 'automatic_prefix', 'none']


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """What one model can host, and how its provider caches a prompt prefix.

    ``cache_mode``:
      * ``explicit_breakpoints`` — caches only where the request puts
        ``cache_control`` markers (Anthropic).
      * ``automatic_prefix`` — the provider caches matching prefixes on its own;
        markers are neither needed nor sent, but a STABLE prefix is (DeepSeek,
        OpenAI).
      * ``none`` — no prefix cache we can use.

    ``reserved_output`` is what a call reserves by default (the seam's
    ``max_tokens``); ``safety_margin`` is the fraction of the window kept free
    for estimator error, the router prefilter's 10% headroom.
    """

    context_window: int
    cache_mode: CacheMode = 'none'
    reserved_output: int = 8192
    safety_margin: float = 0.1


# Lower-case ids. Azure Foundry spells its deployments ``DeepSeek-V3.2``.
_MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {
    # Anthropic — 200K: the 1M long-context option is not enabled.
    'claude-sonnet-4-6': ModelCapabilities(200_000, 'explicit_breakpoints'),
    'claude-haiku-4-5-20251001': ModelCapabilities(200_000, 'explicit_breakpoints'),
    'claude-haiku-4-5': ModelCapabilities(200_000, 'explicit_breakpoints'),
    'claude-opus-4-6': ModelCapabilities(200_000, 'explicit_breakpoints'),
    'claude-opus-4-8': ModelCapabilities(200_000, 'explicit_breakpoints'),
    # Azure Foundry. OpenAI caches prefixes >=1024 tokens by itself.
    'gpt-5.4-mini': ModelCapabilities(1_000_000, 'automatic_prefix'),
    'gpt-5.4': ModelCapabilities(1_000_000, 'automatic_prefix'),
    'grok-4-20-non-reasoning': ModelCapabilities(1_000_000),
    'grok-4-20-reasoning': ModelCapabilities(1_000_000),
    # Was 1M under the class key; V3.2 is 128K-class everywhere, 163_840 per
    # the vendored map's ``azure_ai/deepseek-v3.2``.
    'deepseek-v3.2': ModelCapabilities(163_840),
    # DeepSeek direct (api-docs.deepseek.com/guides/kv_cache). ponytail: the
    # same id on Azure Foundry (staging) gets this row too; its cache there is
    # unverified, and nothing reads cache_mode to spend money yet.
    'deepseek-v4-flash': ModelCapabilities(1_000_000, 'automatic_prefix'),
    'deepseek-v4-pro': ModelCapabilities(1_000_000, 'automatic_prefix'),
    # Moonshot: 128K is conservative (K2.6 is 256K). Its context cache is a
    # separate API we do not call, so for us it is none.
    'kimi-k2.6': ModelCapabilities(128_000),
}


def _registered(model: str) -> ModelCapabilities | None:
    """Row for ``model``, tolerating case and a ``vendor/`` routing prefix."""
    key = model.strip().lower()
    caps = _MODEL_CAPABILITIES.get(key)
    if caps is None and '/' in key:
        caps = _MODEL_CAPABILITIES.get(key.split('/', 1)[1])
    return caps


_unknown_logged: set[str] = set()


#: What an UNREGISTERED model is assumed to hold. Deliberately the smallest
#: window we ship against: a caller sizing a prompt against this number builds
#: something every provider can host, which is the safe direction for a budget.
#: It is NOT the safe direction for the failover prefilter — guessing low there
#: would skip the only viable provider — so that consumer keeps its own
#: "unknown means don't filter" rule via :func:`declared_context_window`.
CONSERVATIVE_CONTEXT_WINDOW = 32_000
CONSERVATIVE_CAPABILITIES = ModelCapabilities(CONSERVATIVE_CONTEXT_WINDOW)

#: ~4 characters per token, the figure the Anthropic docs quote for
#: English-heavy markdown. Declared here so the two consumers that have to
#: AGREE — the failover prefilter (``router._estimate_tokens``) and the history
#: budget (``chat.use_cases._history_replay.history_budget_chars``) — read one
#: constant: a prompt sized by one and rejected by the other is the failure this
#: prevents. ``llm.wrapping`` and ``chat.orchestrator.budget_estimator`` alias it
#: too — they measure things nobody compares against these, so folding them in
#: was cleanup rather than a correctness fix, but a second literal 4 is a second
#: number to retune and one of them always gets missed.
#:
#: NOT the yardstick for the byte-based estimators: ``extraction.sanitize``
#: (``len(text.encode('utf-8')) // 4``) and the attachment preflight in
#: ``chat.routes.chat`` divide BYTES, not characters, and the two coincide only
#: on ASCII. Folding those into this constant would silently retune a cap.
#:
#: Known bias: it UNDER-counts dense JSON and non-English text, which is most of
#: what a replayed tool result is made of. Both consumers therefore apply a
#: headroom ratio on top rather than spending the whole window.
CHARS_PER_TOKEN = 4


def declared_context_window(provider: LLMProvider, model: str = '') -> int | None:
    """The registered window for the model ``provider`` serves, or ``None``.

    The ``None`` arm is the difference between this and
    :meth:`LLMProvider.context_window`: a consumer that must not act on a guess
    (the failover prefilter, which would starve the chain) reads this; a
    consumer that needs a number to size something (the history budget) reads
    the method and gets :data:`CONSERVATIVE_CONTEXT_WINDOW`.

    An object without ``effective_model`` (a duck-typed fake) is unregistered,
    as it was under the old class-name lookup.
    """
    effective_model = getattr(provider, 'effective_model', None)
    if effective_model is None:
        return None
    caps = _registered(effective_model(model))
    return caps.context_window if caps else None


class LLMProvider(ABC):
    """Abstract base for all LLM providers. Implement for each provider.

    Subclasses implement ``_chat`` / ``_chat_stream``; the public ``chat`` /
    ``chat_stream`` are the sampling seam and are not meant to be overridden.
    """

    #: Sampling params this provider actually sends. Anything outside the set
    #: is dropped before the request body is built, so an unsupported param can
    #: never turn into a 400. Verified live per provider (2026-07-14): every
    #: Azure Foundry deployment (gpt-5.4-mini, grok-4-20-*, DeepSeek-V3.2/V4)
    #: and Anthropic accept ``temperature``; Moonshot pins it by vendor
    #: contract and narrows this to ``frozenset()``.
    sampling_params: frozenset[str] = frozenset({'temperature'})

    #: JSON output grades this provider honours on the wire, from
    #: :mod:`app.llm.json_output`. Empty means the provider is sent no
    #: ``response_format`` at all: a fallback that predates the field keeps
    #: working instead of 400ing on it, and the call site's tolerant reader
    #: stays the net it already is. Declared from each provider's own
    #: documentation, never inferred from the API being OpenAI-shaped:
    #: DeepSeek documents the object grade and no schema grade, and Azure
    #: Foundry documents it per deployment, so that class answers through
    #: :meth:`_json_output_modes` instead of this set.
    json_output_modes: frozenset[str] = frozenset()

    def _json_output_modes(self, kwargs: dict) -> frozenset[str]:
        """The grades honoured for THIS call.

        Split out from the class attribute for the one provider whose answer
        depends on the call: Azure Foundry fronts several deployments through
        a single class and they do not all document the same support. Reads
        ``kwargs`` without consuming anything, since the body builder still
        needs what is in there.
        """
        return self.json_output_modes

    def _apply_sampling(self, kwargs: dict) -> dict:
        for name, default in _SAMPLING_DEFAULTS.items():
            if name in self.sampling_params:
                kwargs.setdefault(name, default)
            else:
                kwargs.pop(name, None)
        return kwargs

    def _apply_json_output(self, kwargs: dict) -> dict | None:
        """Narrow ``response_format`` to the grade this provider honours.

        Returns the schema the WIRE will NOT be enforcing, so the seam can
        hold the reply to it locally, or ``None`` when there is nothing left
        to check. A schema call to a provider that only does objects is sent
        the object grade rather than dropped: it is the difference between
        losing the type constraint and losing JSON altogether.
        """
        requested = kwargs.pop('response_format', None)
        if not isinstance(requested, dict):
            return None
        modes = self._json_output_modes(kwargs)
        kind = requested.get('type')
        if kind in modes:
            kwargs['response_format'] = requested
            return None
        if kind != JSON_SCHEMA:
            return None
        if JSON_OBJECT in modes:
            kwargs['response_format'] = {'type': JSON_OBJECT}
        schema = (requested.get('json_schema') or {}).get('schema')
        return schema if isinstance(schema, dict) else None

    async def chat(
        self,
        *,
        system: str | list[dict],
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        **kwargs: object,
    ) -> LLMResponse:
        unenforced = self._apply_json_output(kwargs)
        response = await self._chat(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            **self._apply_sampling(kwargs),
        )
        if unenforced is not None:
            try:
                check_reply(response.text, unenforced)
            except JSONOutputError as e:
                # The reply is rejected, the tokens are not free: they were
                # generated and they will be on the invoice. This is the last
                # frame that still holds the response, and the frame that
                # meters the call is several above it, so the response rides
                # out on the exception instead of being dropped here.
                e.response = response
                raise
        return response

    def chat_stream(
        self,
        *,
        system: str | list[dict],
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        **kwargs: object,
    ) -> AsyncGenerator[LLMEvent]:
        # No local check on this side: a stream cannot be held to a schema
        # while it is still arriving, and the caller has already been handed
        # the first half by the time the last brace would settle it. What the
        # wire honours is still normalised, which is the part that matters:
        # a streamed reply from a provider in JSON mode is a JSON object.
        self._apply_json_output(kwargs)
        return self._chat_stream(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            **self._apply_sampling(kwargs),
        )

    @abstractmethod
    async def _chat(
        self,
        *,
        system: str | list[dict],
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        **kwargs: object,
    ) -> LLMResponse: ...

    @abstractmethod
    def _chat_stream(
        self,
        *,
        system: str | list[dict],
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        **kwargs: object,
    ) -> AsyncGenerator[LLMEvent]: ...

    def effective_model(self, requested: str = '') -> str:
        """The model this provider will ACTUALLY serve for ``requested``.

        An abandoned stream never sees a ``done`` event, so its usage row has to
        be priced against a guess. Ask the provider rather than assuming it
        honours the caller's kwarg: one that rewrites the model (see
        ``OpenAICompatibleProvider``) would otherwise be billed for a model it
        never ran."""
        return requested or getattr(self, '_default_model', '') or ''

    def model_capabilities(self, model: str = '') -> ModelCapabilities:
        """Capabilities of the model this provider will serve for ``model``.

        Unregistered model → :data:`CONSERVATIVE_CAPABILITIES` and one warning
        per model id per process, so a new deployment shows up in the logs
        instead of silently running on a 32K budget.
        """
        served = self.effective_model(model)
        caps = _registered(served)
        if caps is not None:
            return caps
        if served not in _unknown_logged:
            _unknown_logged.add(served)
            logger.warning(
                'llm.model_capabilities_unknown',
                model=served,
                provider=type(self).__name__,
                assumed_window=CONSERVATIVE_CONTEXT_WINDOW,
            )
        return CONSERVATIVE_CAPABILITIES

    def context_window(self, model: str = '') -> int:
        """Tokens the served model can host, output included.

        A capability declaration, not a measurement: callers sizing a prompt
        still subtract what the turn spends before the first replayed row and
        leave headroom (``chat.use_cases.history_window``).
        """
        return self.model_capabilities(model).context_window

    def cache_mode(self, model: str = '') -> CacheMode:
        """How the served model caches a prompt prefix; see :class:`ModelCapabilities`."""
        return self.model_capabilities(model).cache_mode

    def caches_prefix_automatically(self, model: str = '') -> bool:
        """The provider caches a stable prefix with no markers (DeepSeek, OpenAI).

        The other half of what ``supports_prompt_caching`` used to be asked to
        mean: that one is marker EMISSION only, and flipping it on for DeepSeek
        would put Anthropic ``cache_control`` blocks on a wire that ignores them.
        """
        return self.cache_mode(model) == 'automatic_prefix'

    @abstractmethod
    def supports_prompt_caching(self) -> bool:
        """Whether callers should EMIT explicit ``cache_control`` markers.

        Marker emission only — a property of the wire format, so it stays per
        provider class. Automatic prefix caching is
        :meth:`caches_prefix_automatically`.
        """

    @abstractmethod
    def supports_tool_use(self) -> bool: ...

    def supports_server_tools(self) -> bool:
        """Whether this provider supports server-side tools (e.g. Anthropic web_search)."""
        return False

    def skill_invocation_tier(self) -> str:
        """How strong this provider's function-calling is for v2 skill
        invocation (see ``app.chat.skills``).

        Returns one of:

        - ``'high'`` — top-tier function calling (Anthropic Claude, GPT-4.1).
          ``invoke_skill`` is exposed; skills load lazily.
        - ``'medium'`` — solid function calling (DeepSeek, Moonshot,
          older OpenAI models). ``invoke_skill`` is exposed but a
          ``high``-tier skill may eager-load its top-1 body alongside.
        - ``'low'`` — fragile or absent function calling (no shipped
          provider today; the tier remains for the next small local
          model). A ``high``-tier skill drops to the legacy eager path
          with ``invoke_skill`` hidden; medium/low skills still run
          hybrid, ``invoke_skill`` exposed with the top-1 body
          eager-loaded alongside.

        Default: ``'medium'``. Subclasses should override to declare
        their tier explicitly. The string return type (instead of an
        enum) keeps this base module free of an
        ``app.chat.skills`` import dependency — circular import risk
        is high since ``app.chat.skills.loader`` already imports the
        provider Protocol implicitly.
        """
        return 'medium'
