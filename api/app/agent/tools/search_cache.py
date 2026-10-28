"""Session-scoped cache for successful prospect searches."""

from __future__ import annotations

import asyncio
import hashlib
import json
from functools import lru_cache
from typing import cast

import redis
import structlog

from app.agent.tools.registry import ToolContext, ToolResult
from app.core.config import settings

logger = structlog.get_logger()
TTL_SECONDS = 300


@lru_cache(maxsize=1)
def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)


def cache_key(
    ctx: ToolContext,
    *,
    tool: str,
    provider: str,
    endpoint: str,
    arguments: dict,
) -> str | None:
    if not ctx.session_id:
        return None
    identity = json.dumps(
        {
            'user_id': ctx.user_id,
            'session_id': ctx.session_id,
            'tool': tool,
            'provider': provider,
            'endpoint': endpoint,
            'arguments': arguments,
        },
        sort_keys=True,
        separators=(',', ':'),
    )
    return f'agent:search:v1:{hashlib.sha256(identity.encode()).hexdigest()}'


async def get(key: str | None) -> ToolResult | None:
    if key is None:
        return None
    try:
        encoded = cast(str | None, await asyncio.to_thread(_redis().get, key))
        if encoded is None:
            return None
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            return None
        if not payload.get('ok'):
            return None
        return ToolResult(**payload)
    except redis.RedisError, json.JSONDecodeError, TypeError, ValueError:
        logger.warning('agent.search_cache.read_failed')
        return None


async def put(key: str | None, result: ToolResult) -> None:
    if key is None or not result.ok:
        return
    try:
        encoded = json.dumps(result.__dict__, separators=(',', ':'))
        await asyncio.to_thread(_redis().setex, key, TTL_SECONDS, encoded)
    except redis.RedisError, TypeError, ValueError:
        logger.warning('agent.search_cache.write_failed')
