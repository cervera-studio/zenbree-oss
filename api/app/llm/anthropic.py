"""Anthropic LLM provider — primary. Sonnet + Haiku with prompt caching."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import anthropic

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from anthropic.lib.streaming import AsyncMessageStreamManager

from app.llm import pricing
from app.llm._per_loop_client import PerLoopClientMixin
from app.llm.base import (
    LLMEvent,
    LLMProvider,
    LLMResponse,
    ProviderAPIError,
    ProviderRateLimitError,
    ServerToolUse,
    TokenUsage,
    ToolCall,
    WebSearchResult,
)
from app.llm.tool_arguments import parsed_tool_call


def _harvest_web_search_citations(content: list) -> list[dict]:
    """Pull plaintext ``cited_text`` spans from a message's text blocks.

    When the model answers using the server-side ``web_search`` tool,
    each ``text`` block carries a ``citations`` list of
    ``web_search_result_location`` items. Unlike the
    ``web_search_tool_result`` block body (opaque ``encrypted_content``),
    the citation's ``cited_text`` is the **plaintext span the model
    quoted** — exactly the grounding truth set the synthesis check needs.

    Returns ``[{'url', 'title', 'cited_text'}, ...]``; tolerant of SDK
    shape drift (missing fields → skipped) so a citation-schema change
    can never break the provider call.
    """
    out: list[dict] = []
    for block in content or []:
        if getattr(block, 'type', None) != 'text':
            continue
        for cit in getattr(block, 'citations', None) or []:
            cited_text = getattr(cit, 'cited_text', None)
            url = getattr(cit, 'url', None)
            if isinstance(cited_text, str) and cited_text.strip() and url:
                out.append(
                    {
                        'url': url,
                        'title': getattr(cit, 'title', '') or '',
                        'cited_text': cited_text,
                    }
                )
    return out


def system_with_cache(text: str) -> list[dict]:
    """Wrap a plain system-prompt string as Anthropic's text-block list with a ``cache_control``
    breakpoint, so ``tools`` and ``system`` cache together across the steps of an agent turn.

    Every string ``system`` the provider sends goes through it. A prompt below the model's minimum
    cacheable length is not cached and costs nothing extra.
    """
    return [
        {
            'type': 'text',
            'text': text,
            'cache_control': {'type': 'ephemeral'},
        },
    ]


class AnthropicProvider(LLMProvider, PerLoopClientMixin[anthropic.AsyncAnthropic]):
    def __init__(self) -> None:
        from app.core.config import settings
        from app.llm.gateway import gateway_auth_header, gateway_base_url

        # Route through the CF ``/anthropic`` endpoint when the gateway is on.
        # It proxies the native Messages API (SDK appends ``/v1/messages``), so
        # prompt caching + server web_search + citations all pass through — the
        # OpenAI-compat gateway path would drop them. Native client when off.
        # Kept as kwargs (not a one-shot ``self._client``) so ``_build_client``
        # can mint a fresh per-loop client for off-loop callers — e.g. the KG
        # resolution path's per-entity ``asyncio.run`` (see PerLoopClientMixin).
        self._client_kwargs: dict = {'api_key': settings.anthropic_api_key}
        gw = gateway_base_url('anthropic')
        if gw:
            self._client_kwargs['base_url'] = gw
            self._client_kwargs['default_headers'] = gateway_auth_header()
        self._init_per_loop_client()
        # ``settings.llm_model`` carries a litellm-style ``anthropic/`` prefix
        # (ScrapegraphAI / litellm require it), but the Anthropic SDK wants the
        # bare model id and 404s on ``model: anthropic/claude-sonnet-4-6``.
        self._default_model = (settings.llm_model or 'claude-sonnet-4-6').removeprefix('anthropic/')

    def _build_client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(**self._client_kwargs)

    async def _chat(self, *, system, messages, tools=None, max_tokens=8192, **kwargs) -> LLMResponse:
        model = self.effective_model(str(kwargs.pop('model', None) or ''))

        params: dict = {
            'model': model,
            'max_tokens': max_tokens,
            'system': system_with_cache(system) if isinstance(system, str) and system else system,
            'messages': messages,
        }
        if tools:
            params['tools'] = tools
        params.update(kwargs)

        try:
            response = await self._active_client().messages.create(**params)
        except anthropic.RateLimitError as e:
            raise ProviderRateLimitError(str(e), wait_seconds=30) from e
        except anthropic.APIError as e:
            raise ProviderAPIError(
                str(e),
                status_code=getattr(e, 'status_code', None),
                raw_message=str(e),
            ) from e

        text_parts = []
        tool_calls = []
        server_tool_uses = []
        web_search_results = []
        for block in response.content:
            if block.type == 'text':
                text_parts.append(block.text)
            elif block.type == 'tool_use':
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
            elif block.type == 'server_tool_use':
                server_tool_uses.append(
                    ServerToolUse(
                        id=block.id,
                        name=block.name,
                        input=block.input,
                        raw_block=block.model_dump(),
                    )
                )
            elif block.type == 'web_search_tool_result':
                results = []
                error = None
                for item in getattr(block, 'content', []) or []:
                    if getattr(item, 'type', None) == 'web_search_result':
                        results.append({'url': getattr(item, 'url', ''), 'title': getattr(item, 'title', '')})
                    elif getattr(item, 'type', None) == 'web_search_tool_result_error':
                        error = getattr(item, 'error_code', 'unknown')
                web_search_results.append(
                    WebSearchResult(
                        tool_use_id=getattr(block, 'tool_use_id', ''),
                        results=results,
                        error=error,
                        raw_block=block.model_dump(),
                    )
                )

        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
            cache_creation_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
        )

        return LLMResponse(
            text=''.join(text_parts),
            tool_calls=tool_calls,
            server_tool_uses=server_tool_uses,
            web_search_results=web_search_results,
            web_search_citations=_harvest_web_search_citations(response.content),
            usage=usage,
            cost_usd=pricing.cost_usd(model, usage),
            model=response.model,
            stop_reason=response.stop_reason or '',
        )

    def effective_model(self, requested: str = '') -> str:
        # The API takes a bare model id; a routing prefix is ours, not theirs.
        return (requested or self._default_model).removeprefix('anthropic/')

    async def _chat_stream(
        self, *, system, messages, tools=None, max_tokens=8192, **kwargs
    ) -> AsyncGenerator[LLMEvent]:
        model = self.effective_model(str(kwargs.pop('model', None) or ''))

        params: dict = {
            'model': model,
            'max_tokens': max_tokens,
            'system': system_with_cache(system) if isinstance(system, str) and system else system,
            'messages': messages,
            # Seam-normalised sampling (base.DEFAULT_TEMPERATURE). Taken by key
            # rather than ``params.update(kwargs)`` because the streaming loop
            # also passes ``tool_choice`` in OpenAI's string form ('none'),
            # which Anthropic's API only accepts as an object — forwarding the
            # whole kwargs bag here would 400 the primary chat path.
            'temperature': kwargs['temperature'],
        }
        if tools:
            params['tools'] = tools

        try:
            stream_ctx: AsyncMessageStreamManager[None] = self._active_client().messages.stream(**params)
        except anthropic.RateLimitError as e:
            raise ProviderRateLimitError(str(e), wait_seconds=30) from e
        except anthropic.APIError as e:
            raise ProviderAPIError(
                str(e),
                status_code=getattr(e, 'status_code', None),
                raw_message=str(e),
            ) from e

        # Track in-flight tool calls
        current_tool_id = ''
        current_tool_name = ''
        current_tool_input_json = ''

        try:
            async with stream_ctx as stream:
                async for event in stream:
                    if event.type == 'content_block_start':
                        block = event.content_block
                        if block.type == 'tool_use':
                            current_tool_id = block.id
                            current_tool_name = block.name
                            current_tool_input_json = ''
                            yield LLMEvent(
                                type='tool_start',
                                tool_call=ToolCall(id=block.id, name=block.name, input={}),
                            )
                        elif block.type == 'server_tool_use':
                            yield LLMEvent(
                                type='server_tool_use',
                                server_tool_use=ServerToolUse(
                                    id=block.id,
                                    name=block.name,
                                    input=block.input,
                                    raw_block=block.model_dump(),
                                ),
                            )

                    elif event.type == 'content_block_delta':
                        delta = event.delta
                        if delta.type == 'text_delta':
                            yield LLMEvent(type='text_delta', text=delta.text)
                        elif delta.type == 'input_json_delta':
                            current_tool_input_json += delta.partial_json
                            yield LLMEvent(type='tool_delta', text=delta.partial_json)

                    elif event.type == 'content_block_stop':
                        if current_tool_id:
                            try:
                                parsed_input = json.loads(current_tool_input_json)
                            except json.JSONDecodeError, ValueError:
                                parsed_input = {}
                            yield LLMEvent(
                                type='tool_end',
                                tool_call=parsed_tool_call(current_tool_id, current_tool_name, parsed_input),
                            )
                            current_tool_id = ''
                            current_tool_name = ''
                            current_tool_input_json = ''

                # Get final message for usage, cost, stop_reason, and server tool blocks
                final_message = await stream.get_final_message()
                usage = TokenUsage(
                    input_tokens=final_message.usage.input_tokens,
                    output_tokens=final_message.usage.output_tokens,
                    cache_read_tokens=(getattr(final_message.usage, 'cache_read_input_tokens', 0) or 0),
                    cache_creation_tokens=(getattr(final_message.usage, 'cache_creation_input_tokens', 0) or 0),
                )

                # Emit web_search_tool_result events from the final message
                for block in final_message.content:
                    if block.type == 'web_search_tool_result':
                        results = []
                        error = None
                        for item in getattr(block, 'content', []) or []:
                            if getattr(item, 'type', None) == 'web_search_result':
                                results.append({'url': getattr(item, 'url', ''), 'title': getattr(item, 'title', '')})
                            elif getattr(item, 'type', None) == 'web_search_tool_result_error':
                                error = getattr(item, 'error_code', 'unknown')
                        yield LLMEvent(
                            type='web_search_result',
                            web_search_result=WebSearchResult(
                                tool_use_id=getattr(block, 'tool_use_id', ''),
                                results=results,
                                error=error,
                                raw_block=block.model_dump(),
                            ),
                        )

                # Serialize all content blocks for message history
                serialized_content: list[dict[str, Any]] = []
                for block in final_message.content:
                    if block.type == 'text':
                        serialized_content.append({'type': 'text', 'text': block.text})
                    elif block.type == 'tool_use':
                        serialized_content.append(
                            {
                                'type': 'tool_use',
                                'id': block.id,
                                'name': block.name,
                                'input': block.input,
                            }
                        )
                    elif block.type in ('server_tool_use', 'web_search_tool_result'):
                        serialized_content.append(block.model_dump())

                yield LLMEvent(
                    type='done',
                    usage=usage,
                    stop_reason=final_message.stop_reason or '',
                    cost_usd=pricing.cost_usd(model, usage),
                    model=final_message.model,
                    # Stash serialized content in text field for message history
                    text=json.dumps(serialized_content),
                    # Plaintext web_search citation spans for this turn —
                    # carried separately (NOT folded into serialized_content,
                    # which is replayed to Anthropic as message history).
                    web_search_citations=_harvest_web_search_citations(
                        final_message.content,
                    ),
                )
        except anthropic.RateLimitError as e:
            raise ProviderRateLimitError(str(e), wait_seconds=30) from e
        except anthropic.APIError as e:
            raise ProviderAPIError(
                str(e),
                status_code=getattr(e, 'status_code', None),
                raw_message=str(e),
            ) from e

    def supports_prompt_caching(self) -> bool:
        return True

    def supports_tool_use(self) -> bool:
        return True

    def supports_server_tools(self) -> bool:
        return True

    def skill_invocation_tier(self) -> str:
        return 'high'
