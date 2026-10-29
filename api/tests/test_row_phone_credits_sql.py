"""Divergence point 8: «Cerca il telefono» bills from its ``row_charges`` record, as the chat does, so a click whose
worker dies after the store still pays for the number it stored."""

import contextlib
import threading

from app.agent.tools.handlers import enrich_phone
from app.lists import row_phone
from app.lists.row_view import read_row
from app.llm.base import ToolCall
from tests.test_agent import _balance, _job, _people, _run, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_credit_callers_sql import _ledger
from tests.test_row_phone import FOUND, MISSING, _lookup, _row
from tests.test_row_phone import vendors as vendors

pytestmark = needs_db


def _reap(db) -> None:
    db.execute("UPDATE credit_transactions SET created_at = created_at - interval '1 hour'")
    db.execute('SELECT reap_stale_reservations_ids(20)').fetchall()


def _charges(db) -> list[dict]:
    return db.execute(
        'SELECT t.reference_type, c.credits, c.state FROM row_charges c JOIN credit_transactions t ON t.id = c.hold_id'
        ' ORDER BY t.reference_type'
    ).fetchall()


def test_a_click_whose_worker_dies_after_the_store_still_bills_its_number(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, FOUND)
    stored, dead = threading.Event(), threading.Event()

    def die(row_id, list_id):  # after the store's commit: this worker runs no further line
        stored.set()
        dead.wait(30)
        raise RuntimeError('the worker died')

    monkeypatch.setattr(row_phone, '_current', die)

    def post():
        with contextlib.suppress(RuntimeError):
            c.post(f'/v1/lists/{lid}/rows/{rid}/phone')

    click = threading.Thread(target=post)
    click.start()
    try:
        assert stored.wait(30), 'the click never stored its number'
        _reap(db)

        assert (
            db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']['phone']
            == '+39333111222'
        )
        assert _balance(db, uid) == 42
        assert _ledger(db, uid) == [('deduction', 'committed', 8, 'enrich_phone')]
        assert _charges(db) == [{'reference_type': 'enrich_phone', 'credits': 8, 'state': 'charged'}]
    finally:
        dead.set()
        click.join(30)


async def test_the_click_and_the_chat_bill_a_found_phone_at_its_price(client, db, monkeypatch):
    c, clicker = client
    lid, rid = _row(db, clicker)
    chatter = _user(db, credits=50)
    sid, jid = _job(db, chatter, 'telefoni')
    (person,) = _people(db, chatter, sid, 1)
    _lookup(monkeypatch, FOUND)

    assert c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()['credits'] == 8
    await _run(jid, [('', [ToolCall('t1', 'enrich_phone', {'row_ids': [str(person)]})]), ('fatto', [])])

    assert (_balance(db, clicker), _balance(db, chatter)) == (42, 42)
    assert _charges(db) == [
        {'reference_type': 'enrich_phone', 'credits': 8, 'state': 'charged'},
        {'reference_type': 'job', 'credits': 8, 'state': 'charged'},
    ]


def _reaped(monkeypatch, db, outcome: dict) -> None:
    """The vendor answers ``outcome`` after the reaper closed the click's hold."""

    async def one(p, apollo, treg, off):
        _reap(db)
        return {'row_id': p['row_id'], 'cells': outcome}

    monkeypatch.setattr(enrich_phone, '_phone_one', one)


def _stored(db, rid: str) -> tuple[str | None, str | None]:
    cells = db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']
    return cells.get('phone'), cells.get('phone_status')


def test_a_number_found_after_the_reaper_closed_the_hold_is_not_stored_and_the_search_is_offered_again(
    client, db, monkeypatch
):
    c, uid = client
    lid, rid = _row(db, uid)
    _reaped(monkeypatch, db, FOUND)

    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')

    assert res.status_code == 200
    assert (res.json()['status'], res.json()['credits']) == ('failed', 0)
    assert _stored(db, rid) == (None, None)
    assert read_row(db, rid, lid)['lookups']['phone'] == {'state': 'not_searched'}
    assert _balance(db, uid) == 50
    assert _charges(db) == []


def test_a_miss_after_the_reaper_closed_the_hold_stores_its_verdict_and_bills_nothing(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid)
    _reaped(monkeypatch, db, MISSING)

    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')

    assert res.status_code == 200
    assert (res.json()['status'], res.json()['credits']) == ('not_found', 0)
    assert _stored(db, rid) == (None, 'not_found')
    assert _balance(db, uid) == 50
    assert _charges(db) == []


def test_a_number_found_on_a_row_missing_a_required_column_shows_in_the_answer(client, db, monkeypatch):
    """The charge is held, and a held charge hides its cells while the hold is reserved: the close in the store's
    transaction makes it final before the answer reads the row."""
    c, uid = client
    lid, rid = _row(db, uid)
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'email', 'Email', 'static', 9, '{\"required\": true}')",
        (lid,),
    )
    _lookup(monkeypatch, FOUND)

    body = c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()

    assert (body['status'], body['credits']) == ('found', 8)
    assert body['row']['cells']['phone'] == '+39333111222'
    assert body['row']['lookups']['phone'] == {'state': 'found'}
    assert _balance(db, uid) == 42
    assert _charges(db) == [{'reference_type': 'enrich_phone', 'credits': 8, 'state': 'charged'}]
