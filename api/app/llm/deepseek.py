"""DeepSeek LLM provider — thin subclass of OpenAICompatibleProvider."""

from __future__ import annotations

import hashlib
import hmac

import structlog

from app.llm.gateway import gateway_auth_header, gateway_base_url
from app.llm.json_output import JSON_OBJECT
from app.llm.metering import get_metering_context
from app.llm.openai_compat import OpenAICompatibleProvider

logger = structlog.get_logger()


class DeepSeekProvider(OpenAICompatibleProvider):
    # JSON mode, and only JSON mode: api-docs.deepseek.com/guides/json_mode
    # documents ``{'type': 'json_object'}`` and no schema grade, so a call
    # site asking for one gets the object here and the type constraint is
    # checked in the seam instead. The two conditions the same page attaches
    # to JSON mode are already met by the call sites that use it: the word
    # "json" appears in the prompt, and max_tokens is set. Its warning that
    # the reply is occasionally empty needs no handling here, since a caller
    # that reads JSON out of an empty string already has an answer for that.
    json_output_modes = frozenset({JSON_OBJECT})

    def __init__(self) -> None:
        from app.core.config import settings

        api_key = (settings.deepseek_api_key or '').strip()
        # Mirror AzureFoundryProvider: refuse to init without a key so the
        # router factory drops DeepSeek-direct from the chain instead of
        # firing a 401 on every default-routed call (which would otherwise
        # look like a transient failover, hiding the missing-secret config).
        if not api_key:
            raise RuntimeError(
                'DEEPSEEK_API_KEY is not configured — DeepSeekProvider '
                'cannot be initialized. Set the secret, or rely on the '
                'fallback chain if DeepSeek-direct is intentionally absent '
                'on this environment.'
            )

        base_url = gateway_base_url('deepseek') or settings.deepseek_base_url
        # ``thinking`` is a DeepSeek-native body field. Sent to a different
        # OpenAI-compatible host it is at best ignored and at worst a 400, so
        # it only rides along when the request is actually going to DeepSeek —
        # directly or through the CF gateway's own ``/deepseek`` endpoint,
        # which passes the body through untouched.
        native = 'api.deepseek.com' in base_url or base_url.endswith('/deepseek')
        # OpenRouter spells the same switch differently. Without it v4-flash
        # runs in thinking mode there (measured: 21 reasoning tokens on a
        # two-word reply), which is the behaviour the native flag above exists
        # to turn OFF — so omitting this would make an OpenRouter-backed
        # environment quietly slower, dearer and differently-shaped than
        # production rather than equivalent to it.
        openrouter = 'openrouter.ai' in base_url
        # ``user_id`` isolates DeepSeek's KV cache per user and must carry no
        # PII (api-docs.deepseek.com/api/create-chat-completion), so only an
        # HMAC of the user id goes out. DeepSeek-native, like ``thinking``.
        # No secret -> no field: fail closed rather than send the raw id.
        secret = (settings.deepseek_user_id_secret or '').strip()
        self._user_id_key = secret.encode() if native and secret else None
        if native and not secret:
            logger.warning('deepseek_user_id_secret_missing', effect='user_id not sent')

        super().__init__(
            # CF ``/deepseek`` endpoint keeps the ``/chat/completions`` path,
            # so only the base changes; native URL when the gateway is off.
            base_url=base_url,
            headers={'Authorization': f'Bearer {api_key}', **gateway_auth_header()},
            default_model=settings.deepseek_model or 'deepseek-v4-flash',
            # v4-flash defaults to thinking mode, which requires every
            # assistant turn's reasoning_content to be replayed on the next
            # request (DeepSeek 400s otherwise). The agent loop is tool
            # selection, not chain-of-thought, so disable thinking: faster,
            # cheaper, and it sidesteps the reasoning_content round-trip that
            # openai_compat does not carry.
            extra_body=(
                {'thinking': {'type': 'disabled'}}
                if native
                else {'reasoning': {'enabled': False}}
                if openrouter
                else {}
            ),
            # DeepSeek intermittently emits a tool call as text markup
            # (``<｜｜DSML｜｜tool_calls>…`` or Anthropic-style ``<function_calls>``)
            # instead of via the native tool_calls field; salvage it so the
            # turn isn't wasted and the markup never reaches the UI.
            text_tool_call_fallback=True,
        )

    def _per_call_body(self) -> dict:
        user_id = get_metering_context().user_id
        if not (self._user_id_key and user_id):
            return {}
        return {'user_id': hmac.new(self._user_id_key, user_id.encode(), hashlib.sha256).hexdigest()}

    def skill_invocation_tier(self) -> str:
        # DeepSeek V4 has solid function calling — invoke_skill exposed
        # in lazy mode for medium/low-tier skills, hybrid mode for
        # high-tier skills (see app.chat.skills.loader).
        return 'medium'
