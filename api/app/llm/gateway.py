"""Cloudflare AI Gateway — optional reverse proxy in front of LLM providers.

When configured (``cf_ai_gateway_account_id`` + ``cf_ai_gateway_name``),
provider base URLs route through the gateway so every LLM call is observed,
cached, and cost-tracked in one place — without changing provider keys
(pass-through) or losing provider-native features. We use each provider's own
CF endpoint (``/deepseek``, ``/anthropic``), NOT the OpenAI-compat one, so
Anthropic prompt caching + server-side web_search citations survive intact.

Off by default: with either setting empty, ``gateway_base_url`` returns None and
providers keep their native base URLs — zero behaviour change.
"""

from __future__ import annotations


def gateway_base_url(provider_slug: str) -> str | None:
    """CF endpoint for ``provider_slug`` (e.g. ``'deepseek'``, ``'anthropic'``),
    or None when the gateway is not configured."""
    from app.core.config import settings

    account = (settings.cf_ai_gateway_account_id or '').strip()
    name = (settings.cf_ai_gateway_name or '').strip()
    if not account or not name:
        return None
    return f'https://gateway.ai.cloudflare.com/v1/{account}/{name}/{provider_slug}'


def gateway_auth_header() -> dict[str, str]:
    """``cf-aig-authorization`` header when the gateway is Authenticated, else
    an empty dict. Sent alongside (not instead of) the provider's own key, so
    an unauthenticated gateway URL can't be abused by third parties."""
    from app.core.config import settings

    token = (settings.cf_ai_gateway_token or '').strip()
    return {'cf-aig-authorization': f'Bearer {token}'} if token else {}
