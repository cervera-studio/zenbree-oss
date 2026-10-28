"""app.main and app.worker both configure logging at import, and the tests import both in one process."""

import structlog

from app.core.logging import configure_logging


def test_a_second_configuration_keeps_the_processors_cached_loggers_hold(app_logging):
    processors = structlog.get_config()['processors']
    configure_logging()
    assert structlog.get_config()['processors'] is processors
