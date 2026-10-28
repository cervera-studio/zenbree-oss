"""Logging for the Celery processes (worker, beat): the app's configuration, kept against Celery's own."""

import logging

from celery.app.trace import LOG_SUCCESS
from celery.signals import setup_logging

from app.core.logging import configure_logging


def configure_celery_logging() -> None:
    configure_logging()
    # "Task … succeeded in …s: <return value>": a return value is data, not a log line. Retries and
    # failures on the same logger stay.
    logging.getLogger('celery.app.trace').addFilter(lambda record: record.msg != LOG_SUCCESS)
    # Any receiver makes Celery skip its own logging setup, which would reset the root logger to the
    # command's --loglevel and override LOG_LEVEL.
    setup_logging.connect(_keep_app_logging, weak=False)


def _keep_app_logging(**_: object) -> None:
    configure_logging()
