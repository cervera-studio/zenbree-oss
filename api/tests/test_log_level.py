"""LOG_LEVEL turns the API's logs up or down with a restart; a typo stops the process instead of logging nothing."""

import logging
from typing import get_args

from celery.app.trace import LOG_RETRY, LOG_SUCCESS

from app.core.config import Settings
from app.core.logging import LEVELS, redact_secrets


def test_every_log_level_the_setting_accepts_has_a_logging_level():
    assert set(LEVELS) == set(get_args(Settings.model_fields['log_level'].annotation))


def test_the_log_level_is_read_in_any_case():
    assert Settings(log_level='INFO').log_level == 'info'  # type: ignore[arg-type]  # env values come as typed


def test_celery_keeps_the_app_log_level(app_logging):
    from app.worker import celery_app

    root = logging.getLogger()
    before = root.level
    celery_app.log.setup_logging_subsystem(loglevel='DEBUG')
    assert root.level == before


def test_a_task_return_value_never_reaches_the_log_but_retries_do(app_logging):
    import app.worker  # noqa: F401  # adds the filter

    trace_logger = logging.getLogger('celery.app.trace')
    success = trace_logger.makeRecord('t', logging.INFO, 'f', 1, LOG_SUCCESS, ({'return_value': 'x'},), None)
    retry = trace_logger.makeRecord('t', logging.INFO, 'f', 1, LOG_RETRY, ({'exc': 'x'},), None)
    assert not trace_logger.filter(success)
    assert trace_logger.filter(retry)


def test_secrets_and_contact_data_never_reach_a_log_line():
    event = redact_secrets(
        None,
        'info',
        {'event': 'x', 'api_key': 'k', 'payload': {'email': 'a@b.it', 'rows': [{'phone': '+39'}]}, 'input_tokens': 3},
    )
    assert event == {
        'event': 'x',
        'api_key': '[redacted]',
        'payload': {'email': '[redacted]', 'rows': [{'phone': '[redacted]'}]},
        'input_tokens': 3,
    }


def test_an_access_line_carries_no_query_string_or_unsubscribe_token():
    from app.core.logging import _redact_access_path

    access = logging.getLogger('uvicorn.access')
    fmt = '%s - "%s %s HTTP/%s" %d'
    oauth = access.makeRecord('a', logging.INFO, 'f', 1, fmt, ('ip', 'GET', '/v1/cb?code=c&state=s', '1.1', 302), None)
    unsub = access.makeRecord(
        'a', logging.INFO, 'f', 1, fmt, ('ip', 'POST', '/v1/unsubscribe/gAAAsecret/undo', '1.1', 200), None
    )
    assert _redact_access_path(oauth) and _redact_access_path(unsub)
    assert oauth.getMessage() == 'ip - "GET /v1/cb HTTP/1.1" 302'
    assert unsub.getMessage() == 'ip - "POST /v1/unsubscribe/<token>/undo HTTP/1.1" 200'
