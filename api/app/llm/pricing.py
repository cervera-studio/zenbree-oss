"""Single source of truth for per-model LLM prices and cost computation.

Layered lookup, first match wins:

1. ``_OVERRIDES`` -- prices we pin explicitly: the models we actively route
   (Anthropic, with prompt-cache rates where cache_creation dominates our
   spend; DeepSeek direct + on Azure incl. the brand-new v4) and anything the
   LiteLLM map does not yet carry. Hand-verified.
2. **LiteLLM price map** -- the community-maintained price table, vendored as
   ``data/litellm_model_prices.json`` (re-vendor the file to refresh). Covers
   the long tail without us editing code.
3. ``_DEFAULT`` -- last-resort estimate. A total miss also logs a one-time
   warning so an un-priced model is never silently metered as free.

Our recorded model strings differ from LiteLLM keys, so ``_LITELLM_ALIASES``
maps ours -> theirs for the layer-2 lookup. All prices are USD per 1M tokens.
"""

from __future__ import annotations

import functools
import json
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from app.llm.base import TokenUsage

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Price:
    input: float
    output: float
    cache_read: float = 0.0
    cache_creation: float = 0.0


# Hand-pinned prices (USD / 1M tokens). LiteLLM lags on brand-new models
# (DeepSeek v4) and we want explicit control over the cache rates that drive
# Anthropic cost, so the models we actually route live here rather than
# relying on the map.
#
# A hand-pinned price is only as good as its last read, and nothing here
# fails when it goes stale: the number is simply wrong, on every booked
# figure, silently. So each block says WHEN it was read and FROM WHERE.
# Only the DeepSeek-direct block was re-read on 2026-09-20; the rest carry
# their 2026-06 verification, and at least two of them are known to disagree
# with the vendored LiteLLM map — see the notes on those lines.
_OVERRIDES: dict[str, Price] = {
    # Anthropic (cache_creation is ~70% of our agent-loop cost). Verified
    # 2026-06. NOT re-read here; note that the vendored LiteLLM map disagrees
    # on haiku (it says 1.00/5.00/0.10/1.25 against the 0.80/4.0/0.08/1.0
    # below), which is an under-book of ~20% if the map is the current one.
    'claude-sonnet-4-6': Price(3.0, 15.0, 0.30, 3.75),
    'claude-haiku-4-5-20251001': Price(0.80, 4.0, 0.08, 1.0),
    'claude-opus-4-8': Price(5.0, 25.0, 0.50, 6.25),
    # DeepSeek direct API. Read off api-docs.deepseek.com/quick_start/pricing
    # on 2026-09-20. The previous 0.14/0.28 and 0.435/0.87 were a V3-era
    # list: output alone was under-booked 4.3x on Flash and 4.6x on Pro.
    #
    # THESE ARE THE PEAK RATES, AND NO SINGLE NUMBER CAN BE RIGHT. The same
    # page halves every rate off-peak (peak is 01:00-04:00 and 06:00-10:00
    # UTC, Mon-Fri, excluding Chinese public holidays; everything else,
    # weekends included, is off-peak), and ``Price`` has one field per
    # category with nowhere to put a time of day. Pinning peak over-books an
    # off-peak call by at most exactly 2x, which is the direction a spend cap
    # survives; the cache rate uses the same convention.
    # Representing the two windows properly means giving ``cost_usd`` the
    # call's timestamp, which is a change to every caller, not to this table.
    #
    # ``deepseek-flash`` is the CURRENT id on that page. It was absent from
    # both this table and the LiteLLM map, so it resolved to ``_DEFAULT`` —
    # 3.00/15.00, a 10x over-book — the moment anyone set DEEPSEEK_MODEL to
    # the name the docs tell them to use. ``deepseek-v4-flash`` (our config
    # default) is the legacy alias: the page says those requests are served
    # by DeepSeek-V4.1-Flash "and billed at the Flash price", so one price,
    # two keys. The third number on the three rows below is the cache-HIT rate
    # from the same PEAK column (0.006 Flash, 0.044 Pro), carried over from
    # the change that first made the hit count readable.
    #
    # ``deepseek-chat``/``-reasoner`` are NOT on that page at all — the rule
    # above is applied to them by INFERENCE, not quoted. Nothing books against
    # these two keys today: in the API they survive only in prose and in
    # bench CLI defaults spelled ``deepseek/deepseek-chat``, which the LiteLLM
    # map prices under its own vendor-prefixed entry and which therefore never
    # reaches this table (``test_pricing_vendor_prefix``). The inference costs
    # nothing if it is wrong, and it errs toward over-booking — the map's own
    # V3-era 0.28/0.42 for them is already above the 0.14/0.28 they carried.
    'deepseek-chat': Price(0.30, 1.20),
    'deepseek-reasoner': Price(0.30, 1.20),
    'deepseek-flash': Price(0.30, 1.20, 0.006),
    'deepseek-v4-flash': Price(0.30, 1.20, 0.006),
    'deepseek-v4-pro': Price(1.32, 3.96, 0.044),
    # DeepSeek on Azure Foundry (Global Standard). Verified 2026-06, NOT
    # re-read here: Foundry has its own list, so these do not follow the
    # rates above and must not be "corrected" to match them. Worth a look in
    # its own change, though — V3.2 agrees with the LiteLLM map exactly
    # (azure_ai/deepseek-v3.2), while the two V4 rows are in neither the map
    # nor the DeepSeek page, and 0.19/0.51 is BELOW what DeepSeek itself
    # charges for Flash, which a reseller normally is not.
    'DeepSeek-V3.2': Price(0.58, 1.68),
    'DeepSeek-V4-Flash': Price(0.19, 0.51),
    'DeepSeek-V4-Pro': Price(1.74, 3.48),
    # Azure OpenAI. Verified 2026-06, NOT re-read here; both agree with the
    # LiteLLM map to the cent.
    'gpt-5.4-mini': Price(0.75, 4.5),
    'gpt-5.4': Price(2.5, 15.0),
    # Moonshot / Kimi (cache-miss rate; hits not surfaced). Verified 2026-06,
    # NOT re-read here; the map's moonshot/ entry says 0.60/2.50, so this one
    # over-books by ~50% if the map is current — the safe direction, unlike
    # DeepSeek, which is why it can wait.
    'kimi-k2-0711-preview': Price(0.90, 3.75),
}

# Our model string -> LiteLLM map key, for models served by the layer-2 map.
_LITELLM_ALIASES: dict[str, str] = {
    'grok-4-20-non-reasoning': 'azure_ai/grok-4-fast-non-reasoning',
    'grok-4-20-reasoning': 'azure_ai/grok-4-fast-reasoning',
}

_DEFAULT = Price(3.0, 15.0, 0.30, 3.75)

_LITELLM_PATH = pathlib.Path(__file__).parent / 'data' / 'litellm_model_prices.json'


@functools.cache
def _litellm_map() -> dict:
    try:
        return json.loads(_LITELLM_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        logger.warning('pricing.litellm_map_unavailable', error=str(e)[:200])
        return {}


def _from_litellm(model: str) -> Price | None:
    entry = _litellm_map().get(_LITELLM_ALIASES.get(model, model))
    if not isinstance(entry, dict):
        return None

    def per_m(field: str) -> float:
        v = entry.get(field)
        return float(v) * 1_000_000 if v is not None else 0.0

    inp, out = per_m('input_cost_per_token'), per_m('output_cost_per_token')
    if inp == 0.0 and out == 0.0:
        return None
    return Price(inp, out, per_m('cache_read_input_token_cost'), per_m('cache_creation_input_token_cost'))


@functools.cache
def get_price(model: str) -> Price:
    """Resolve a model's price via overrides -> LiteLLM map -> default.

    ``functools.cache`` keeps the lookup (and the not-found warning) to once
    per distinct model string for the process lifetime.
    """
    override = _OVERRIDES.get(model)
    if override is not None:
        return override
    mapped = _from_litellm(model)
    if mapped is not None:
        return mapped
    # OpenRouter names every model ``<vendor>/<model>``, so a gateway-routed
    # call reports ``deepseek/deepseek-v4-flash`` where the tables above are
    # keyed on ``deepseek-v4-flash``. Falling through to _DEFAULT priced that
    # at 3.00/15.00 against a real 0.30/1.20 — a 10x overstatement, silently,
    # on every booked figure for a gateway environment. (It read "~20x against
    # 0.14/0.28" until the 2026-09-20 re-read; the bug is the same
    # size in kind, and the fallback is what keeps the scraping path —
    # ``deepseek/deepseek-v4-flash``, ``deepseek/deepseek-v4-pro`` in
    # the API's ALLOWED_LLM_MODELS — on the rates above.)
    if '/' in model:
        # The reseller's OWN list price when the map knows it: exact, margin
        # included. Only the newest models are missing here.
        reseller = _from_litellm(f'openrouter/{model}')
        if reseller is not None:
            return reseller
        # Otherwise the upstream vendor's price under the bare id. An
        # APPROXIMATION — it does not carry the reseller's margin — but it is
        # the right order of magnitude, which _DEFAULT is not.
        bare = model.split('/', 1)[1]
        stripped = _OVERRIDES.get(bare)
        if stripped is None:
            stripped = _from_litellm(bare)
        if stripped is not None:
            logger.info('pricing.vendor_prefix_stripped', model=model, priced_as=bare)
            return stripped
    logger.warning('pricing.model_not_found', model=model)
    return _DEFAULT


def cache_read_rate(p: Price) -> float:
    """What a cache-read token costs, and the INPUT rate when we do not know.

    ``Price.cache_read`` defaults to 0.0, which reads as "this model has no
    prompt cache" and prices as FREE. Those are not the same statement, and the
    difference became load-bearing with #3134: DeepSeek serves 100% of
    production and its ``prompt_cache_hit_tokens`` were never read, so the
    tokens were all counted as ``input`` and priced at the full input rate. Now
    that they are read, an unpinned ``cache_read`` of 0.0 would move them into
    a category that costs nothing and the booked figure would DROP — not
    because anything got cheaper, but because we started reading a field.

    A cache read is never free. So an unpinned rate falls back to the input
    rate: the cost number does not move until somebody pins the real discount
    against an invoice, and when they do it moves DOWN, with a reason. The
    metric becomes visible without the bill changing underneath it, which is
    the whole point of separating the two halves of that issue.
    """
    return p.cache_read or p.input


def cost_usd(model: str, usage: TokenUsage) -> float:
    """USD cost for one call's token usage, summed across token categories."""
    p = get_price(model)
    return (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_read_tokens * cache_read_rate(p)
        + usage.cache_creation_tokens * p.cache_creation
    ) / 1_000_000
