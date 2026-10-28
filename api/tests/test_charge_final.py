"""#416: a per-row charge on a row that is unfinished when it is charged stays pending until its turn ends.
The list hides the cells such a charge bought while that turn runs, and the end-of-turn refund takes back only
those: a charge on a complete row is final at once, and an exclusion or a hand edit after the charge keeps it."""

import asyncio
import contextlib
import dataclasses
import json

import pytest
from psycopg.types.json import Jsonb

from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.credits import Hold
from app.billing.price import Price
from app.billing.tab import Tab
from app.lists.lookup_outcome import searched
from app.llm.base import ToolCall
from tests.conftest import call_as, hold, member
from tests.test_agent import FakeProvider, _balance, _job, _run, _user, as_starter, needs_db
from tests.test_agent import db as db
from tests.test_plan import _fake_people, _list_row_ids, _plan_args
from tests.web_fixtures import web_fixture, web_frames, web_row


def _peek(monkeypatch, look):
    """A free tool that runs ``look(list_id)`` mid-turn: what the user does in the list while the turn runs."""

    async def peek(args, ctx):
        await asyncio.to_thread(look, ctx.list_id)
        return ToolResult(ok=True, data={})

    monkeypatch.setitem(registry._REGISTRY, 'peek', ToolDef('peek', 'test', {}, peek))


def _cy_unanswered(monkeypatch):
    """The people of test_plan plus Cy, complete, whose email search gets no answer: ``plan_emails`` finds Ada's and
    Bo's and reports Cy as failed, unbilled, as ``enrich_email`` does."""

    async def people(args, ctx):
        found = await _fake_people(args, ctx)
        cy = {'full_name': 'Cy Neri', 'title': 'CTO', 'linkedin_url': 'https://linkedin.com/in/cy'}
        return dataclasses.replace(found, rows=[*found.rows, cy])

    async def emails(args, ctx):
        *ids, cy = _list_row_ids(ctx.list_id)
        found = [
            {'row_id': r, 'cells': {'email': f'{i}@acme.test', 'email_status': 'verified'}} for i, r in enumerate(ids)
        ]
        updates = [*found, {'row_id': cy, 'cells': {'email_status': 'failed'}}]
        return ToolResult(
            ok=True,
            row_updates=updates,
            outcome=searched('email', updates),
            entity='person',
            # no row id: the chat frames carry this data, and the web fixture must not change from run to run
            data={'searched': len(ids) + 1, 'found': len(ids), 'failed': [{'name': 'Cy Neri'}]},
        )

    for name, handler in (('plan_people', people), ('plan_emails', emails)):
        monkeypatch.setitem(registry._REGISTRY, name, dataclasses.replace(registry._REGISTRY[name], handler=handler))


def _phone_required() -> dict:
    """A later plan on the same list that makes ``phone`` required: Ada, complete until then, is not any more."""
    plan = _plan_args(rows=2, email_units=1)
    return {**plan, 'mode': 'enrich', 'columns': [*plan['columns'], {'key': 'phone', 'required': True}]}


async def _turn(db, monkeypatch, look, *more, on_frame=lambda event, data: None):
    """The plan of test_plan (title required; Bo has none), then ``look(list_id, user)`` mid-turn, then ``more``.
    ``on_frame`` sees each frame as the turn emits it."""
    from app.agent.loop import run_job

    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, '2 CTOs with emails')
    user = member(db, uid)
    _peek(monkeypatch, lambda list_id: look(list_id, user))
    events: list[tuple[str, dict]] = []

    def emit(event, data):
        events.append((event, data))
        on_frame(event, data)

    with as_starter(jid):
        await run_job(
            jid,
            emit=emit,
            provider=FakeProvider(
                [
                    ('', [ToolCall('p1', 'propose_plan', _plan_args(rows=2, email_units=2))]),
                    ('', [ToolCall('s1', 'plan_people', {})]),
                    ('', [ToolCall('e1', 'plan_emails', {})]),  # 2 credits for each email
                    ('', [ToolCall('k1', 'peek', {})]),
                    *more,
                    ('Done.', []),
                ]
            ),
        )
    list_id = db.execute('SELECT id FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()['id']
    return uid, sid, user, list_id, events


def _rows(list_id, user) -> dict[str, dict]:
    from app.lists.routes import get_rows

    rows = call_as(user, get_rows, list_id, user, cursor=0, limit=100)['rows']
    # the web rejects the whole list when one cell_meta entry has no status (web/lib/lists/api.ts rowSchema)
    assert all('status' in meta for r in rows for meta in r['cell_meta'].values()), [r['cell_meta'] for r in rows]
    return {r['cells']['full_name']: r for r in rows}


def _charges(db, list_id) -> dict[str, list[str]]:
    """The states of each row's charge records, oldest first, by name."""
    charges: dict[str, list[str]] = {}
    for r in db.execute(
        "SELECT r.cells->>'full_name' AS name, c.state FROM row_charges c JOIN list_rows r ON r.id = c.row_id"
        ' WHERE r.list_id = %s ORDER BY c.created_at',
        (list_id,),
    ):
        charges.setdefault(r['name'], []).append(r['state'])
    return charges


def _cells(list_id, user) -> dict[str, dict]:
    return {name: r['cells'] for name, r in _rows(list_id, user).items()}


def _patch_bo(db, list_id, user, **cells):
    from app.lists.routes import RowPatch, patch_row

    bo = db.execute("SELECT id FROM list_rows WHERE list_id = %s AND cells->>'full_name' = 'Bo Bianchi'", (list_id,))
    return call_as(user, patch_row, list_id, bo.fetchone()['id'], RowPatch(cells=cells), user)


@needs_db
async def test_a_row_unfinished_when_charged_shows_no_paid_cell_until_the_turn_refunds_it(db, monkeypatch):
    seen: dict[str, dict] = {}
    uid, _, user, list_id, events = await _turn(db, monkeypatch, lambda lid, user: seen.update(_cells(lid, user)))

    assert seen['Ada Rossi']['email'] == '0@acme.test'  # complete when charged: final at once
    assert not {'email', 'email_status'} & seen['Bo Bianchi'].keys()
    refund = next(d for e, d in events if e == 'credits' and d.get('refund') and d['tool_id'] == 'e1')
    assert {k: v for k, v in refund.items() if k != 'summary'} == {
        'tool_id': 'e1',
        'tool_name': 'plan_emails',
        'cost': -2,
        'remaining': 98,
        'refund': True,
    }
    assert (
        refund['summary']['kind'] == 'all_found' and refund['summary']['refunded'] == 1
    )  # the whole summary, refunded
    assert _balance(db, uid) == 98
    after = _cells(list_id, user)
    assert after['Ada Rossi']['email'] == '0@acme.test' and 'email' not in after['Bo Bianchi']


@needs_db
@pytest.mark.web_fixture
async def test_the_list_and_the_chat_agree_on_a_refunded_email(db, monkeypatch):
    """#659: mid-turn the list reads Bo's pending email as searching, and after the turn as refunded. The call that
    bought it gives its credits back and counts the row in its summary, so the chip and the back office say so.
    The refund frame carries the whole summary, so the web replaces it and merges nothing."""
    _cy_unanswered(monkeypatch)
    seen: dict[str, dict] = {}
    sent: list[list] = []

    def send(event, data):
        if data.get('tool_id') == 'e1':  # a copy: the turn later adds the refund to the summary it sent
            sent.append([event, json.loads(json.dumps(data))])

    _, sid, user, list_id, _ = await _turn(
        db, monkeypatch, lambda lid, user: seen.update(_rows(lid, user)), on_frame=send
    )

    assert seen['Ada Rossi']['lookups']['email'] == {'state': 'found', 'verdict': 'verified'}
    assert seen['Bo Bianchi']['lookups']['email'] == {'state': 'searching'}
    bo = _rows(list_id, user)['Bo Bianchi']
    assert bo['lookups']['email'] == {'state': 'refunded'}
    calls = db.execute("SELECT tool_calls FROM chat_messages WHERE session_id = %s AND role = 'assistant'", (sid,))
    e1 = next(c for c in calls.fetchone()['tool_calls'] if c['id'] == 'e1')
    assert e1['credits_used'] == 2  # 2 emails at 2 credits, Bo's given back
    assert e1['summary'] == {
        'kind': 'partial',
        'contact': 'email',
        'searched': 3,
        'tally': {'found': 2, 'invalid': 0, 'not_found': 0, 'failed': 1, 'no_source': 0, 'no_input': 0},
        'unmatched': 0,
        'refunded': 1,
    }
    web_row('email-searching', seen['Bo Bianchi'])
    web_row('email-refunded', bo)
    web_fixture('summary-email-refunded', e1['summary'])
    list_rows = db.execute('SELECT id::text FROM list_rows WHERE list_id = %s ORDER BY seq', (list_id,)).fetchall()
    web_frames('frames-email-refunded', sent, str(list_id), [r['id'] for r in list_rows])


@needs_db
async def test_a_call_that_fails_after_it_charged_still_gives_back_what_the_list_took_back(db, monkeypatch):
    """#659 review: the tool_result frame of e1 fails to send (a Redis error), so the turn ends before it records
    the call. Bo's email is still taken back, and so are its credits."""

    def redis_down(event, data):
        if event == 'tool_result' and data.get('tool_id') == 'e1':
            raise ConnectionError('redis down')

    uid, _, user, list_id, _ = await _turn(db, monkeypatch, lambda lid, user: None, on_frame=redis_down)

    assert _rows(list_id, user)['Bo Bianchi']['lookups']['email'] == {'state': 'refunded'}
    assert _balance(db, uid) == 98  # Ada's 2 credits; Bo's 2 come back


def test_a_refund_frame_that_fails_to_send_loses_no_refund(monkeypatch):
    """Two calls each lose a row; the first refund frame fails to send. Both calls still give their credits back."""
    from types import SimpleNamespace

    from app.agent import loop, turn_credits
    from app.billing import row_charges

    refunds = [row_charges.Refund('e1', 'r1', 2), row_charges.Refund('e2', 'r2', 2)]
    conn = SimpleNamespace(execute=lambda *args: None)
    monkeypatch.setattr(turn_credits, 'get_conn', lambda timeout=None: contextlib.nullcontext(conn))
    monkeypatch.setattr(Tab, 'refund_unfinished', lambda self, conn: refunds)
    monkeypatch.setattr('app.billing.credits.close', lambda conn, hold_id: 1)
    monkeypatch.setattr(row_charges, 'settled_lists', lambda conn, hold_id: ['l1'])
    monkeypatch.setattr('app.billing.balance.changed', lambda org_id: None)

    def emit(event, data):
        if data.get('refund'):
            raise ConnectionError('redis down')

    calls = [{'id': c, 'name': 'enrich_email', 'credits_used': 2} for c in ('e1', 'e2')]
    credits = turn_credits.TurnCredits('j', 'u', 's')
    credits.spent, credits.tab = 5, Tab(Hold('h', 5))
    credits.balance = 100
    turn = SimpleNamespace(credits=credits, tool_calls=calls, lists={}, emit=emit)
    turn._emit_credits = lambda *a, **k: loop.Turn._emit_credits(turn, *a, **k)
    with pytest.raises(ConnectionError):
        loop.Turn._finish_credits(turn)
    assert (credits.spent, credits.refunded, [c['credits_used'] for c in calls]) == (1, 4, [0, 0])


def _require(db, list_id, *keys):
    """Adds required columns to the list, as a plan the user approves elsewhere or a column edit would."""
    for key in keys:
        db.execute(
            'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
            " VALUES (%s, %s, %s, 'static', 99, '{\"required\": true}')",
            (list_id, key, key),
        )


@needs_db
async def test_a_charge_on_a_complete_row_survives_a_required_column_added_later_in_the_turn(db, monkeypatch):
    """After the user reads Ada's email, ``phone`` becomes required, so Ada is unfinished when the turn ends."""
    seen: dict[str, dict] = {}

    def read_then_require_phone(list_id, user):
        seen.update(_cells(list_id, user))
        _require(db, list_id, 'phone')

    uid, _, user, list_id, _ = await _turn(db, monkeypatch, read_then_require_phone)

    required = db.execute(
        "SELECT key FROM list_columns WHERE list_id = %s AND config->>'required' = 'true'", (list_id,)
    )
    assert 'phone' in [r['key'] for r in required]
    assert seen['Ada Rossi']['email'] == '0@acme.test'  # the user read it mid-turn
    assert _cells(list_id, user)['Ada Rossi']['email'] == '0@acme.test'
    assert _balance(db, uid) == 98  # only Bo's 2 credits come back


@needs_db
async def test_an_email_paid_in_an_earlier_turn_stays_visible_while_a_later_turn_runs(db, monkeypatch):
    uid, sid, user, list_id, _ = await _turn(db, monkeypatch, lambda lid, user: None)
    seen: dict[str, dict] = {}
    _peek(monkeypatch, lambda lid: seen.update(_cells(list_id, user)))
    _, jid = _job(db, uid, 'now find their phones', session_id=sid)

    await _run(
        jid,
        [('', [ToolCall('p2', 'propose_plan', _phone_required())]), ('', [ToolCall('k2', 'peek', {})]), ('Done.', [])],
    )

    assert seen['Ada Rossi']['email'] == '0@acme.test'
    assert _cells(list_id, user)['Ada Rossi']['email'] == '0@acme.test'
    assert _balance(db, uid) == 98


def _running_list(db, cells: dict, cell_meta: dict, minutes_ago=0, held=False, close=None):
    """A one-row person list whose chat has a running job, last updated ``minutes_ago``; ``held``: that job's hold
    holds a charge on the email. ``close`` closes the hold as its holder does (``settle``), as the reaper does
    (``reap``), or with no charge made final (``release``)."""
    from app.lists.rows import add_rows, create_list

    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, 'now find their phones')
    db.execute(
        "UPDATE jobs SET status = 'running', updated_at = now() - make_interval(mins => %s) WHERE id = %s",
        (minutes_ago, jid),
    )
    lid = create_list(db, user_id=uid, session_id=sid, name='CTOs', entity='person')
    (rid,) = add_rows(db, lid, 'person', [cells])
    db.execute('UPDATE list_rows SET cell_meta = %s WHERE id = %s', (Jsonb(cell_meta), rid))
    if held:
        hid = hold(db, uid, jid)
        db.execute(
            "INSERT INTO row_charges (hold_id, job_id, row_id, keys, credits, state) VALUES (%s, %s, %s, '{email}', 1,"
            " 'held')",
            (hid, jid, rid),
        )
        if close == 'reap':
            db.execute("UPDATE credit_transactions SET created_at = created_at - interval '30 minutes'")
            db.execute('SELECT reap_stale_reservations_ids(20)').fetchall()
        elif close:
            db.execute(f'SELECT {"settle_hold" if close == "settle" else "release_reservation"}(%s)', (hid,))
    _require(db, lid, 'phone')
    return _cells(lid, member(db, uid))['Ada Rossi']


@needs_db
def test_an_uploaded_email_stays_visible_while_a_turn_runs(db):
    ada = _running_list(
        db,
        {'full_name': 'Ada Rossi', 'email': 'ada@acme.test'},
        {'full_name': {'status': 'manual'}, 'email': {'status': 'manual'}},
    )

    assert ada['email'] == 'ada@acme.test'


@needs_db
@pytest.mark.parametrize(
    ('minutes_ago', 'close', 'hidden'),
    [(0, None, True), (21, None, True), (0, 'settle', False), (21, 'reap', False), (0, 'release', False)],
)
def test_a_pending_cell_hides_only_while_its_hold_is_reserved(db, minutes_ago, close, hidden):
    """A turn whose worker died never settles its charges: the cell shows once the reaper closes its hold. A job
    that only looks lost still holds its hold, so its cell stays hidden."""
    ada = _running_list(
        db,
        {'full_name': 'Ada Rossi', 'email': 'ada@acme.test'},
        {'email': {'status': 'filled'}},
        minutes_ago,
        held=True,
        close=close,
    )

    assert ('email' not in ada) is hidden


@needs_db
async def test_no_charge_stays_pending_after_its_turn(db, monkeypatch):
    seen: dict[str, list[str]] = {}
    _, _, _, list_id, _ = await _turn(db, monkeypatch, lambda lid, user: seen.update(_charges(db, lid)))

    # Bo's is the charge the refund may take back; Ada's was final at once
    assert seen == {'Ada Rossi': ['charged'], 'Bo Bianchi': ['held']}
    assert _charges(db, list_id) == {'Ada Rossi': ['charged'], 'Bo Bianchi': ['refunded']}


@needs_db
async def test_a_row_completed_after_a_pending_charge_keeps_it_and_shows_the_cell_after_the_turn(db, monkeypatch):
    seen: dict[str, dict] = {}

    def complete_bo(list_id, user):
        seen.update({'Bo Bianchi': _patch_bo(db, list_id, user, title='CEO')['cells']})

    uid, _, user, list_id, _ = await _turn(db, monkeypatch, complete_bo)

    assert 'email' not in seen['Bo Bianchi']  # still pending while the turn runs
    bo = _rows(list_id, user)['Bo Bianchi']
    assert bo['cells']['email'] == '1@acme.test'
    assert _charges(db, list_id)['Bo Bianchi'] == ['charged']  # the charge is final
    assert _balance(db, uid) == 96  # 2 for each email, both final


@needs_db
async def test_the_end_of_the_turn_tells_the_web_to_reload_a_list_whose_pending_cells_it_kept(db, monkeypatch):
    """The web refetches rows only on ``list_updated``: without one, a kept email stays hidden until a reload."""
    reloads: list[dict] = []

    def reload(event, data):
        if event == 'list_updated':
            owner = db.execute('SELECT user_id FROM lists WHERE id = %s', (data['list_id'],)).fetchone()['user_id']
            reloads.append({**data, 'bo': _cells(data['list_id'], member(db, owner))['Bo Bianchi']})

    uid, _, _, list_id, _ = await _turn(
        db, monkeypatch, lambda lid, user: _patch_bo(db, lid, user, title='CEO'), on_frame=reload
    )

    last = reloads[-1]  # the job still runs when the web reloads on it
    assert (last['list_id'], last['rows_updated'], last['bo'].get('email')) == (str(list_id), 1, '1@acme.test')
    assert _balance(db, uid) == 96


@needs_db
async def test_a_required_cell_cleared_by_hand_after_a_pending_row_was_completed_keeps_the_charge(db, monkeypatch):
    """The user could read Bo's email while his row was complete, so clearing his title does not refund it."""

    def complete_then_clear_bo(list_id, user):
        _patch_bo(db, list_id, user, title='CEO')
        _patch_bo(db, list_id, user, title='')

    uid, _, user, list_id, _ = await _turn(db, monkeypatch, complete_then_clear_bo)

    assert _balance(db, uid) == 96
    assert _cells(list_id, user)['Bo Bianchi']['email'] == '1@acme.test'


@needs_db
async def test_an_export_during_a_turn_leaves_out_a_pending_cell(db, monkeypatch):
    """Turn 2 buys Bo's email again; the user completes his row and exports; then the turn's first plan makes
    ``phone`` required, so Bo is unfinished again and the turn refunds the email the export must not hold."""
    from app.lists.export_routes import _export

    uid, sid, user, list_id, _ = await _turn(db, monkeypatch, lambda lid, user: None)  # Bo's email refunded
    bo = _rows(list_id, user)['Bo Bianchi']['id']
    exported: dict[str, dict] = {}

    def complete_bo_and_export(lid):
        _patch_bo(db, list_id, user, title='CEO')
        exported.update(
            {
                r['cells']['full_name']: r['cells']
                for r in call_as(user, _export, list_id, user, 'all', None, False, False)[2]
            }
        )

    _peek(monkeypatch, complete_bo_and_export)
    _, jid = _job(db, uid, "find Bo's email again", session_id=sid)
    await _run(
        jid,
        [
            ('', [ToolCall('e2', 'plan_emails', {'row_ids': [str(bo)]})]),
            ('', [ToolCall('k2', 'peek', {})]),
            ('', [ToolCall('p2', 'propose_plan', _phone_required())]),
            ('Done.', []),
        ],
    )

    assert exported['Ada Rossi']['email'] == '0@acme.test'
    assert 'email' not in exported['Bo Bianchi']
    assert _balance(db, uid) == 98  # turn 2's 2 credits came back


@needs_db
async def test_a_campaign_drafted_during_a_turn_enrolls_no_row_whose_email_the_turn_refunds(db, monkeypatch):
    """#462: Bo, unfinished when his email is charged, is no target; Ada, complete, is one with her email."""
    from app.core.db import get_conn
    from app.outreach.drafts import draft_from_list

    def draft(list_id, user):
        with get_conn() as conn:
            draft_from_list(conn, user.id, list_id, 'CTOs', reuse=False)

    uid, _, _, _, _ = await _turn(db, monkeypatch, draft)

    targets = db.execute('SELECT first_name, email FROM outreach_campaign_targets').fetchall()
    assert [(t['first_name'], t['email']) for t in targets] == [('Ada', '0@acme.test')]
    assert _balance(db, uid) == 98  # Bo's email came back


def _complete_bo_and_draft(db):
    """The user completes Bo, whose email charge is pending, then drafts a campaign from the list."""
    from app.core.db import get_conn
    from app.outreach.drafts import draft_from_list

    def look(list_id, user):
        _patch_bo(db, list_id, user, title='CEO')
        with get_conn() as conn:
            draft_from_list(conn, user.id, list_id, 'CTOs', reuse=False)

    return look


def _target_emails(db) -> dict[str, str | None]:
    return {t['first_name']: t['email'] for t in db.execute('SELECT * FROM outreach_campaign_targets')}


@needs_db
async def test_a_row_completed_during_the_turn_is_drafted_with_its_email_and_keeps_the_charge(db, monkeypatch):
    """The web refetches rows only on ``list_updated``: one must follow the draft, or Bo's email stays hidden."""
    drafted: list[bool] = []
    reloads: list[dict] = []
    complete_bo_and_draft = _complete_bo_and_draft(db)

    def look(list_id, user):
        complete_bo_and_draft(list_id, user)
        drafted.append(True)

    def reload(event, data):
        if event == 'list_updated' and drafted:
            owner = db.execute('SELECT user_id FROM lists WHERE id = %s', (data['list_id'],)).fetchone()['user_id']
            reloads.append({'list_id': data['list_id'], **_cells(data['list_id'], member(db, owner))['Bo Bianchi']})

    uid, _, user, list_id, _ = await _turn(db, monkeypatch, look, on_frame=reload)

    assert _target_emails(db) == {'Ada': '0@acme.test', 'Bo': '1@acme.test'}
    assert _balance(db, uid) == 96  # 2 for each email, both final
    assert _cells(list_id, user)['Bo Bianchi']['email'] == '1@acme.test'
    assert [(r['list_id'], r.get('email')) for r in reloads] == [(str(list_id), '1@acme.test')]


@needs_db
async def test_an_email_that_left_for_a_campaign_is_never_refunded(db, monkeypatch):
    """After the draft, ``phone`` becomes required, so Bo is unfinished again when the turn ends."""
    complete_bo_and_draft = _complete_bo_and_draft(db)

    def draft_then_require_phone(list_id, user):
        complete_bo_and_draft(list_id, user)
        _require(db, list_id, 'phone')

    uid, _, user, list_id, _ = await _turn(db, monkeypatch, draft_then_require_phone)

    assert _target_emails(db)['Bo'] == '1@acme.test'
    assert _balance(db, uid) == 96
    assert _cells(list_id, user)['Bo Bianchi']['email'] == '1@acme.test'


def _bo_phone(monkeypatch):
    """A per-row tool like ``enrich_phone`` at 1 credit: it finds Bo's phone."""
    from app.core.db import get_conn

    async def bo_phone(args, ctx):
        with get_conn() as conn:
            q = "SELECT id::text FROM list_rows WHERE list_id = %s AND cells->>'full_name' = 'Bo Bianchi'"
            bo = conn.execute(q, (ctx.list_id,)).fetchone()['id']
        updates = [{'row_id': bo, 'cells': {'phone': '+39 02 0000', 'phone_status': 'valid'}}]
        return ToolResult(
            ok=True,
            row_updates=updates,
            outcome=searched('phone', updates),
            entity='person',
        )

    price = Price(credits=1, keys=('phone', 'phone_status'), found=lambda cells: bool(cells.get('phone')))
    monkeypatch.setitem(registry._REGISTRY, 'bo_phone', ToolDef('bo_phone', 'test', {}, bo_phone, price=price))


@needs_db
@pytest.mark.web_fixture
async def test_a_phone_bought_for_an_unfinished_row_reads_searching_until_the_turn_refunds_it(db, monkeypatch):
    _bo_phone(monkeypatch)
    seen: list[dict] = []  # Bo as each peek reads him: before the phone search, then after it

    uid, _, user, list_id, _ = await _turn(
        db,
        monkeypatch,
        lambda lid, user: seen.append(_rows(lid, user)['Bo Bianchi']),
        ('', [ToolCall('f1', 'bo_phone', {})]),
        ('', [ToolCall('k2', 'peek', {})]),
    )

    assert [b['lookups']['phone'] for b in seen] == [{'state': 'not_searched'}, {'state': 'searching'}]
    bo = _rows(list_id, user)['Bo Bianchi']
    assert bo['lookups']['phone'] == {'state': 'refunded'} and 'phone' not in bo['cells']
    assert _balance(db, uid) == 98  # 2 for Ada's email; Bo's email and phone came back
    web_row('phone-searching', seen[-1])
    web_row('phone-refunded', bo)


@needs_db
async def test_a_later_refund_on_a_drafted_row_returns_only_the_charge_it_takes_back(db, monkeypatch):
    """After the draft, ``phone`` and ``seniority`` become required. The turn then buys Bo's phone, and Bo is still
    unfinished at its end: the turn refunds the phone, not the email that left for the campaign."""
    _bo_phone(monkeypatch)
    complete_bo_and_draft = _complete_bo_and_draft(db)

    def draft_then_require_phone_and_seniority(list_id, user):
        complete_bo_and_draft(list_id, user)
        _require(db, list_id, 'phone', 'seniority')

    uid, _, user, list_id, events = await _turn(
        db, monkeypatch, draft_then_require_phone_and_seniority, ('', [ToolCall('f1', 'bo_phone', {})])
    )

    refund = next(d for e, d in events if e == 'credits' and d.get('refund') and d['tool_id'] == 'f1')
    assert {k: v for k, v in refund.items() if k != 'summary'} == {
        'tool_id': 'f1',
        'tool_name': 'bo_phone',
        'cost': -1,
        'remaining': 96,
        'refund': True,
    }
    assert refund['summary']['kind'] == 'all_found' and refund['summary']['refunded'] == 1
    assert _balance(db, uid) == 96  # 2 for each email; the phone came back
    bo = _cells(list_id, user)['Bo Bianchi']
    assert bo['email'] == '1@acme.test' and 'phone' not in bo


@needs_db
async def test_an_exclusion_after_the_charge_keeps_the_charge(db, monkeypatch):
    from app.lists.routes import RowPatch, patch_row

    excluded: dict[str, dict] = {}

    def exclude_all(list_id, user):
        ids = db.execute("SELECT id, cells->>'full_name' AS name FROM list_rows WHERE list_id = %s", (list_id,))
        for r in ids.fetchall():
            excluded[r['name']] = patch_row(list_id, r['id'], RowPatch(excluded=True), user)['cells']

    uid, _, _, _, _ = await _turn(db, monkeypatch, exclude_all)

    # 2 for each email; only Bo was unfinished when charged, so only his 2 come back
    assert _balance(db, uid) == 98
    assert excluded['Ada Rossi']['email'] == '0@acme.test'
    assert 'email' not in excluded['Bo Bianchi']  # the patch response hides it too
    ada = db.execute("SELECT cells FROM list_rows WHERE cells->>'full_name' = 'Ada Rossi'").fetchone()
    assert ada['cells']['email'] == '0@acme.test'


@needs_db
async def test_clearing_a_required_cell_after_the_charge_keeps_the_charge(db, monkeypatch):
    from app.lists.routes import RowPatch, patch_row

    def clear_ada_title(list_id, user):
        ada = db.execute("SELECT id FROM list_rows WHERE cells->>'full_name' = 'Ada Rossi'").fetchone()
        patch_row(list_id, ada['id'], RowPatch(cells={'title': ''}), user)

    uid, _, user, list_id, _ = await _turn(db, monkeypatch, clear_ada_title)

    assert _balance(db, uid) == 98
    assert _cells(list_id, user)['Ada Rossi']['email'] == '0@acme.test'


def test_the_scorer_sees_no_cell_a_tool_bills_per_row():
    """A score reason must not quote a cell the turn may still refund."""
    from app.agent.loop import _score_cells
    from app.agent.tools import handlers  # noqa: F401  (handlers register the tools)

    cells = {'email': 'ada@acme.test', 'email_status': 'verified', 'phone': '+39 02 1234 567', 'title': 'CTO'}
    assert _score_cells(cells) == {'title': 'CTO'}


@needs_db
@pytest.mark.parametrize(
    ('requires_mobile', 'state', 'email'), [(True, 'held', 'searching'), (False, 'charged', 'found')]
)
def test_a_later_charge_wins_over_an_earlier_refund_of_the_cell(db, requires_mobile, state, email):
    """#540: the row's email was refunded, then a later turn bought it again, on an unfinished row (held while that
    turn runs) or a complete one (final at once). The email no longer reads refunded."""
    from app.billing import row_charges
    from app.lists.row_view import ROW_COLS, with_lookups
    from app.lists.rows import add_rows, create_list

    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, 'find the email again')
    db.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (jid,))
    lid = create_list(db, user_id=uid, session_id=sid, name='CTOs', entity='person')
    cells = {'full_name': 'Ada Rossi', 'title': 'CTO', 'email': 'ada@acme.test', 'email_status': 'verified'}
    (rid,) = add_rows(db, lid, 'person', [cells])
    if requires_mobile:
        _require(db, lid, 'mobile')
    db.execute("INSERT INTO row_charges (row_id, keys, credits, state) VALUES (%s, '{email}', 0, 'refunded')", (rid,))

    row_charges.charge(db, hold(db, uid, jid), jid, 'e1', lid, [rid], ('email', 'email_status'), 1)

    assert _charges(db, lid) == {'Ada Rossi': ['refunded', state]}
    row = db.execute(f'SELECT {ROW_COLS} FROM list_rows r WHERE r.id = %s', (rid,)).fetchone()
    assert with_lookups(row)['lookups']['email']['state'] == email
