"""The end of a chat turn commits once: the refund of its unfinished rows, the close of its hold and the job's
``credits_spent``. A kill, a failed close or a reaper that closed the hold first leaves the hold, the cells and the
cache telling the same story."""

import threading

import psycopg
import pytest
from psycopg.rows import dict_row

from app.billing import credits, row_charges
from app.core.config import settings
from app.core.scope import for_another_thread
from app.lists.row_view import VISIBLE_CELLS_SQL
from app.lists.rows import apply_row_updates
from tests.conftest import hold
from tests.test_agent import _balance, needs_db
from tests.test_agent import db as db
from tests.test_charge_final import _cells, _charges, _require, _turn
from tests.test_tab_sql import EMAIL, _emails, _reap, _stale, _wait_for_a_lock
from tests.test_tab_sql import _turn as _holding_turn

pytestmark = needs_db


class _Killed(BaseException):
    """The worker dies."""


def _job(db) -> dict:
    """The test's only job, so the query needs no filter."""
    return db.execute(
        'SELECT j.user_id::text, j.credits_spent, h.status AS hold, m.credits_used FROM jobs j'
        ' JOIN credit_transactions h ON h.id = j.reservation_id'
        " LEFT JOIN chat_messages m ON m.job_id = j.id AND m.role = 'assistant'"
    ).fetchone()


def _refund_frames(events) -> list[dict]:
    return [data for event, data in events if event == 'credits' and data.get('refund')]


async def test_a_turn_killed_after_its_refund_caches_what_its_hold_keeps(db, monkeypatch):
    """Bo is still unfinished at the end, so his email is refunded; the worker dies before the refund frame goes
    out. The hold keeps Ada's 2 credits, the job's ``credits_spent`` says 2 as well, and Bo's email stays taken back.
    """

    def die_on_the_refund(event, data):
        if data.get('refund'):
            raise _Killed

    with pytest.raises(_Killed):
        await _turn(db, monkeypatch, lambda list_id, user: None, on_frame=die_on_the_refund)
    reaped = _reap(db)
    job = _job(db)
    list_id = db.execute('SELECT id FROM lists').fetchone()['id']

    assert (reaped, _balance(db, job['user_id']), job['credits_spent'], _charges(db, list_id)) == (
        [],
        98,
        2,
        {'Ada Rossi': ['charged'], 'Bo Bianchi': ['refunded']},
    )


async def test_a_close_that_fails_takes_its_refund_back_and_the_reaper_keeps_every_charge(db, monkeypatch):
    """The refund and the close commit together or not at all: the turn reports what it spent before the refund,
    and the reaper later keeps every charge and shows Bo's email, as for a turn killed before its end."""

    def broken(conn, hold_id):
        raise RuntimeError('settle_hold failed')

    monkeypatch.setattr(credits, 'close', broken)

    uid, _, user, list_id, events = await _turn(db, monkeypatch, lambda list_id, user: None)
    job = _job(db)
    ended = (_refund_frames(events), job['credits_used'], job['credits_spent'], job['hold'], _charges(db, list_id))
    _reap(db)

    assert ended == ([], 4, 4, 'reserved', {'Ada Rossi': ['charged'], 'Bo Bianchi': ['held']})
    assert (_balance(db, uid), _cells(list_id, user)['Bo Bianchi']['email'], _charges(db, list_id)['Bo Bianchi']) == (
        96,
        '1@acme.test',
        ['charged'],
    )


async def test_a_turn_whose_hold_the_reaper_closed_refunds_nothing_and_reports_what_it_recorded(db, monkeypatch):
    """The reaper closes the hold mid-turn, after both emails were charged: it made Bo's held charge final, so the
    end of the turn gives nothing back, and the job, the message and the last ``stages`` frame say 4. The web still
    reloads Bo's list, whose pending email now shows."""
    uid, _, user, list_id, events = await _turn(db, monkeypatch, lambda list_id, user: _reap(db))
    job = _job(db)
    last_stages = [data for event, data in events if event == 'stages'][-1]

    assert (_refund_frames(events), _balance(db, uid), _charges(db, list_id)['Bo Bianchi']) == ([], 96, ['charged'])
    assert (job['credits_spent'], job['credits_used'], last_stages['credits_spent']) == (4, 4, 4)
    assert ('list_updated', {'list_id': str(list_id), 'entity': 'person', 'rows_updated': 1}) in events
    assert _cells(list_id, user)['Bo Bianchi']['email'] == '1@acme.test'


def test_a_turn_whose_hold_the_reaper_released_reports_nothing_spent(db):
    """The reaper finds no charge on the hold and releases it whole: the turn spent nothing, whatever it counted."""
    uid, turn, _, _ = _holding_turn(db)
    turn.spent = 40  # a count gone wrong
    reaped = _reap(db)

    given = turn.finish()

    spent = db.execute('SELECT credits_spent FROM jobs WHERE id = %s', (turn.job_id,)).fetchone()['credits_spent']
    assert (reaped, given, turn.spent, spent, _balance(db, uid)) == ([turn.tab.hold.id], ({}, []), 0, 0, 100)


def _charged(db, credits=100):
    """A turn that charged 5 credits each for the emails of Ada, complete, and Bo, who misses the required title, so
    his charge is held. (user, turn, list, ada, bo)"""
    uid, turn, lid, (ada, bo) = _holding_turn(db, credits=credits)
    _require(db, lid, 'title')
    db.execute("""UPDATE list_rows SET cells = cells || '{"title": "CTO"}' WHERE id = %s""", (ada,))
    with db.transaction():
        turn.spent = turn.record(db, 'c1', lid, apply_row_updates(db, lid, 'person', _emails([ada, bo])), EMAIL)
    return uid, turn, lid, ada, bo


def _in_thread(out: dict, name: str, fn) -> threading.Thread:
    def run() -> None:
        try:
            out[name] = fn()
        except Exception as e:  # a deadlock aborts one side
            out[name] = e

    thread = threading.Thread(
        target=for_another_thread().run, args=(run,)
    )  # the turn's scope, as the worker's own thread
    thread.start()
    return thread


def _paused_after_the_close(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """The end of the turn stops once it closed the hold, before its commit: it holds the hold, Bo's charge and row,
    and the wallet. (paused, go)"""
    paused, go = threading.Event(), threading.Event()
    close = credits.close

    def close_and_wait(conn, hold_id):
        spent = close(conn, hold_id)
        paused.set()
        go.wait(10)
        return spent

    monkeypatch.setattr(credits, 'close', close_and_wait)
    return paused, go


def _reap_now() -> list[str]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return [r['r'] for r in conn.execute('SELECT r::text FROM reap_stale_reservations_ids(20) AS r')]


def _states(db, *rows) -> list[str]:
    return [db.execute('SELECT state FROM row_charges WHERE row_id = %s', (r,)).fetchone()['state'] for r in rows]


def test_a_close_that_waits_on_the_reaper_gives_nothing_back_and_keeps_the_recorded_spend(db):
    """The reaper locks the hold first and makes Bo's charge final; the end of the turn waits on the hold, then finds
    it closed. It refunds nothing and reports the 10 credits the charges cost, not its own count."""
    uid, turn, lid, ada, bo = _charged(db)
    turn.spent = 40  # a count gone wrong
    _stale(db)
    reaper = psycopg.connect(settings.database_url, row_factory=dict_row)
    reaped = [r['r'] for r in reaper.execute('SELECT r::text FROM reap_stale_reservations_ids(20) AS r')]
    out: dict = {}

    thread = _in_thread(out, 'finished', turn.finish)
    _wait_for_a_lock(db, 'the end of the turn')
    reaper.commit()
    reaper.close()
    thread.join()

    spent = db.execute('SELECT credits_spent FROM jobs WHERE id = %s', (turn.job_id,)).fetchone()['credits_spent']
    bo_cells = db.execute(f'SELECT {VISIBLE_CELLS_SQL} AS cells FROM list_rows r WHERE r.id = %s', (bo,))
    assert (reaped, out['finished'], turn.spent, turn.refunded, spent, _balance(db, uid)) == (
        [turn.tab.hold.id],
        ({}, [lid]),
        10,
        0,
        10,
        90,
    )
    assert (_states(db, ada, bo), bo_cells.fetchone()['cells']['email']) == (['charged', 'charged'], f'{bo}@acme.test')


def test_the_reaper_skips_the_hold_of_a_turn_that_is_closing_and_waits_for_its_wallet(db, monkeypatch):
    """Both holds of the organization are stale. The end of the turn holds its hold and the wallet; the reaper skips
    that hold, releases the other one once the wallet is free, and neither deadlocks."""
    uid, turn, lid, ada, bo = _charged(db, credits=200)
    other = hold(db, uid, amount=30)
    _stale(db)
    paused, go = _paused_after_the_close(monkeypatch)
    out: dict = {}

    finishing = _in_thread(out, 'finished', turn.finish)
    assert paused.wait(10)
    reaping = _in_thread(out, 'reaped', _reap_now)
    _wait_for_a_lock(db, 'the reaper')
    go.set()
    finishing.join()
    reaping.join()

    assert out == {'finished': ({'c1': (5, 1)}, [lid]), 'reaped': [other]}
    assert (_balance(db, uid), _states(db, ada, bo)) == (195, ['charged', 'refunded'])


def test_a_campaign_draft_that_enrolls_bo_first_keeps_his_charge_through_the_end_of_the_turn(db):
    """#462: the draft's ``finalize`` locks Bo's charge; the end of the turn waits for its commit, then has nothing
    held to refund."""
    uid, turn, lid, ada, bo = _charged(db)
    draft = psycopg.connect(settings.database_url, row_factory=dict_row)
    row_charges.finalize(draft, [bo])
    out: dict = {}

    thread = _in_thread(out, 'finished', turn.finish)
    _wait_for_a_lock(db, 'the end of the turn')
    draft.commit()
    draft.close()
    thread.join()

    assert (out['finished'], turn.spent, _balance(db, uid), _states(db, ada, bo)) == (
        ({}, [lid]),
        10,
        90,
        ['charged', 'charged'],
    )


def test_a_campaign_draft_during_the_end_of_the_turn_waits_and_finds_bo_refunded(db, monkeypatch):
    """The end of the turn locks Bo's charge before the draft does: the draft waits for its commit, then finds the
    charge refunded and makes nothing final."""
    uid, turn, lid, ada, bo = _charged(db)
    paused, go = _paused_after_the_close(monkeypatch)
    out: dict = {}

    def enroll() -> bool:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            row_charges.finalize(conn, [bo])
        return True

    finishing = _in_thread(out, 'finished', turn.finish)
    assert paused.wait(10)
    enrolling = _in_thread(out, 'enrolled', enroll)
    _wait_for_a_lock(db, 'the draft')
    go.set()
    finishing.join()
    enrolling.join()

    assert out == {'finished': ({'c1': (5, 1)}, [lid]), 'enrolled': True}
    assert (_balance(db, uid), _states(db, ada, bo)) == (95, ['charged', 'refunded'])


def test_a_refund_locks_its_rows_by_id_so_a_writer_locking_them_by_id_never_deadlocks(db):
    """Both rows are unfinished and held. The scan meets the higher id first (it was written first, the lower id
    last, and the planner is held to the heap), so a refund that locked in scan order would wait on the lower row
    while holding the higher one: a refill that locks by id and holds the lower row deadlocks with it. Locked by id,
    the refund waits first and both finish.
    """
    for setting in ('enable_indexscan', 'enable_bitmapscan'):  # the scan then follows the heap
        db.execute(f'ALTER DATABASE {db.info.dbname} SET {setting} = off')
    _, turn, lid, rows = _holding_turn(db)
    low, high = sorted(rows)
    _require(db, lid, 'title')
    with db.transaction():
        turn.spent = turn.record(db, 'c1', lid, apply_row_updates(db, lid, 'person', _emails([high, low])), EMAIL)
    for row in (high, low):  # an update moves the tuple to the end of the heap
        db.execute('UPDATE list_rows SET cells = cells WHERE id = %s', (row,))
    refill = psycopg.connect(settings.database_url, row_factory=dict_row)
    refill.execute('SELECT 1 FROM list_rows WHERE id = %s FOR UPDATE', (low,))
    out: dict = {}

    thread = _in_thread(out, 'finished', turn.finish)
    _wait_for_a_lock(db, 'the end of the turn')
    refill.execute('SELECT 1 FROM list_rows WHERE id = %s FOR UPDATE', (high,))
    refill.commit()
    refill.close()
    thread.join()

    assert (isinstance(out['finished'], Exception), _states(db, low, high)) == (False, ['refunded', 'refunded'])


def test_a_batch_of_rows_merged_in_vendor_order_locks_them_by_id_and_never_deadlocks_with_the_refund(db):
    """A saved-search run merges two existing rows, the higher id first. It locks them by id up front, so a writer
    that holds the lower row and then takes the higher one waits for it instead of deadlocking."""
    from app.core.db import get_conn
    from app.lists.rows import add_rows

    _, _, lid, _ = _holding_turn(db)
    cells = [{'email': 'a@x.test'}, {'email': 'b@x.test'}]
    ids = {c['email']: i for c, i in zip(cells, add_rows(db, lid, 'person', cells), strict=True)}
    low, high = sorted(ids.values())
    batch = [{'email': e, 'title': 'CTO'} for e in sorted(ids, key=lambda e: ids[e], reverse=True)]
    other = psycopg.connect(settings.database_url, row_factory=dict_row)
    other.execute('SELECT 1 FROM list_rows WHERE id = %s FOR NO KEY UPDATE', (low,))

    def merge() -> None:
        with get_conn() as conn:
            add_rows(conn, lid, 'person', batch)

    out: dict = {}
    thread = _in_thread(out, 'merged', merge)
    _wait_for_a_lock(db, 'the merge')
    other.execute('SELECT 1 FROM list_rows WHERE id = %s FOR NO KEY UPDATE', (high,))
    other.commit()
    other.close()
    thread.join()

    titles = [
        r['cells'].get('title')
        for r in db.execute('SELECT cells FROM list_rows WHERE id = ANY(%s::uuid[])', (list(ids.values()),))
    ]
    assert (out['merged'], titles) == (None, ['CTO', 'CTO'])
