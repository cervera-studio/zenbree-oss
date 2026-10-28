"""Each send status writer, run on a row that another writer moved just before the write lands.

The table lists, per writer, the states its write may move a send from. For every other state the write must leave
the status as it is. The file does not import ``send_states``: it runs unchanged on a tree without it, so the same
matrix measures the writers before and after they share one transition table.

Needs TEST_DATABASE_URL; copies a throwaway database from the migrated api/db template.
"""

import contextlib
import inspect

import pytest

from app.auth.middleware import CurrentUser
from app.core.errors import err, ok
from app.core.mail_threading import ProviderReceipt
from app.core.scope import acting_as, acting_for
from app.outreach import crm_tasks, dispatcher, routes
from tests import test_dispatcher_sql
from tests.conftest import scoped_conn
from tests.test_dispatcher_sql import (
    DB_URL,
    LINKEDIN_STEPS,
    NOW,
    _campaign,
    _each_org,
    _each_org_async,
    _linkedin,
    _mailbox,
)
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches
unipile = test_dispatcher_sql.unipile

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: send proof SQL test NOT run')

STATES = ('awaiting_approval', 'approved', 'sending', 'sent', 'failed', 'refused', 'skipped')
CLAIMABLE, IN_FLIGHT = ('approved', 'failed'), ('sending',)


class _Race:
    """Moves the send to ``state`` right before the next status write of a writer runs, in the same transaction:
    what another writer that committed first leaves behind."""

    def __init__(self, c):
        self.c, self.send_id, self.state = c, None, None

    def wrap(self, inner):
        @contextlib.contextmanager
        def get_conn(*args, **kwargs):
            with inner(*args, **kwargs) as conn:
                yield _Conn(conn, self)

        return get_conn


class _Conn:
    def __init__(self, conn, race: _Race):
        self._conn, self._race = conn, race

    def execute(self, query, params=None, **kwargs):
        race = self._race
        if race.state and isinstance(query, str) and query.lstrip().startswith('UPDATE outreach_campaign_sends SET'):
            state, race.state = race.state, None
            self._conn.execute(
                "UPDATE outreach_campaign_sends SET status = %s, delivered_at = CASE WHEN %s = 'sent'"
                ' THEN coalesce(delivered_at, now()) ELSE delivered_at END WHERE id = %s',
                (state, state, race.send_id),
            )
        return self._conn.execute(query, params, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _settle_kwargs(claimed: bool) -> dict:
    """``claimed`` became a required keyword with the transition table; the tree before it has no such parameter."""
    return {'claimed': claimed} if 'claimed' in inspect.signature(dispatcher._settle).parameters else {}


def _send(c) -> dict:
    return c.execute('SELECT * FROM outreach_campaign_sends ORDER BY created_at LIMIT 1').fetchone()


def _status(c, send_id) -> str:
    return c.execute('SELECT status FROM outreach_campaign_sends WHERE id = %s', (send_id,)).fetchone()['status']


def _email_send(c) -> dict:
    uid = _user(c)
    _mailbox(c, uid)
    _campaign(c, uid)
    _each_org(dispatcher.plan, NOW)
    return _send(c)


async def _email_beat(c, race, provider, result, state) -> str:
    s = _email_send(c)
    provider.result = result
    race.send_id, race.state = s['id'], state
    await _each_org_async(dispatcher.send_due, NOW)
    return s['id']


async def _pre_claim(c, race, state, status, code, **kwargs):
    s = _email_send(c)
    race.send_id, race.state = s['id'], state
    with acting_for(str(s['organization_id']), 'campaign beat'):
        dispatcher._settle(s, status, code, **_settle_kwargs(False), **kwargs)
    return s['id']


async def _post_claim_settle(c, race, provider, code, state, **context):
    return await _email_beat(c, race, provider, err(code, detail='x', **context), state)


async def _linkedin_done(c, race, unipile, state, *, connected=False):
    unipile.connected = connected
    uid = _user(c)
    _linkedin(c, uid)
    _campaign(c, uid, LINKEDIN_STEPS)
    c.execute("UPDATE outreach_campaign_targets SET linkedin_url = 'https://www.linkedin.com/in/anna-rossi/'")
    _each_org(dispatcher.plan, NOW)
    s = _send(c)
    race.send_id, race.state = s['id'], state
    await _each_org_async(dispatcher.send_linkedin_due, NOW)
    return s['id']


async def _crm_settle(c, race, status, state, **kwargs):
    s = _email_send(c)
    race.send_id, race.state = s['id'], state
    with acting_for(str(s['organization_id']), 'campaign beat'):
        crm_tasks._settle(s, status, **kwargs)
    return s['id']


async def _undo_reject(c, race, state):
    uid = _user(c)
    _mailbox(c, uid)
    cid = _campaign(c, uid, approve=True)
    _each_org(dispatcher.plan, NOW)
    s = _send(c)
    c.execute(
        "UPDATE outreach_campaign_sends SET status = 'skipped', error_code = 'REJECTED' WHERE id = %s", (s['id'],)
    )
    c.execute('UPDATE outreach_campaign_targets SET next_step = 2 WHERE id = %s', (s['target_id'],))
    race.send_id, race.state = s['id'], state
    org = str(s['organization_id'])
    with acting_as(org, str(uid)):
        routes.undo_reject(cid, routes.SendIdsIn(send_ids=[s['id']]), CurrentUser(id=str(uid), org_id=org))
    return s['id']


# writer -> (the states its write may move a send from, how to run it on a send moved to `state` just before)
WRITERS = {
    'dispatcher._settle skipped EXCLUDED, before the claim': (
        CLAIMABLE,
        lambda f, state: _pre_claim(f.db, f.race, state, 'skipped', 'EXCLUDED', target_status='excluded'),
    ),
    'dispatcher._settle refused INVALID_EMAIL, before the claim': (
        CLAIMABLE,
        lambda f, state: _pre_claim(f.db, f.race, state, 'refused', 'INVALID_EMAIL'),
    ),
    'dispatcher success write (sent)': (
        IN_FLIGHT,
        lambda f, state: _email_beat(f.db, f.race, f.provider, ok(ProviderReceipt(message_id='g1')), state),
    ),
    'dispatcher rate-limit give-back (approved)': (
        IN_FLIGHT,
        lambda f, state: _post_claim_settle(f.db, f.race, f.provider, 'INTEGRATIONS.RATE_LIMITED', state),
    ),
    'dispatcher._settle failed, after the claim': (
        IN_FLIGHT,
        lambda f, state: _post_claim_settle(
            f.db, f.race, f.provider, 'INTEGRATIONS.TRANSPORT', state, delivery_ambiguous=False
        ),
    ),
    'dispatcher._settle refused, after the claim': (
        IN_FLIGHT,
        lambda f, state: _post_claim_settle(f.db, f.race, f.provider, 'INTEGRATIONS.HTTP_ERROR', state),
    ),
    'dispatcher unknown outcome (stays sending)': (
        IN_FLIGHT,
        lambda f, state: _post_claim_settle(
            f.db, f.race, f.provider, 'INTEGRATIONS.TRANSPORT', state, delivery_ambiguous=True
        ),
    ),
    'dispatcher._linkedin_done sent': (IN_FLIGHT, lambda f, state: _linkedin_done(f.db, f.race, f.unipile, state)),
    'dispatcher._linkedin_done skipped ALREADY_CONNECTED': (
        IN_FLIGHT,
        lambda f, state: _linkedin_done(f.db, f.race, f.unipile, state, connected=True),
    ),
    'crm_tasks._settle sent': (
        IN_FLIGHT,
        lambda f, state: _crm_settle(f.db, f.race, 'sent', state, task_id='task-1', at=NOW),
    ),
    'crm_tasks._settle failed': (
        IN_FLIGHT,
        lambda f, state: _crm_settle(f.db, f.race, 'failed', state, code='HUBSPOT.X'),
    ),
    'routes.undo_reject (awaiting_approval)': (('skipped',), lambda f, state: _undo_reject(f.db, f.race, state)),
}
CASES = [(w, s) for w, (sources, _) in WRITERS.items() for s in STATES if s not in sources]


class _Fixtures:
    def __init__(self, db, race, provider, unipile):
        self.db, self.race, self.provider, self.unipile = db, race, provider, unipile


@pytest.mark.parametrize(('writer', 'state'), CASES, ids=[f'{w} | from {s}' for w, s in CASES])
async def test_a_write_from_a_state_it_may_not_leave_changes_nothing(
    db, provider, switches, unipile, monkeypatch, webhook_events, writer, state
):
    race = _Race(db)
    monkeypatch.setattr(dispatcher, 'get_conn', race.wrap(dispatcher.get_conn))
    monkeypatch.setattr(crm_tasks, 'get_conn', race.wrap(scoped_conn(db)))
    monkeypatch.setattr(routes, 'get_conn', race.wrap(scoped_conn(db)))
    send_id = await WRITERS[writer][1](_Fixtures(db, race, provider, unipile), state)
    assert race.state is None, 'the writer never wrote this send'
    assert _status(db, send_id) == state
