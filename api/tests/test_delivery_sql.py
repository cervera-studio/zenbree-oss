import json
from dataclasses import replace

import pytest

from app.agent.tools import handlers, registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.price import Price
from app.lists.lookup_outcome import searched
from app.llm.base import ToolCall
from tests.test_agent import _balance, _chat, _run, _user, needs_db
from tests.test_agent import db as db
from tests.test_plan import _people_list

pytestmark = needs_db

EMAIL = Price(credits=1, keys=('email', 'email_status'), found=lambda cells: bool(cells.get('email')))


def _emails(monkeypatch, row_ids, price=EMAIL):
    async def handle(args, ctx):
        updates = [{'row_id': r, 'cells': {'email': f'{r}@acme.test', 'email_status': 'verified'}} for r in row_ids]
        return ToolResult(ok=True, row_updates=updates, entity='person', outcome=searched('email', updates))

    schema = {'type': 'object', 'properties': {'row_ids': {'type': 'array'}}}
    tool = ToolDef('emails', 'test', schema, handle, price=price)
    monkeypatch.setitem(registry._REGISTRY, 'emails', tool)


def _spent(db, jid) -> int:
    return db.execute('SELECT credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()['credits_spent']


def _charged(db) -> list[int]:
    return [r['credits'] for r in db.execute('SELECT credits FROM row_charges ORDER BY created_at')]


def _stored(db, list_id) -> int:
    q = "SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND cells->>'email' IS NOT NULL"
    return db.execute(q, (list_id,)).fetchone()['n']


async def test_an_update_on_a_row_of_another_list_is_not_billed(db, monkeypatch):
    from app.lists.rows import add_rows, create_list

    uid = _user(db, credits=100)
    jid, _, ids = _people_list(db, uid, n=2)
    elsewhere = create_list(db, user_id=uid, session_id=_chat(db, uid), name='Other', entity='person')
    [stray] = add_rows(db, elsewhere, 'person', [{'full_name': 'Zed', 'linkedin_url': 'https://linkedin.com/in/zed'}])
    _emails(monkeypatch, [ids[0], stray])

    events, _ = await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': [ids[0], stray]})]), ('Done.', [])])

    assert [d['cost'] for e, d in events if e == 'credits'] == [1]
    assert (_spent(db, jid), _balance(db, uid), _charged(db)) == (1, 99, [1])


async def test_a_store_that_raises_leaves_no_charge_and_no_spend(db, monkeypatch):
    from app.lists import rows

    uid = _user(db, credits=100)
    jid, _, ids = _people_list(db, uid, n=2)
    _emails(monkeypatch, ids)

    def broken(*args):
        raise RuntimeError('the store failed')

    monkeypatch.setattr(rows, 'ensure_columns', broken)
    await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': ids})]), ('Done.', [])])

    assert (_spent(db, jid), _balance(db, uid), _charged(db)) == (0, 100, [])
    assert db.execute('SELECT status FROM jobs WHERE id = %s', (jid,)).fetchone()['status'] == 'failed'


async def test_a_store_that_raises_after_the_charge_rolls_the_charge_back(db, monkeypatch):
    from app.agent.turn_credits import TurnCredits

    uid = _user(db, credits=100)
    jid, list_id, ids = _people_list(db, uid, n=2)
    _emails(monkeypatch, ids)
    record = TurnCredits.record

    def record_then_fail(self, *args):
        record(self, *args)
        raise RuntimeError('the store failed after the charge')

    monkeypatch.setattr(TurnCredits, 'record', record_then_fail)
    await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': ids})]), ('Done.', [])])

    assert (_spent(db, jid), _balance(db, uid), _charged(db), _stored(db, list_id)) == (0, 100, [], 0)


async def test_a_found_update_that_does_not_land_is_not_billed_when_a_miss_on_its_row_lands(db, monkeypatch):
    uid = _user(db, credits=100)
    jid, _, ids = _people_list(db, uid, n=1)
    db.execute('UPDATE list_rows SET cells = cells || \'{"email": "kept@acme.test"}\' WHERE id = %s', (ids[0],))

    async def handle(args, ctx):
        found = {'row_id': ids[0], 'cells': {'email': 'new@acme.test'}, 'if_empty': 'email'}
        updates = [{'row_id': ids[0], 'cells': {'email_status': 'unknown'}}, found]
        return ToolResult(ok=True, row_updates=updates, outcome=searched('email', updates))

    schema = {'type': 'object', 'properties': {'row_ids': {'type': 'array'}}}
    monkeypatch.setitem(registry._REGISTRY, 'emails', ToolDef('emails', 'test', schema, handle, price=EMAIL))

    await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': ids})]), ('Done.', [])])

    assert (_spent(db, jid), _balance(db, uid), _charged(db)) == (0, 100, [])
    cells = db.execute('SELECT cells FROM list_rows WHERE id = %s', (ids[0],)).fetchone()['cells']
    assert (cells['email'], cells['email_status']) == ('kept@acme.test', 'unknown')


async def test_one_row_named_in_two_uuid_spellings_bills_once(db, monkeypatch):
    uid = _user(db, credits=100)
    jid, _, ids = _people_list(db, uid, n=1)
    both = [ids[0], ids[0].upper()]
    _emails(monkeypatch, both)

    await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': both})]), ('Done.', [])])

    assert (_spent(db, jid), _balance(db, uid), _charged(db)) == (1, 99, [1])


async def test_found_rows_past_the_quote_are_neither_stored_nor_billed(db, monkeypatch):
    uid = _user(db, credits=100)
    jid, list_id, ids = _people_list(db, uid, n=3)
    _emails(monkeypatch, ids)

    await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': ids[:2]})]), ('Done.', [])])

    assert (_spent(db, jid), _balance(db, uid), _charged(db), _stored(db, list_id)) == (2, 98, [1, 1], 2)
    third = db.execute('SELECT cells FROM list_rows WHERE id = %s', (ids[2],)).fetchone()['cells']
    assert 'email' not in third


async def test_each_row_pays_the_price_of_one_row(db, monkeypatch):
    uid = _user(db, credits=100)
    jid, _, ids = _people_list(db, uid, n=3)
    _emails(monkeypatch, ids, price=Price(credits=7, keys=EMAIL.keys, found=EMAIL.found))

    await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': ids})]), ('Done.', [])])

    assert (_spent(db, jid), _balance(db, uid), _charged(db)) == (21, 79, [7, 7, 7])


def _email_vendor(monkeypatch):
    from app.agent.tools.handlers import enrich_email

    async def finds(p, *_):
        return {'row_id': p['row_id'], 'cells': {'email': f'{p["row_id"]}@acme.test', 'email_status': 'verified'}}

    monkeypatch.setattr(enrich_email, '_enrich_one', finds)


def _phone_vendor(monkeypatch):
    from app.agent.tools.handlers import enrich_phone

    async def finds(p, *_):
        return {'row_id': p['row_id'], 'cells': {'phone': '+39 02 1234567', 'phone_status': 'revealed'}}

    monkeypatch.setattr(enrich_phone, '_phone_one', finds)


VENDORS = {'enrich_email': _email_vendor, 'enrich_phone': _phone_vendor}
PAID = sorted(t.name for t in registry.all_tools() if t.price and t.handler.__module__.startswith(handlers.__name__))


def test_every_paid_tool_has_a_vendor_that_finds_rows():
    assert set(PAID) == set(VENDORS)


async def _bills_each_found_row(db, name: str, n: int) -> None:
    uid = _user(db, credits=100)
    jid, _, ids = _people_list(db, uid, n=n)

    await _run(jid, [('', [ToolCall('c1', name, {'row_ids': ids})]), ('Done.', [])])

    q = "SELECT credits FROM row_charges WHERE job_id = %s AND state <> 'refunded'"
    assert [r['credits'] for r in db.execute(q, (jid,))] == [registry._REGISTRY[name].price.credits] * n


@pytest.mark.parametrize('name', PAID)
async def test_every_paid_tool_records_one_charge_per_row_it_finds(db, monkeypatch, keys, name):
    assert name in VENDORS, f'{name} is paid: add to VENDORS a fake vendor that finds a row for each person'
    VENDORS[name](monkeypatch)

    await _bills_each_found_row(db, name, 3)


async def test_a_paid_tool_that_finds_rows_and_records_none_fails_the_check(db, monkeypatch):
    async def finds_in_data_only(args, ctx):
        return ToolResult(ok=True, data={'emails': [f'{r}@acme.test' for r in args['row_ids']]}, entity='person')

    _emails(monkeypatch, [])
    monkeypatch.setitem(registry._REGISTRY, 'emails', replace(registry._REGISTRY['emails'], handler=finds_in_data_only))

    with pytest.raises(AssertionError):
        await _bills_each_found_row(db, 'emails', 3)


async def test_the_result_names_the_rows_still_missing_a_required_column(db, monkeypatch):
    uid = _user(db, credits=100)
    jid, list_id, ids = _people_list(db, uid, n=2)
    for position, key in enumerate(('email', 'phone')):
        db.execute(
            "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, %s, %s, 'code', %s,"
            ' \'{"required": true}\')',
            (list_id, key, key, position),
        )
    db.execute('UPDATE list_rows SET cells = cells || \'{"phone": "+390000"}\' WHERE id = %s', (ids[1],))
    _emails(monkeypatch, ids)

    events, _ = await _run(jid, [('', [ToolCall('e1', 'emails', {'row_ids': ids})]), ('Done.', [])])

    [preview] = [d['result_preview'] for e, d in events if e == 'tool_result']
    assert json.loads(preview)['list']['undelivered'] == {ids[0]: ['phone']}


def test_a_row_with_an_email_and_no_email_status_is_not_missing_the_status(db):
    from app.lists.rows import missing_required

    _, list_id, ids = _people_list(db, _user(db, credits=1), n=2)
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'email_status', 'S',"
        " 'code', 60, '{\"required\": true}')",
        (list_id,),
    )
    db.execute('UPDATE list_rows SET cells = cells || \'{"email": "a@acme.test"}\' WHERE id = %s', (ids[0],))

    assert missing_required(db, list_id, ids) == {ids[1]: ['email_status']}
