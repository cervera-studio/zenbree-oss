"""structlog → JSON on stdout.

ponytail: no Logtail HTTP handler; BetterStack ingests container stdout
(Vector / docker log driver on the VPS). Add a handler only if that falls short.
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from structlog.typing import Processor

from app.core.config import settings

# Python's names, which the web's logger (web/lib/logger.ts) also uses: one LOG_LEVEL sets both, and a
# Better Stack query on `level` matches both sides. No level above error: an error is never muted.
LEVELS = {'debug': logging.DEBUG, 'info': logging.INFO, 'warning': logging.WARNING, 'error': logging.ERROR}

# Keys whose value never reaches a log line, whatever the caller passes. Exact names, not fragments:
# `input_tokens` or `email_status` are counts and states worth keeping.
_SECRET_KEYS = frozenset(
    {
        'token',
        'access_token',
        'refresh_token',
        'id_token',
        'authorization',
        'password',
        'secret',
        'client_secret',
        'cookie',
        'api_key',
        'apikey',
        'email',
        'phone',
    }
)


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {k: '[redacted]' if _is_secret(k) else _redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def _is_secret(key: object) -> bool:
    return isinstance(key, str) and key.lower() in _SECRET_KEYS


# uvicorn's access line carries the raw URL: OAuth callbacks put `code`/`state` in the query (#452), and an
# unsubscribe token is the recipient's credential (#172). Same redaction as the web's http-client.
_UNSUBSCRIBE_TOKEN = re.compile(r'^(/v1/unsubscribe/)[^/?]+')


def _redact_access_path(record: logging.LogRecord) -> bool:
    if isinstance(record.args, tuple) and len(record.args) == 5:
        client, method, path, version, status = record.args
        path = _UNSUBSCRIBE_TOKEN.sub(r'\1<token>', str(path).split('?', 1)[0])
        record.args = (client, method, path, version, status)
    return True


def redact_secrets(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> Mapping[str, Any]:
    """structlog processor: the same denylist as the web's logger."""
    return {k: '[redacted]' if _is_secret(k) else _redact(v) for k, v in event_dict.items()}


_configured = False


def configure_logging() -> None:
    """Idempotent: ``app.main`` and ``app.worker`` both call it at import, and one process can import both (the
    tests do). Each logger is cached on first use with the processors of that moment, so a second configuration
    would leave the loggers already used on the old list, out of reach of ``structlog.testing.capture_logs``."""
    global _configured
    if _configured:
        return
    _configured = True
    level = LEVELS[settings.log_level]
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        redact_secrets,
        structlog.processors.TimeStamper(fmt='iso', utc=True),
        # show_locals would print every frame's variables (api keys, emails) past redact_secrets.
        structlog.processors.ExceptionRenderer(structlog.tracebacks.ExceptionDictTransformer(show_locals=False)),
    ]
    # Stdlib records (Celery, uvicorn, libraries) get the same JSON line, level and redaction as ours.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[structlog.stdlib.add_logger_name, *shared],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # httpx logs every request URL at INFO; some vendor keys ride in the query string.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    # uvicorn configures its loggers, with their own text handlers, before it imports the app; this runs after.
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)
    logging.getLogger('uvicorn.access').addFilter(_redact_access_path)
    structlog.configure(
        processors=[*shared, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
