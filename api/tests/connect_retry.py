"""Docker Desktop forwards the test database's port through its VM, which dials Postgres from 10,535 local ports
(55000-65535). A closed dial holds its port for 60 s (TIME_WAIT), and one full run peaks at about 5,900 of them, so
overlapping runs from two worktrees use them all. The forward then accepts a connect and closes it at once (#760).
Ports come back within a minute, so a connect the forward dropped waits and tries again."""

import asyncio
import time

import psycopg

# What libpq says when the forward closes a connect before Postgres answers.
DROPPED = 'server closed the connection unexpectedly'
DEADLINE_S = 60.0
# A server that stays away for a whole deadline is down, not drained: every later connect fails at once.
_down = False


def _retry(error: psycopg.OperationalError, deadline: float, wait: float) -> bool:
    global _down
    if _down or DROPPED not in str(error):
        return False
    _down = time.monotonic() + wait > deadline
    return not _down


def _sync(connect):
    def retried(cls, *args, **kwargs):
        deadline, wait = time.monotonic() + DEADLINE_S, 0.05
        while True:
            try:
                return connect(cls, *args, **kwargs)
            except psycopg.OperationalError as e:
                if not _retry(e, deadline, wait):
                    raise
            time.sleep(wait)
            wait = min(wait * 2, 2.0)

    return classmethod(retried)


def _async(connect):
    async def retried(cls, *args, **kwargs):
        deadline, wait = time.monotonic() + DEADLINE_S, 0.05
        while True:
            try:
                return await connect(cls, *args, **kwargs)
            except psycopg.OperationalError as e:
                if not _retry(e, deadline, wait):
                    raise
            await asyncio.sleep(wait)
            wait = min(wait * 2, 2.0)

    return classmethod(retried)


def install() -> None:
    """Every connect of the run, the pool's included: ``psycopg.connect`` is a copy of the class method."""
    psycopg.Connection.connect = _sync(psycopg.Connection.connect.__func__)
    psycopg.connect = psycopg.Connection.connect
    psycopg.AsyncConnection.connect = _async(psycopg.AsyncConnection.connect.__func__)
