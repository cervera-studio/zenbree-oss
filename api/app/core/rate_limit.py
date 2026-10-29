"""Redis fixed-window limits; a Redis outage fails open."""

import hashlib
from functools import cache
from typing import cast

import redis
import structlog

from app.core.config import settings
from app.core.errors_http import api_error

logger = structlog.get_logger()


@cache
def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def over_limit(key: str, action: str, maximum: int, window: int) -> bool:
    """Count one hit in a fixed window. A Redis outage fails open."""
    try:
        count = cast(
            int,
            _redis().eval(
                "local count = redis.call('INCR', KEYS[1]); "
                "if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; "
                'return count',
                1,
                key,
                str(window),
            ),
        )
    except redis.RedisError as e:
        # ponytail: best-effort limits keep routes working during Redis outages; the API logs the gap.
        logger.warning('rate_limit.unavailable', action=action, reason=type(e).__name__)
        return False
    if not isinstance(count, int):  # a Redis that cannot run the script: an outage too
        logger.warning('rate_limit.unavailable', action=action, reason='no_count')
        return False
    return count > maximum


def hit(user_id: str, action: str, maximum: int, window: int, code: str, message: str) -> None:
    """Count one call of ``action`` by a user; past ``maximum`` in ``window`` seconds: 429 ``code``."""
    key = f'rl:{action}:{hashlib.sha256(user_id.encode()).hexdigest()[:32]}'
    if over_limit(key, action, maximum, window):
        logger.warning('rate_limited', user_id=user_id, action=action)
        raise api_error(429, code, message, headers={'Retry-After': str(window)})


def claim(key: str, ttl: int) -> bool:
    """Take a lock that expires on its own (``SET NX EX``): False while another holder has it. A Redis outage
    fails open, as the limits do."""
    try:
        return bool(_redis().set(key, '1', nx=True, ex=ttl))
    except redis.RedisError as e:
        logger.warning('rate_limit.unavailable', action='claim', reason=type(e).__name__)
        return True


def release(key: str) -> None:
    try:
        _redis().delete(key)
    except redis.RedisError as e:
        logger.warning('rate_limit.unavailable', action='release', reason=type(e).__name__)
