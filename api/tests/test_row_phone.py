"""#344: «Cerca il telefono» on a person row: found is charged, anything else is not, other orgs get a 404."""

from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from psycopg.types.json import Jsonb

from app.agent.tools.handlers import enrich_phone
from app.agent.tools.registry import ToolContext
from app.core.errors import ok
from app.core.migrate import MIGRATIONS
from app.lists.rows import add_rows, apply_row_updates
from tests.conftest import project
from tests.test_agent import _balance, _chat, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.web_fixtures import web_fixture, wire_row

pytestmark = needs_db


class FakeRedis:
    held: ClassVar[dict[str, str]] = {}

    def set(self, key, value, nx=False, ex=None):
        if key in self.held:
            return None
        self.held[key] = value
        return True

    def eval(self, script, n, key, token):
        if self.held.get(key) == token:
            del self.held[key]


@pytest.fixture(autouse=True)
def vendors(monkeypatch, keys):
    FakeRedis.held = {}
    monkeypatch.setattr('app.lists.row_phone.redis.Redis.from_url', lambda *a, **k: FakeRedis())
    monkeypatch.setattr(enrich_phone, 'providers_off', lambda providers: frozenset())


def _row(db, uid, cells=None) -> tuple[str, str]:
    sid = _chat(db, uid)
    lid = str(
        db.execute(
            'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity)'
            " VALUES (default_organization(%s), %s, %s, %s, 'People', 'person') RETURNING id",
            (uid, project(db, uid), uid, sid),
        ).fetchone()['id']
    )
    add_rows(db, lid, 'person', [cells or {'full_name': 'Ada Rossi', 'company_domain': 'acme.test'}])
    rid = str(db.execute('SELECT id FROM list_rows WHERE list_id = %s', (lid,)).fetchone()['id'])
    return lid, rid


def _lookup(monkeypatch, outcome):
    async def one(p, apollo, treg, off):
        if isinstance(outcome, Exception):
            raise outcome
        return {'row_id': p['row_id'], 'cells': outcome}

    monkeypatch.setattr(enrich_phone, '_phone_one', one)


def _apollo(numbers: list[dict] | None):
    """Apollo matches the person and its phone job finishes with ``numbers``; ``None``: Apollo is down."""

    def vendor(request: httpx.Request) -> httpx.Response:
        if numbers is None:
            return httpx.Response(503, json={'error': 'down'})
        if request.url.path.endswith('/people/match'):
            return httpx.Response(200, json={'person': {'id': 'x', 'first_name': 'Ada'}, 'request_id': 7})
        return httpx.Response(200, json={'webhook_result': {'people': [{'id': 'x', 'phone_numbers': numbers}]}})

    return vendor


def _phone_fixture(name: str, body: dict) -> None:
    web_fixture(name, {**body, 'row': wire_row(body['row'], name)})


RETRY = '20261009093638_phone_status_retry.sql'
FOUND = {'phone': '+39333111222', 'phone_status': 'revealed', 'phone_source': 'treg'}
MISSING = {'phone': None, 'phone_status': 'not_found'}


@pytest.mark.web_fixture
def test_found_charges_the_tool_price_once(client, db, http):
    c, uid = client
    lid, rid = _row(db, uid)
    http.handler = _apollo([{'sanitized_number': '+39333111222'}])
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 200
    body = res.json()
    assert (body['status'], body['credits']) == ('found', 8)
    assert body['row']['cells']['phone'] == '+39333111222'
    assert body['row']['lookups']['phone'] == {'state': 'found'}
    _phone_fixture('phone-found', body)
    assert not FakeRedis.held
    cols = [r['key'] for r in db.execute('SELECT key FROM list_columns WHERE list_id = %s', (lid,))]
    assert 'phone' in cols  # the paid number has a column to show in
    meta = db.execute('SELECT cell_meta FROM list_rows WHERE id = %s', (rid,)).fetchone()['cell_meta']
    assert meta == {'phone': {'status': 'filled', 'provider': 'apollo'}}
    assert _balance(db, uid) == 50 - 8
    again = c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()
    assert (again['status'], again['credits']) == ('already', 0)
    assert _balance(db, uid) == 50 - 8


@pytest.mark.web_fixture
def test_not_found_is_free_and_remembered(client, db, http):
    c, uid = client
    lid, rid = _row(db, uid)
    http.handler = _apollo([])
    body = c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()
    assert (body['status'], body['credits']) == ('not_found', 0)
    stored = db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']
    assert stored['phone_status'] == 'not_found' and 'phone' not in stored
    assert not {'phone_status', 'phone'} & body['row']['cells'].keys()  # the web reads lookups (public_cells)
    assert body['row']['lookups']['phone'] == {'state': 'not_found'}
    _phone_fixture('phone-not-found', body)
    assert _balance(db, uid) == 50
    assert db.execute("SELECT count(*) AS n FROM credit_transactions WHERE status = 'reserved'").fetchone()['n'] == 0


def test_a_vendor_crash_does_not_raise_nor_charge(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, RuntimeError('boom'))
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 200 and res.json()['status'] == 'failed' and res.json()['credits'] == 0
    assert _balance(db, uid) == 50


@pytest.mark.web_fixture
def test_a_failed_source_is_free_and_leaves_the_row_searchable(client, db, http):
    c, uid = client
    lid, rid = _row(db, uid)
    http.handler = _apollo(None)
    body = c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()
    assert (body['status'], body['credits']) == ('failed', 0)
    assert body['row']['lookups']['phone'] == {'state': 'failed'}  # the web offers the search again
    _phone_fixture('phone-failed', body)
    cells = db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']
    assert cells['phone_status'] == 'failed'
    assert _balance(db, uid) == 50


def test_a_row_that_lost_its_search_hints_meanwhile_answers_no_input(client, db, monkeypatch):
    """#659 review: an API-key PATCH clears the name between the click's check and the search, so the handler finds
    nothing to search by and writes no status."""
    c, uid = client
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, {'phone': None})
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 200
    assert (res.json()['status'], res.json()['credits']) == ('no_input', 0)
    assert _balance(db, uid) == 50


def test_no_vendor_configured_is_unavailable_and_free(client, db, monkeypatch):
    from app.core.config import settings

    c, uid = client
    monkeypatch.setattr(settings, 'apollo_api_key', '')
    lid, rid = _row(db, uid)
    assert c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()['status'] == 'unavailable'
    assert _balance(db, uid) == 50


def test_another_organizations_row_is_a_404(client, db, monkeypatch):
    c, _ = client
    other = _user(db)
    lid, rid = _row(db, other)
    _lookup(monkeypatch, FOUND)
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 404 and res.json()['detail']['code'] == 'ROW.NOT_FOUND'
    assert _balance(db, other) == 50


def test_not_enough_credits_is_refused_before_the_vendor(client, db, monkeypatch):
    c, uid = client
    db.execute('UPDATE user_credits SET credits_remaining = 3 WHERE user_id = %s', (uid,))
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, RuntimeError('must not be called'))
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 402 and res.json()['detail']['code'] == 'ROW.NO_CREDITS'
    assert not FakeRedis.held  # a refused click leaves no lock: the retry after a top-up goes through


def test_a_second_click_while_one_runs_is_refused(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid)
    _lookup(monkeypatch, FOUND)
    FakeRedis.held[f'row_phone:{rid}'] = 'other'
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 409 and res.json()['detail']['code'] == 'ROW.PHONE_RUNNING'
    assert _balance(db, uid) == 50


def test_a_row_the_projects_exclusions_match_is_refused_before_the_hold(client, db, monkeypatch):
    """#550: the rule-excluded row reads as excluded, never as a phone service that is down."""
    c, uid = client
    lid, rid = _row(db, uid)
    db.execute(
        "INSERT INTO exclusions (project_id, kind, value) VALUES (%s, 'domain', 'acme.test')", (project(db, uid),)
    )
    _lookup(monkeypatch, RuntimeError('must not be called'))
    res = c.post(f'/v1/lists/{lid}/rows/{rid}/phone')
    assert res.status_code == 409 and res.json()['detail']['code'] == 'ROW.EXCLUDED'
    assert not FakeRedis.held and _balance(db, uid) == 50


def test_a_company_list_row_is_a_404(client, db):
    c, uid = client
    lid, rid = _row(db, uid)
    db.execute("UPDATE lists SET entity = 'company' WHERE id = %s", (lid,))
    assert c.post(f'/v1/lists/{lid}/rows/{rid}/phone').status_code == 404


def test_a_number_that_arrived_meanwhile_is_kept_and_not_billed(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid)

    async def one(p, apollo, treg, off):
        db.execute('UPDATE list_rows SET cells = cells || \'{"phone": "+39000"}\' WHERE id = %s', (rid,))
        return {'row_id': p['row_id'], 'cells': FOUND}

    monkeypatch.setattr(enrich_phone, '_phone_one', one)
    body = c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()
    assert (body['status'], body['credits'], body['row']['cells']['phone']) == ('already', 0, '+39000')
    assert _balance(db, uid) == 50


async def test_a_chat_lookup_that_found_nothing_never_erases_a_number_a_click_bought(client, db, monkeypatch):
    _, uid = client
    lid, bought = _row(db, uid)
    add_rows(db, lid, 'person', [{'full_name': 'Lia Neri', 'company_domain': 'acme.test'}])
    fresh = str(db.execute('SELECT id FROM list_rows WHERE id <> %s', (bought,)).fetchone()['id'])
    # The chat read both rows before the click stored its number: to the chat, neither has a phone.
    stale = [{'row_id': r, 'linkedin_url': f'https://linkedin.com/in/{r}'} for r in (bought, fresh)]
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: stale)

    class Nothing:
        async def reveal(self, hints):
            return ok(SimpleNamespace(phone=None))

    monkeypatch.setattr(enrich_phone, 'build_reveal_chain', lambda *a: Nothing())
    db.execute('UPDATE list_rows SET cells = cells || %s WHERE id = %s', (Jsonb(FOUND), bought))

    res = await enrich_phone.handle({'row_ids': [bought, fresh]}, ToolContext(user_id=uid, list_id=lid))
    assert len(apply_row_updates(db, lid, 'person', res.row_updates)) == 1  # what the agent loop does with them

    cells = {
        r['id']: r['cells'] for r in db.execute('SELECT id::text, cells FROM list_rows WHERE list_id = %s', (lid,))
    }
    assert {k: cells[bought][k] for k in FOUND} == FOUND
    assert cells[fresh]['phone_status'] == 'not_found'


@pytest.mark.web_fixture
def test_a_row_no_source_can_search_holds_nothing_and_keeps_no_verdict(client, db, monkeypatch):
    c, uid = client
    lid, rid = _row(db, uid, {'last_name': 'Rossi', 'company_domain': 'acme.test'})
    _lookup(monkeypatch, MISSING)  # never reached
    before = _balance(db, uid)
    body = c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()
    assert (body['status'], body['credits']) == ('no_input', 0)
    cells = db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']
    assert 'phone_status' not in cells
    _phone_fixture('phone-no-input', body)
    assert _balance(db, uid) == before


def test_the_migration_offers_old_not_found_rows_the_search_again(client, db):
    _, uid = client
    lid, _ = _row(db, uid, {'full_name': 'Ada Rossi', 'phone_status': 'not_found'})
    add_rows(db, lid, 'person', [{'full_name': 'Bo Bianchi', 'phone': '+39000', 'phone_status': 'revealed'}])
    db.execute((MIGRATIONS / RETRY).read_text())  # the fixture ran it on an empty schema: run it on this data
    cells = {
        r['cells']['full_name']: r['cells']
        for r in db.execute('SELECT cells FROM list_rows WHERE list_id = %s', (lid,))
    }
    assert 'phone_status' not in cells['Ada Rossi']
    assert cells['Bo Bianchi']['phone_status'] == 'revealed'
