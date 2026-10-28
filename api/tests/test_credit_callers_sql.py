"""#660: every caller that holds credits, through a charge and a refund, read from the ledger the user sees.

Each caller runs as in production: the chat turn, «Cerca il telefono» on a row, an AI column fill, a scheduled
search and a watchlist run. Each test reads the ``credit_transactions`` rows of the organization and its
balance after the run. The refusal tests read which error each caller gives: the project's hard monthly cap
(#307) or the wallet."""

import threading
import time
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.rows import dict_row

from app.billing.credits import Hold, Payer, reserve
from app.core.config import settings
from app.llm.base import ToolCall
from app.scheduling import runner as scheduled
from app.scheduling import service as schedules
from app.watchlists import runner as watching
from app.watchlists import service as watchlists
from tests.conftest import act_in_own_org, member, project
from tests.test_agent import _balance, _job, _run, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_ai_columns import ACME_PAGE, ANSWERS, SEARCH, FakeProvider, _fill, _list_with_rows
from tests.test_ai_columns import fake_search as fake_search
from tests.test_ai_columns import queued as queued
from tests.test_row_phone import FOUND, MISSING, _lookup, _row
from tests.test_row_phone import vendors as vendors
from tests.test_scheduling_sql import fake as fake_search_tool
from tests.test_scheduling_sql import setup as scheduled_search
from tests.test_watchlists_sql import Vendor, _as_owner, _posting
from tests.test_watchlists_sql import _setup as watched_list

pytestmark = needs_db


def _ledger(db, uid) -> list[tuple[str, str, int, str | None]]:
    rows = db.execute(
        'SELECT type, status, amount, reference_type FROM credit_transactions'
        " WHERE organization_id = default_organization(%s) AND reference_type IS DISTINCT FROM 'earlier'",
        (uid,),
    ).fetchall()
    return sorted((r['type'], r['status'], r['amount'], r['reference_type']) for r in rows)


def _held(db, uid) -> int:
    return db.execute(
        'SELECT count(*) AS n FROM credit_transactions WHERE organization_id = default_organization(%s)'
        " AND status = 'reserved'",
        (uid,),
    ).fetchone()['n']


def _cap(db, uid, left: int) -> None:
    """A hard monthly cap (#307) on the user's default project with ``left`` credits of room: an earlier spend of 1
    this month, outside the ledger the tests read."""
    pid = project(db, uid)
    db.execute("UPDATE projects SET credit_cap = %s, credit_cap_mode = 'hard' WHERE id = %s", (left + 1, pid))
    db.execute(
        'INSERT INTO credit_transactions (organization_id, user_id, project_id, type, amount, balance_after, status,'
        " reference_type) VALUES (default_organization(%s), %s, %s, 'deduction', 1, 0, 'committed', 'earlier')",
        (uid, uid, pid),
    )


# ── chat turn ─────────────────────────────────────────────────────────────


async def test_a_chat_turn_keeps_what_it_spent_and_refunds_the_slack(db):
    uid = _user(db, credits=50)
    _, jid = _job(db, uid, 'agencies in Milan')
    step = [ToolCall('t1', 'fake_companies', {}), ToolCall('t2', 'fake_emails', {})]
    await _run(jid, [('', step), ('Found 2.', [])])
    assert _ledger(db, uid) == [('deduction', 'committed', 50, 'job'), ('refund', 'committed', 46, 'reservation.slack')]
    assert _balance(db, uid) == 46


async def test_a_chat_turn_that_spends_nothing_releases_its_hold(db):
    uid = _user(db, credits=50)
    _, jid = _job(db, uid, 'remember this')
    await _run(jid, [('', [ToolCall('t1', 'fake_note', {})]), ('Saved.', [])])
    assert _ledger(db, uid) == [('refund', 'committed', 50, 'job'), ('reservation', 'released', 50, 'job')]
    assert _balance(db, uid) == 50


@pytest.mark.parametrize(
    ('credits', 'room', 'error'),
    [
        (50, 1, 'monthly credit cap'),
        (1, None, 'Not enough credits'),
        # #660: the one rule of every caller, the cap names a refusal its room cannot cover, also with the wallet short
        (1, 1, 'monthly credit cap'),
    ],
)
async def test_a_chat_step_refused_names_the_cap_or_the_wallet(db, credits, room, error):
    uid = _user(db, credits=credits)
    if room is not None:
        _cap(db, uid, room)
    _, jid = _job(db, uid, 'x')
    events, _ = await _run(jid, [('', [ToolCall('t1', 'fake_emails', {})]), ('Sorry.', [])])
    result = dict(events)['tool_result']
    assert result['status'] == 'failed' and error in result['result_preview']
    assert _balance(db, uid) == credits and _held(db, uid) == 0


def test_a_hold_that_takes_the_cap_room_first_leaves_the_rest_to_the_next(db):
    """Another hold of the project commits 60 between the room read and the locked recheck: the next step holds
    the 90 left of the cap, not a refusal that tells a user with 940 credits to top up."""
    uid = _user(db, credits=1000)
    pid = project(db, uid)
    db.execute("UPDATE projects SET credit_cap = 150, credit_cap_mode = 'hard' WHERE id = %s", (pid,))
    org = db.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o']
    payer = Payer(str(org), uid, pid)
    other = psycopg.connect(settings.database_url, row_factory=dict_row)
    reserve(other, payer, 60, reference_type='job', reference_id=None, description='other step')
    out = {}

    def step():
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            out['hold'] = reserve(conn, payer, 100, need=5, reference_type='job', reference_id=None, description='step')

    thread = threading.Thread(target=step)
    thread.start()
    deadline = time.monotonic() + 10
    while not db.execute(  # the step waits on the project's lock that the other hold keeps until its commit
        "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone():
        assert time.monotonic() < deadline, 'the step never waited on the project lock'
        time.sleep(0.01)
    other.commit()
    other.close()
    thread.join()
    assert isinstance(out['hold'], Hold) and out['hold'].amount == 90, out['hold']
    assert _ledger(db, uid) == [('reservation', 'reserved', 60, 'job'), ('reservation', 'reserved', 90, 'job')]
    assert _balance(db, uid) == 850


# ── row phone ─────────────────────────────────────────────────────────────


def test_a_found_phone_is_charged_its_price(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, FOUND)
    assert c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()['credits'] == 8
    assert _ledger(db, uid) == [('deduction', 'committed', 8, 'enrich_phone')]
    assert _balance(db, uid) == 42


def test_a_phone_not_found_releases_its_hold(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, MISSING)
    assert c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()['credits'] == 0
    assert _ledger(db, uid) == [
        ('refund', 'committed', 8, 'enrich_phone'),
        ('reservation', 'released', 8, 'enrich_phone'),
    ]
    assert _balance(db, uid) == 50


@pytest.mark.parametrize(('credits', 'room', 'code'), [(50, 7, 'ROW.PROJECT_CAP'), (7, None, 'ROW.NO_CREDITS')])
def test_a_phone_search_refused_names_the_cap_or_the_wallet(client, db, monkeypatch, credits, room, code):
    c, uid = client
    db.execute('UPDATE user_credits SET credits_remaining = %s WHERE user_id = %s', (credits, uid))
    if room is not None:
        _cap(db, uid, room)
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, RuntimeError('must not be called'))
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 402 and res.json()['detail']['code'] == code
    assert _ledger(db, uid) == []


# ── AI column fill ────────────────────────────────────────────────────────


def _ai_column(tc, db, uid, answers: dict[str, str | None]) -> str:
    lid = _list_with_rows(db, uid, list(answers))
    for name, value in answers.items():
        SEARCH[name] = [ACME_PAGE]
        ANSWERS[name] = {'value': value, 'source_url': ACME_PAGE['url'] if value else None}
    assert tc.post(f'/v1/lists/{lid}/columns', json={'label': 'H', 'prompt': 'hospitals?'}).status_code == 201
    return lid


async def test_an_ai_fill_charges_filled_cells_and_refunds_the_slack(client, db, fake_search, queued):
    tc, uid = client
    lid = _ai_column(tc, db, uid, {'Acme': 'Yes, 120 hospitals', 'Beta': None})
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    assert _ledger(db, uid) == [
        ('deduction', 'committed', 2, 'ai_column'),
        ('refund', 'committed', 1, 'reservation.slack'),
    ]
    assert _balance(db, uid) == 49


async def test_an_ai_fill_that_fills_nothing_releases_its_hold(client, db, fake_search, queued):
    tc, uid = client
    lid = _ai_column(tc, db, uid, {'Acme': None, 'Beta': None})
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    assert _ledger(db, uid) == [('refund', 'committed', 2, 'ai_column'), ('reservation', 'released', 2, 'ai_column')]
    assert _balance(db, uid) == 50


@pytest.mark.parametrize(
    ('credits', 'room', 'error'), [(50, 0, 'project_cap_reached'), (0, None, 'insufficient_credits')]
)
async def test_an_ai_fill_refused_names_the_cap_or_the_wallet(client, db, fake_search, queued, credits, room, error):
    tc, uid = client
    db.execute('UPDATE user_credits SET credits_remaining = %s WHERE user_id = %s', (credits, uid))
    if room is not None:
        _cap(db, uid, room)
    lid = _ai_column(tc, db, uid, {'Acme': 'Yes, 120 hospitals'})
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    fill = next(c for c in tc.get(f'/v1/lists/{lid}').json()['columns'] if c['key'] == 'ai_h')['config']['fill']
    assert (fill['status'], fill['error']) == ('failed', error)
    assert _ledger(db, uid) == []


# ── scheduled search ──────────────────────────────────────────────────────


async def test_a_scheduled_search_that_delivers_is_charged(db, monkeypatch):
    user, search = scheduled_search(db, campaign=False)
    fake_search_tool(monkeypatch)
    await scheduled.run_search(schedules.run_now(user, search['id'])['id'])
    assert _ledger(db, user) == [('deduction', 'committed', 1, 'saved_search')]
    assert _balance(db, user) == 19


async def test_a_scheduled_search_that_delivers_nothing_gives_its_hold_back(db, monkeypatch):
    user, search = scheduled_search(db, campaign=False)
    fake_search_tool(monkeypatch, rows=[])
    await scheduled.run_search(schedules.run_now(user, search['id'])['id'])
    assert _ledger(db, user) == [
        ('refund', 'committed', 1, 'saved_search'),
        ('reservation', 'released', 1, 'saved_search'),
    ]
    assert _balance(db, user) == 20


@pytest.mark.parametrize(
    ('credits', 'room', 'error'), [(20, 0, 'project_cap_reached'), (0, None, 'insufficient_credits')]
)
async def test_a_scheduled_search_refused_names_the_cap_or_the_wallet(db, monkeypatch, credits, room, error):
    user, search = scheduled_search(db, campaign=False, credits=credits)
    if room is not None:
        _cap(db, user, room)
    fake_search_tool(monkeypatch)
    run = schedules.run_now(user, search['id'])
    await scheduled.run_search(run['id'])
    row = db.execute('SELECT status, error_code FROM saved_search_runs WHERE id = %s', (run['id'],)).fetchone()
    assert (row['status'], row['error_code']) == ('failed', error)
    assert _ledger(db, user) == []


# ── watchlist run ─────────────────────────────────────────────────────────


@pytest.fixture
def vendor(monkeypatch):
    fake = Vendor()
    monkeypatch.setattr(watching, 'ApolloClient', lambda: fake)
    monkeypatch.setattr(watching, 'vendor_disabled_now', lambda provider: False)
    return fake


async def _watch(db, watchlist) -> dict:
    run = watchlists.enqueue(db, watchlist['id'], datetime.now(UTC))
    with _as_owner(db, watchlist):
        await watching.run_watchlist(run['id'])
    return db.execute('SELECT * FROM watchlist_runs WHERE id = %s', (run['id'],)).fetchone()


async def test_a_watchlist_run_charges_each_account_with_postings_and_refunds_the_slack(db, vendor):
    uid, watchlist, _ = watched_list(db, [{'name': 'Acme', 'apollo_id': 'o1'}, {'name': 'Quiet', 'apollo_id': 'o2'}])
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    assert (await _watch(db, watchlist))['credits_spent'] == 1
    assert _ledger(db, uid) == [
        ('deduction', 'committed', 2, 'watchlist'),
        ('refund', 'committed', 1, 'reservation.slack'),
    ]
    assert _balance(db, uid) == 199


async def test_a_watchlist_run_that_finds_no_postings_gives_its_hold_back(db, vendor):
    uid, watchlist, _ = watched_list(db, [{'name': 'Quiet', 'apollo_id': 'o2'}])
    assert (await _watch(db, watchlist))['credits_spent'] == 0
    assert _ledger(db, uid) == [('refund', 'committed', 1, 'watchlist'), ('reservation', 'released', 1, 'watchlist')]
    assert _balance(db, uid) == 200


async def test_a_deleted_watchlist_gives_the_hold_of_its_running_run_back(db, vendor):
    uid, watchlist, _ = watched_list(db, [{'name': 'Acme', 'apollo_id': 'o1'}])
    act_in_own_org(db, uid)  # the run's task, then the creator's delete request
    assert watching.claim(watchlists.enqueue(db, watchlist['id'], datetime.now(UTC))['id'])
    watchlists.delete(member(db, uid), project(db, uid), watchlist['id'])
    assert _ledger(db, uid) == [('refund', 'committed', 1, 'watchlist'), ('reservation', 'released', 1, 'watchlist')]
    assert _balance(db, uid) == 200


@pytest.mark.parametrize(
    ('credits', 'room', 'error'), [(200, 1, 'project_cap_reached'), (1, None, 'insufficient_credits')]
)
async def test_a_watchlist_run_refused_names_the_cap_or_the_wallet(db, vendor, credits, room, error):
    uid, watchlist, _ = watched_list(
        db, [{'name': 'Acme', 'apollo_id': 'o1'}, {'name': 'Beta', 'apollo_id': 'o2'}], credits=credits
    )
    if room is not None:
        _cap(db, uid, room)
    run = await _watch(db, watchlist)
    assert (run['status'], run['error_code']) == ('failed', error)
    assert _ledger(db, uid) == []


# ── prices ────────────────────────────────────────────────────────────────


def test_a_list_sends_the_prices_its_work_is_billed_at(client, db):
    """The web prices «Altre righe», an AI column and a phone search from these, never from its own copy (#569)."""
    tc, uid = client
    lid, _ = _row(db, uid)
    assert tc.get(f'/v1/lists/{lid}').json()['prices'] == {
        'search': 0,
        'email': 1,
        'phone': 8,
        'ai_cell': 1,
        'ai_cells_per_run': 200,
    }
