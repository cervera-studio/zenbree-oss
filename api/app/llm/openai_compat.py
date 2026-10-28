"""Base class for OpenAI-compatible LLM providers (DeepSeek, Azure Foundry, etc.)."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from app.llm import pricing
from app.llm._per_loop_client import PerLoopClientMixin
from app.llm.base import (
    LLMEvent,
    LLMProvider,
    LLMResponse,
    ProviderAPIError,
    ProviderRateLimitError,
    TokenUsage,
    ToolCall,
)
from app.llm.tool_arguments import parsed_tool_call

logger = structlog.get_logger()


@dataclass(slots=True)
class _StreamingToolCall:
    """One tool call being assembled from ``tool_calls`` deltas.

    ``input`` stays None until the arguments parse; from then on the call is
    closed — its ``tool_end`` is already out and late deltas are refused.
    """

    id: str = ''  # mirrors the wire field
    name: str = ''
    args: str = ''
    input: dict | None = None  # mirrors ToolCall.input


def _fallback_tool_id() -> str:
    """Id for a tool call the provider streamed without one.

    A uuid, not ``call_{index}``: the index restarts at 0 every turn, so a
    positional id would collide across turns and false-positive the session's
    charged-tool set.
    """
    return f'call_{uuid.uuid4().hex[:16]}'


def _complete_tool_arguments(raw: str) -> dict | None:
    """The arguments object once ``raw`` holds all of it, else ``None``.

    A brace-balanced prefix of a JSON object IS the whole object, so the first
    successful parse marks the end of that tool call's arguments — which is how
    the stream can close a call before the response ends (#1515). Anything that
    is not an object (a provider streaming a bare scalar) stays open and falls
    through to the end-of-stream finalizer, which keeps the legacy shape.

    Arguments arrive a few characters at a time, so this runs on every delta:
    the closing-brace check keeps that O(n) in the buffer rather than a full
    re-parse per chunk.
    """
    if not raw.rstrip().endswith('}'):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _system_to_str(system: str | list[dict]) -> str:
    if isinstance(system, str):
        return system
    return '\n'.join(b.get('text', '') for b in system if b.get('type') == 'text')


def _convert_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    converted = []
    for tool in tools:
        if tool.get('type', '').startswith('web_search'):
            continue
        if 'function' in tool:
            converted.append(tool)
        else:
            converted.append(
                {
                    'type': 'function',
                    'function': {
                        'name': tool.get('name', ''),
                        'description': tool.get('description', ''),
                        'parameters': tool.get('input_schema', {}),
                    },
                }
            )
    return converted or None


def _convert_tool_choice(tool_choice: object) -> object | None:
    """Translate an Anthropic-shaped ``tool_choice`` into the OpenAI grammar.

    An Anthropic-first caller (the KG merge arbiter, and any future structured-
    output path) passes Anthropic's shapes — ``{'type': 'tool', 'name': X}`` or
    ``{'type': 'any'|'auto'|'none'}`` — but OpenAI-compatible endpoints want a
    bare ``'none'|'auto'|'required'`` or ``{'type': 'function', 'function':
    {'name': X}}``. Without this translation the forced tool is silently dropped
    on every fallback provider (DeepSeek/Azure-Foundry/…): the model answers in
    prose, ``tool_calls`` comes back empty, and the caller reads that as "no
    decision" exactly when the fallback chain was supposed to keep the tool
    alive. Values that are already OpenAI-shaped (a bare string, or a
    ``function``-shaped dict) pass through untouched.
    """
    if not tool_choice:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        passthrough: object = tool_choice  # already OpenAI-shaped
        kind = str(tool_choice.get('type') or '')
        if 'function' in tool_choice or kind == 'function':
            return passthrough
        if kind == 'tool':
            name = str(tool_choice.get('name') or '')
            return {'type': 'function', 'function': {'name': name}} if name else 'required'
        if kind == 'any':
            return 'required'
        if kind in ('auto', 'none'):
            return kind
    return None


def _convert_messages(messages: list[dict]) -> list[dict]:
    converted = []
    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if isinstance(content, str):
            converted.append({'role': role, 'content': content})
            continue
        if not isinstance(content, list):
            converted.append({'role': role, 'content': str(content)})
            continue
        if role == 'assistant':
            text_parts = []
            tool_calls = []
            for block in content:
                btype = block.get('type', '')
                if btype == 'text':
                    text_parts.append(block.get('text', ''))
                elif btype == 'tool_use':
                    tool_calls.append(
                        {
                            'id': block.get('id', ''),
                            'type': 'function',
                            'function': {
                                'name': block.get('name', ''),
                                'arguments': json.dumps(block.get('input', {})),
                            },
                        }
                    )
            oai_msg: dict = {'role': 'assistant'}
            if text_parts:
                oai_msg['content'] = ''.join(text_parts)
            if tool_calls:
                oai_msg['tool_calls'] = tool_calls
            converted.append(oai_msg)
        elif role == 'user':
            text_parts = []
            tool_results = []
            for block in content:
                btype = block.get('type', '')
                if btype == 'text':
                    text_parts.append(block.get('text', ''))
                elif btype == 'tool_result':
                    tool_results.append(
                        {
                            'role': 'tool',
                            'tool_call_id': block.get('tool_use_id', ''),
                            'content': block.get('content', ''),
                        }
                    )
                else:
                    text_parts.append(str(block))
            converted.extend(tool_results)
            if text_parts:
                converted.append({'role': 'user', 'content': ''.join(text_parts)})
        else:
            converted.append({'role': role, 'content': str(content)})
    return converted


# ── Text tool-call fallback (DeepSeek) ────────────────────────────────────────
#
# DeepSeek (and other open models contaminated by Anthropic transcripts) some-
# times emit a tool call as TEXT in the ``content`` field instead of via the
# native ``tool_calls`` field. Two dialects seen on staging:
#   DeepSeek "DSML": <｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="web_search">
#                    <｜｜DSML｜｜parameter name="query" string="true">VALUE</｜｜DSML｜｜parameter>...
#   Anthropic-style: <function_calls><invoke name="web_search">
#                    <parameter name="query">VALUE</parameter>...</invoke></function_calls>
# Neither is a format we define (a grep of the codebase finds zero), so the
# call never executes and the markup leaks into the UI while the turn is
# wasted. The leaked ``name`` is the real, dispatchable tool name (the t_<hex>
# obfuscation is display-only), so parsing it yields a usable ToolCall.
# Opt-in per provider via ``text_tool_call_fallback`` so the prod gpt/grok/
# Foundry paths are byte-for-byte unchanged.

# DeepSeek decorates the markup with fullwidth-bar markers around "DSML".
# The bar count is not stable across model versions: older builds emit a
# double bar (｜｜DSML｜｜), deepseek-v4-flash emits a single bar (｜DSML｜).
# Match one-or-more bars so both are stripped/detected (staging session
# e5b13bfa, 2026-06-24: a single-bar block leaked to the user because the
# parser only knew the double-bar form). ASCII pipe (U+007C) is a different
# codepoint from the fullwidth bar (U+FF5C), so this never matches prose.
_DSML_DECORATION_RE = re.compile(r'｜+DSML｜+')

# Earliest opener marker → where the leaked block begins in the raw text.
# Require structural follow-through (the wrapper's closing ``>`` or an
# ``invoke name="``) so prose that merely mentions ``<invoke`` or
# ``<function_calls`` (e.g. the model explaining tool syntax) does not freeze
# the stream. Note the wrapper closer never has attributes, so ``>`` is exact.
_TOOL_CALL_OPENER_RE = re.compile(r'<(?:｜+DSML｜+)?(?:function_calls|tool_calls)>|<(?:｜+DSML｜+)?invoke\s+name="')
# Closers, so a block that ended can be stepped over instead of freezing the
# turn. Either wrapper closer ends a wrapper block, so a mismatched pair
# (``<function_calls>`` closed with ``</tool_calls>``) still terminates it.
_WRAPPER_CLOSER_RE = re.compile(r'</(?:｜+DSML｜+)?(?:function_calls|tool_calls)>')
_INVOKE_CLOSER_RE = re.compile(r'</(?:｜+DSML｜+)?invoke>')
# A parameter value is opaque: a closer written inside one is data, not a
# boundary. The attribute blob is captured because the parameter's own NAME is
# part of how its end is recognised — see ``_param_closer``.
_PARAM_OPEN_RE = re.compile(r'<(?:｜+DSML｜+)?parameter\s+([^>]*)>')
_PARAM_CLOSE_RE = re.compile(r'</(?:｜+DSML｜+)?parameter>')
# Must exceed the longest opener token so a marker split across stream deltas
# is held back until the regex can match it (``<｜｜DSML｜｜function_calls>`` is
# 24 chars). A trailing ``<`` older than this never completed an opener, so it
# is prose and gets released.
_OPENER_MAX_LEN = 32

# Max seconds without a ``data:`` frame before the stream is declared stalled (#1119).
# httpx's read timeout only covers total silence; keepalive comment lines reset it
# while carrying no tokens, so a stalled-but-pinging upstream needs this deadline.
_STREAM_IDLE_DEADLINE_S = 120.0
_INVOKE_RE = re.compile(r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>', re.DOTALL)
# Pull ``name`` out of the attribute blob rather than assuming attribute order
# — DeepSeek/Anthropic usually emit name first, but a reordered
# ``<parameter string="false" name="x">`` must not silently drop x.
_PARAM_NAME_RE = re.compile(r'name="([^"]+)"')


def _param_closer(attrs: str) -> re.Pattern[str]:
    """What ends the parameter opened with ``attrs``.

    ``</parameter>`` is the form both dialects document, and it used to be the
    only one recognised. deepseek-v4-flash also closes a parameter with a tag
    named after the parameter itself — ``</preamble>`` for
    ``<parameter name="preamble">`` — and mixes the two inside one call.

    That is not a cosmetic difference. The scan for the end of a value is
    non-greedy, so an unrecognised closer does not fail: it runs on to the
    NEXT parameter's ``</parameter>``, swallowing that parameter's opener and
    value into this one. Prod session 75a5e104 (2026-09-03) is exactly this:
    a ``plan`` call arrived with ``steps`` missing and its markup sitting
    inside ``preamble`` as
    ``"...primary sources.</preamble>\\n<parameter name=\\"steps\\"...>[{...}]"``,
    the handler answered ``plan_not_shown`` because no step carried a
    sentence, and the run went on to spend credits with no plan on screen.

    So the name-tagged form is accepted as a boundary too, and only for the
    name this parameter was opened with: an arbitrary ``</foo>`` stays data,
    but its own name is a boundary only before the next parameter, the end
    of the enclosing call, or the end of the available input. A literal
    ``</html>`` followed by ``</parameter>`` or more value text remains data.
    This preserves canonical HTML, CDATA and JSON values without swallowing
    the next argument in the mixed dialect. Markup that itself imitates a
    complete parameter boundary remains ambiguous in this text protocol.
    """
    name = _PARAM_NAME_RE.search(attrs)
    if name is None:
        return _PARAM_CLOSE_RE
    return re.compile(
        rf'</(?:｜+DSML｜+)?parameter>|'
        rf'</(?:｜+DSML｜+)?{re.escape(name.group(1))}>'
        rf'(?=\s*(?:<(?:｜+DSML｜+)?parameter\b|'
        rf'</(?:｜+DSML｜+)?(?:invoke|function_calls|tool_calls)>|$))',
    )


def _coerce_param(value: str, attrs: str) -> object:
    """Coerce a parsed parameter value using the ``string="..."`` type hint.

    The models copy Anthropic's ``string="false"`` attribute to mark a non-
    string value (e.g. ``num=10``); decode those as JSON so they reach the
    handler as int/bool/list rather than the literal string. Plain strings
    (no hint, or ``string="true"``) are returned verbatim.
    """
    value = value.strip()
    if 'string="false"' in attrs:
        try:
            return json.loads(value)
        except json.JSONDecodeError, ValueError:
            return value
    return value


def _parse_params(body: str) -> dict:
    """Extract ``{name: value}`` from an <invoke> body, attribute-order-agnostic.

    Scanned parameter by parameter rather than with one global regex, because
    each value's end depends on the name its own opener carried
    (:func:`_param_closer`). A parameter whose closer never arrives ends the
    scan: what follows it is inside its value as far as anything here can
    tell, and guessing otherwise is how the value of one argument became the
    markup of the next.
    """
    params: dict = {}
    pos = 0
    while True:
        opener = _PARAM_OPEN_RE.search(body, pos)
        if opener is None:
            return params
        attrs = opener.group(1)
        closed = _param_closer(attrs).search(body, opener.end())
        if closed is None:
            return params
        name = _PARAM_NAME_RE.search(attrs)
        if name:
            params[name.group(1)] = _coerce_param(
                body[opener.end() : closed.start()],
                attrs,
            )
        pos = closed.end()


def _block_end(text: str, start: int, closer: re.Pattern[str]) -> int | None:
    """End of the markup block open at ``start``, or None while it is unknown.

    Scans left to right treating ``<parameter …>…</parameter>`` as opaque, so a
    closer written inside a parameter value is not mistaken for the boundary.
    The answer is read from the prefix alone and never revised as the stream
    grows, which is what lets the emission cursor act on it.
    """
    i = start
    while True:
        param = _PARAM_OPEN_RE.search(text, i)
        end = closer.search(text, i)
        if end is not None and (param is None or end.start() < param.start()):
            return end.end()
        if param is None:
            return None
        closed = _param_closer(param.group(1)).search(text, param.end())
        if closed is None:
            return None
        i = closed.end()


def _split_markup(text: str) -> tuple[str, int]:
    """Split ``text`` into prose and leaked tool-call markup.

    Returns ``(prose, safe_len)``: ``prose`` is ``text`` with every block whose
    closer has arrived removed, and ``safe_len`` is how much of it cannot still
    turn out to be markup. They differ only while an opener is still waiting for
    its closer, and then the tail after that opener is what is withheld.
    """
    kept: list[str] = []
    cursor = 0
    pos = 0
    while True:
        opener = _TOOL_CALL_OPENER_RE.search(text, pos)
        if opener is None:
            frozen_at = len(text)
            break
        closer = _INVOKE_CLOSER_RE if 'invoke' in opener.group(0) else _WRAPPER_CLOSER_RE
        end = _block_end(text, opener.end(), closer)
        if end is None:
            frozen_at = opener.start()
            break
        kept.append(text[cursor : opener.start()])
        cursor = pos = end
    prose = ''.join(kept)
    return prose + text[cursor:], len(prose) + (frozen_at - cursor)


def _parse_text_tool_calls(text: str) -> tuple[list[ToolCall], str]:
    """Extract leaked text tool-call markup into ToolCalls.

    Returns ``(tool_calls, cleaned_text)`` where ``cleaned_text`` is ``text``
    with the markup blocks stripped (prose before, between and after them is
    kept). Returns ``([], text)`` when no markup is present.
    """
    prose, safe_len = _split_markup(text)
    if safe_len == len(text):
        return [], text
    norm = _DSML_DECORATION_RE.sub('', text)
    calls = [
        ToolCall(
            id=f'call_text_{uuid.uuid4().hex[:16]}',
            name=name,
            input=_parse_params(body),
        )
        for name, body in _INVOKE_RE.findall(norm)
    ]
    # Cut at ``safe_len`` so a block still waiting for its closer when the
    # stream ends is dropped, not shipped: a truncated/malformed block (cut off
    # at max_tokens) is still leaked markup, not prose.
    return calls, prose[:safe_len].rstrip()


def _usage_from(raw: dict) -> TokenUsage:
    """Map an OpenAI-compatible ``usage`` object onto :class:`TokenUsage`.

    Until #3134 this read two fields — ``prompt_tokens`` and
    ``completion_tokens`` — and nothing else, which is why ``cache_read`` was
    zero for every DeepSeek turn ever metered: on 17/09 production ran 90 turns
    and 13 284 900 input tokens through ``deepseek-v4-flash`` with
    ``cache_read: 0``, and that zero did NOT mean "the cache did not hit". It
    meant we were not looking at the field.

    Two shapes are read, both optional, and a provider that sends neither ends
    up exactly where it is today:

    * **DeepSeek** — ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
      (api-docs.deepseek.com/guides/kv_cache). ``prompt_tokens`` is their sum.
    * **OpenAI / Azure** — ``prompt_tokens_details.cached_tokens``, which is
      the cached part OF ``prompt_tokens`` and has no "miss" counterpart.

    The split matters because ``TokenUsage`` follows Anthropic's convention,
    where ``input_tokens`` EXCLUDES what was read from cache and
    ``cost_usd`` prices the two categories separately. Feeding
    ``prompt_tokens`` into ``input_tokens`` while also reporting the hits would
    bill the cached prefix twice, so the miss count is what ``input_tokens``
    gets — taken from the provider's own field when it sends one, and derived
    by subtraction when it does not.

    Defensive about what comes back, in ONE direction. Every counter this
    returns is a subset of ``prompt_tokens``, so the hit count is clamped to it
    and the remainder floors at zero: the three categories can under-count the
    prompt, never exceed it. That asymmetry is the house rule — ``router._partial``
    states it as "an over-book throttles a legitimate user; an under-book only
    under-counts".
    """
    prompt = _int(raw.get('prompt_tokens'))
    completion = _int(raw.get('completion_tokens'))

    details = raw.get('prompt_tokens_details')
    cached = _int(details.get('cached_tokens')) if isinstance(details, dict) else 0
    # Clamped to ``prompt_tokens``: see the test named for this line. A hit
    # count larger than the prompt is a provider bug, and passing it through
    # bills tokens that were never sent.
    hit = min(_int(raw.get('prompt_cache_hit_tokens')) or cached, prompt)
    if not hit:
        return TokenUsage(input_tokens=prompt, output_tokens=completion)

    miss = raw.get('prompt_cache_miss_tokens')
    uncached = _int(miss) if miss is not None else max(prompt - hit, 0)
    # Trust the TOTAL over the parts when the two disagree. ``prompt_tokens``
    # is the number the provider bills on, and ``cost_usd`` sums the categories
    # this returns — so a pair that does not add up to it would inflate or
    # deflate the charge by the difference, silently. Recovered from the
    # duplicate PR #29, which spelled this case out.
    if hit + uncached != prompt:
        uncached = max(prompt - hit, 0)
    return TokenUsage(
        input_tokens=uncached,
        output_tokens=completion,
        cache_read_tokens=hit,
    )


def _int(value: object) -> int:
    """A usage counter, or 0 for anything that is not one."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class OpenAICompatibleProvider(LLMProvider, PerLoopClientMixin[httpx.AsyncClient]):
    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        default_model: str,
        max_tokens_param: str = 'max_tokens',
        chat_path: str = '/chat/completions',
        default_temperature: float | None = None,
        extra_body: dict | None = None,
        system_prefix: str = '',
        text_tool_call_fallback: bool = False,
    ) -> None:
        self._default_model = default_model
        self._max_tokens_param = max_tokens_param
        self._chat_path = chat_path
        self._default_temperature = default_temperature
        self._extra_body = extra_body or {}
        self._system_prefix = system_prefix
        # When True, a turn that emits no native tool_calls but leaks tool-call
        # markup as text is salvaged into real tool calls (DeepSeek opt-in).
        self._text_tool_call_fallback = text_tool_call_fallback
        # ``base_url`` / ``headers`` are kept so ``_build_client`` can build a
        # fresh client for off-loop callers (see PerLoopClientMixin).
        self._base_url = base_url
        self._headers = headers
        self._init_per_loop_client()

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
        )

    def _per_call_body(self) -> dict:
        """Body fields that vary per call (``extra_body`` is fixed at init)."""
        return {}

    def _set_temperature(self, body: dict, kwargs: dict) -> None:
        """Sampling temperature, seam-normalised (see ``base.DEFAULT_TEMPERATURE``).

        ``kwargs['temperature']`` is always present unless the subclass narrows
        ``sampling_params`` — Moonshot does, because the hosted Kimi endpoint
        pins the value by vendor contract and 400s on anything else. That case
        falls back to ``_default_temperature``, which is how the vendor-pinned
        value gets sent."""
        temperature = kwargs.pop('temperature', None)
        if temperature is None:
            temperature = self._default_temperature
        if temperature is not None:
            body['temperature'] = temperature

    def _set_response_format(self, body: dict, kwargs: dict) -> None:
        """The JSON grade the seam already narrowed to what we honour.

        Absent from ``kwargs`` unless a call site asked for one AND this
        provider declares it for this call (``_json_output_modes``), so an
        unsupported grade can never reach the wire and turn into a 400. It is
        the same contract ``_apply_sampling`` gives the sampling params.
        """
        response_format = kwargs.pop('response_format', None)
        if response_format is not None:
            body['response_format'] = response_format

    async def _chat(self, *, system, messages, tools=None, max_tokens=8192, **kwargs) -> LLMResponse:
        model = self.effective_model(str(kwargs.pop('model', None) or ''))
        sys_str = _system_to_str(system)
        if self._system_prefix:
            sys_str = f'{self._system_prefix}\n\n{sys_str}' if sys_str else self._system_prefix
        body: dict = {
            'model': model,
            self._max_tokens_param: max_tokens,
            'messages': [{'role': 'system', 'content': sys_str}, *_convert_messages(messages)],
        }
        self._set_temperature(body, kwargs)
        self._set_response_format(body, kwargs)
        for k, v in self._extra_body.items():
            body.setdefault(k, v)
        body.update(self._per_call_body())
        oai_tools = _convert_tools(tools)
        if oai_tools:
            body['tools'] = oai_tools
            # Forward tool_choice, translated to OpenAI grammar. The arbiter
            # passes Anthropic's {'type':'tool','name':X}; without this the
            # forced tool is dropped on every non-Anthropic provider and the
            # decision degrades to prose the caller can't parse.
            oai_tool_choice = _convert_tool_choice(kwargs.get('tool_choice'))
            if oai_tool_choice is not None:
                body['tool_choice'] = oai_tool_choice

        resp = await self._active_client().post(self._chat_path, json=body)
        status = getattr(resp, 'status_code', 200)
        if status == 429:
            raise ProviderRateLimitError(resp.text, wait_seconds=30)
        if isinstance(status, int) and status >= 400:
            raise ProviderAPIError(resp.text, status_code=status, raw_message=resp.text)
        data = resp.json()
        choice = data['choices'][0]
        message = choice['message']
        tool_calls = []
        for tc in message.get('tool_calls') or []:
            fn = tc.get('function', {})
            try:
                args = json.loads(fn.get('arguments', '{}'))
            except json.JSONDecodeError, ValueError:
                args = {}
            tool_calls.append(parsed_tool_call(tc.get('id', ''), fn.get('name', ''), args))
        text = message.get('content') or ''
        stop_reason = choice.get('finish_reason', '')
        if self._text_tool_call_fallback:
            parsed, text = _parse_text_tool_calls(text)
            if parsed and not tool_calls:
                tool_calls, stop_reason = parsed, 'tool_calls'
        raw_usage = data.get('usage') or {}
        usage = _usage_from(raw_usage)
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            cost_usd=pricing.cost_usd(model, usage),
            model=model,
            stop_reason=stop_reason,
        )

    def effective_model(self, requested: str = '') -> str:
        # An incompatible model (e.g. claude-* to Azure) falls back to our own.
        model = requested or self._default_model
        if model.startswith('claude-') or model.startswith('anthropic'):
            return self._default_model
        return model

    async def _chat_stream(
        self, *, system, messages, tools=None, max_tokens=8192, **kwargs
    ) -> AsyncGenerator[LLMEvent]:
        model = self.effective_model(str(kwargs.pop('model', None) or ''))
        body: dict = {
            'model': model,
            self._max_tokens_param: max_tokens,
            'stream': True,
            # Without `include_usage`, OpenAI-compatible streaming endpoints
            # (Azure Foundry, DeepSeek, fireworks, etc.) do NOT send token
            # counts in the stream — only the non-streaming call does. That
            # meant every LLMCall row we emitted for Azure had zero tokens
            # and zero cost, silently under-reporting provider spend in the
            # cost leaderboard (verified 2026-04-20: 197/197 gpt-5.4-mini
            # rows in April had input=0/output=0/cost=0 while the Azure
            # billing export showed ~€0.65 of actual usage).
            'stream_options': {'include_usage': True},
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        f'{self._system_prefix}\n\n{_system_to_str(system)}'
                        if self._system_prefix
                        else _system_to_str(system)
                    ),
                },
                *_convert_messages(messages),
            ],
        }
        self._set_temperature(body, kwargs)
        self._set_response_format(body, kwargs)
        for k, v in self._extra_body.items():
            body.setdefault(k, v)
        body.update(self._per_call_body())
        oai_tools = _convert_tools(tools)
        # Forced-synthesis lever: the agent loop passes tool_choice='none'
        # to make DeepSeek stop calling tools and write the final answer
        # (OpenAI-compatible values: 'none' / 'auto' / 'required'). Only
        # meaningful alongside tools; omit otherwise so 'auto' stays the
        # provider default. Read outside the ``if`` because the text-tool-call
        # fallback below must also honour it on the way OUT of the stream.
        tool_choice = _convert_tool_choice(kwargs.get('tool_choice'))
        if oai_tools:
            body['tools'] = oai_tools
            if tool_choice is not None:
                body['tool_choice'] = tool_choice

        # Accumulate tool calls keyed by their stream ``index`` so a turn that
        # emits MORE THAN ONE tool call (parallel/batched, as Claude does and
        # as we want DeepSeek to do) keeps all of them — the previous single
        # ``current_tool_*`` form silently dropped every call but the last.
        tools_by_index: dict[int, _StreamingToolCall] = {}
        total_input = 0
        total_output = 0
        total_cache_read = 0
        _stop_reason = ''
        text_parts: list[str] = []
        tool_calls_acc: list[dict] = []
        # Cursor into the joined text already streamed to the UI. Only used by
        # the text-tool-call fallback (DeepSeek): it holds back any trailing
        # ``<...`` that could be the start of leaked markup so the markup is
        # never shown. Unused (and emission stays inline) when the flag is off.
        emitted_len = 0

        async with self._active_client().stream('POST', self._chat_path, json=body) as resp:
            if resp.status_code == 429:
                raise ProviderRateLimitError('Rate limited', wait_seconds=30)
            if resp.status_code >= 400:
                error_body = ''
                async for chunk in resp.aiter_text():
                    error_body += chunk
                raise ProviderAPIError(error_body, status_code=resp.status_code, raw_message=error_body)

            last_data_at = time.monotonic()
            async for line in resp.aiter_lines():
                if not line.startswith('data: '):
                    if time.monotonic() - last_data_at > _STREAM_IDLE_DEADLINE_S:
                        raise ProviderAPIError(
                            f'stream stalled: no data frames for {int(_STREAM_IDLE_DEADLINE_S)}s',
                            status_code=504,
                        )
                    continue
                last_data_at = time.monotonic()
                payload = line[6:].strip()
                if payload == '[DONE]':
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError, ValueError:
                    continue
                if chunk.get('usage'):
                    # Same mapping as the non-streaming branch, so a cache hit
                    # is visible on the path production actually takes (#3134).
                    stream_usage = _usage_from(chunk['usage'])
                    total_input = stream_usage.input_tokens
                    total_output = stream_usage.output_tokens
                    total_cache_read = stream_usage.cache_read_tokens
                choices = chunk.get('choices') or []
                if not choices:
                    continue  # Azure sends prompt_filter_results with empty choices
                delta = choices[0].get('delta', {})
                finish = choices[0].get('finish_reason')
                if delta.get('content'):
                    text_parts.append(delta['content'])
                    if not self._text_tool_call_fallback:
                        yield LLMEvent(type='text_delta', text=delta['content'])
                    else:
                        # Emit only prose that can't still turn out to be leaked
                        # markup: a block whose closer has arrived is stepped
                        # over, one still waiting for it holds the tail back.
                        prose, safe_upto = _split_markup(''.join(text_parts))
                        if safe_upto == len(prose):
                            # No block pending, so hold short of any trailing
                            # ``<``: it might begin an opener split across deltas.
                            lt = prose.rfind('<')
                            if lt >= emitted_len and lt >= len(prose) - _OPENER_MAX_LEN:
                                safe_upto = lt
                        if safe_upto > emitted_len:
                            yield LLMEvent(type='text_delta', text=prose[emitted_len:safe_upto])
                            emitted_len = safe_upto
                for tc_delta in delta.get('tool_calls') or []:
                    idx = tc_delta.get('index', 0)
                    entry = tools_by_index.setdefault(idx, _StreamingToolCall())
                    if entry.input is not None:
                        # This index is closed: its ``tool_end`` is out and the
                        # loop may already be dispatching it, so nothing here
                        # can still change its id, name or input without the
                        # event and the serialized block disagreeing. Refuse
                        # the delta rather than corrupt the call. Two ways to
                        # land here, and the log is how you tell them apart:
                        # a provider that reopens an index (the wire format
                        # streams one to completion before moving on), or one
                        # that omits ``index`` entirely — then every call after
                        # the first collapses onto the default 0 and IS
                        # DROPPED, so a run of these means a provider is
                        # losing tool calls, not glitching once.
                        logger.warning(
                            'openai_compat.tool_delta_after_close',
                            index=idx,
                            tool_name=entry.name,
                        )
                        continue
                    if tc_delta.get('id'):
                        entry.id = tc_delta['id']
                    fn = tc_delta.get('function', {})
                    if fn.get('name'):
                        entry.name = fn['name']
                        yield LLMEvent(type='tool_start', tool_call=ToolCall(id=entry.id, name=entry.name, input={}))
                    if fn.get('arguments'):
                        entry.args += fn['arguments']
                        yield LLMEvent(type='tool_delta', text=fn['arguments'])
                        parsed = _complete_tool_arguments(entry.args)
                        if parsed is not None and entry.name:
                            entry.input = parsed
                            entry.id = entry.id or _fallback_tool_id()
                            yield LLMEvent(
                                type='tool_end',
                                tool_call=ToolCall(
                                    id=entry.id,
                                    name=entry.name,
                                    input=parsed,
                                ),
                            )
                if finish:
                    _stop_reason = finish

        # Finalize native tool calls once the stream is fully read, regardless
        # of finish_reason: some openai-compat providers send 'stop' (not
        # 'tool_calls') alongside structured tool-call deltas, and gating on
        # 'tool_calls' would silently drop them.
        for i in sorted(tools_by_index):
            entry = tools_by_index[i]
            if not entry.name:
                continue
            if entry.input is not None:
                parsed = entry.input
            else:
                try:
                    parsed = json.loads(entry.args or '{}')
                except json.JSONDecodeError, ValueError:
                    parsed = {}
            call = parsed_tool_call(entry.id or _fallback_tool_id(), entry.name, parsed)
            if entry.input is None:
                yield LLMEvent(type='tool_end', tool_call=call)
            tool_calls_acc.append({'type': 'tool_use', 'id': call.id, 'name': entry.name, 'input': call.input})
        if tool_calls_acc:
            _stop_reason = 'tool_calls'

        final_text = ''.join(text_parts)
        if self._text_tool_call_fallback:
            # Always strip, even when the turn carries native tool calls: this
            # branch used to be skipped for them and the flush below shipped the
            # raw block to the UI, which is what the guard exists to prevent.
            parsed_calls, final_text = _parse_text_tool_calls(final_text)
            # Under tool_choice='none' the API blocks native tool calls,
            # but v4-flash mid-plan writes them as DSML markup instead —
            # re-materialising them here would undo the forced-synthesis
            # turn (prod session 3a4f62b2, 2026-07-06: the 'none' turn
            # still dispatched 5 tools this way). Strip the markup from
            # the text but DROP the calls; the agent loop's salvage/
            # breaker handles the now-textless turn.
            if tool_calls_acc or tool_choice == 'none':
                parsed_calls = []
            for tc in parsed_calls:
                yield LLMEvent(type='tool_start', tool_call=ToolCall(id=tc.id, name=tc.name, input={}))
                yield LLMEvent(type='tool_end', tool_call=tc)
                tool_calls_acc.append({'type': 'tool_use', 'id': tc.id, 'name': tc.name, 'input': tc.input})
            if parsed_calls:
                _stop_reason = 'tool_calls'
            # Drop a truncated DSML opener left dangling at the end: when the
            # stream is cut (e.g. max_tokens) mid-marker, the opener never
            # completes so _parse_text_tool_calls can't consume it, and the
            # held-back tail would otherwise leak raw markup. The fullwidth bar
            # U+FF5C never appears in prose, so a trailing ``<…｜…`` fragment is
            # a partial tool-call marker, not content — withhold it.
            lt = final_text.rfind('<')
            if lt >= emitted_len and '｜' in final_text[lt:]:
                final_text = final_text[:lt]
            # Flush any text held back by the markup guard (the trailing ``<``
            # tail, or — when no markup followed — the rest of a clean reply).
            if len(final_text) > emitted_len:
                yield LLMEvent(type='text_delta', text=final_text[emitted_len:])

        usage = TokenUsage(
            input_tokens=total_input,
            output_tokens=total_output,
            cache_read_tokens=total_cache_read,
        )
        serialized = []
        if final_text:
            serialized.append({'type': 'text', 'text': final_text})
        serialized.extend(tool_calls_acc)
        yield LLMEvent(
            type='done',
            usage=usage,
            stop_reason=_stop_reason,
            cost_usd=pricing.cost_usd(model, usage),
            model=model,
            text=json.dumps(serialized),
        )

    def supports_prompt_caching(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return True
