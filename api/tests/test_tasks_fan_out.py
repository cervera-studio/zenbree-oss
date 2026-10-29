"""``tasks.each_org``: a beat's units run at once, one per organization, and one unit's failure ends that unit only."""

import asyncio

import pytest
from structlog.testing import capture_logs

from app.core import scope as scopes
from app.core import tasks


async def _unit(org: str) -> str:
    if org == 'broken':
        raise scopes.ScopeChangeWhileOpen('a scope entered while a connection is open')
    await asyncio.sleep(0.01)  # still running when the broken unit fails
    assert scopes.current() == scopes.ForOrg(scopes.OrgId(org), 'test')
    return org


def test_a_unit_that_raises_a_scope_error_ends_only_itself():
    """A scope error is a ``BaseException``: let out of its unit, it ends ``gather``, and ``asyncio.run`` cancels the
    other organizations' units. The beat (``campaign_beat.run``) runs them that way."""
    with capture_logs() as logs:
        done = asyncio.run(tasks.each_org(['a', 'broken', 'b'], 'test', _unit))
    assert done == ['a', 'b']
    failed = [(e['event'], e['org_id'], e['error_type']) for e in logs if e['log_level'] == 'error']
    assert failed == [('task.org_unit_failed', 'broken', 'ScopeChangeWhileOpen')]


async def test_a_cancel_still_ends_every_unit():
    """A cancel (the worker's shutdown, a time limit) passes through: it is not a unit's failure."""
    started, cancelled = asyncio.Event(), []

    async def unit(org: str) -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(org)
            raise
        return org

    with capture_logs() as logs:
        beat = asyncio.create_task(tasks.each_org(['a', 'b'], 'test', unit))
        await started.wait()
        beat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await beat
    assert sorted(cancelled) == ['a', 'b']
    assert not logs, 'a cancel is not a failed unit'
