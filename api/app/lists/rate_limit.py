"""Redis fixed-window limits on list routes."""

import hashlib

import structlog
from fastapi import Depends, Request

from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.errors_http import api_error
from app.core.rate_limit import over_limit

Auth = Depends(current_user_or_api_key)
logger = structlog.get_logger()


def limit_list_reads(request: Request, user: CurrentUser = Auth) -> None:
    action, maximum, window = ('export', 10, 3600) if '/export.' in request.url.path else ('rows', 60, 60)
    key = f'rl:list:{action}:{hashlib.sha256(user.id.encode()).hexdigest()[:32]}'
    if over_limit(key, action, maximum, window):
        logger.warning('lists.read_rate_limited', user_id=user.id, action=action)
        raise api_error(
            429, 'LIST.RATE_LIMITED', 'Too many list reads; try again later', headers={'Retry-After': str(window)}
        )
