"""#659: a lookup a live turn has open reads ``searching`` from its claim, before and after the vendors answer."""

import pytest
from psycopg.types.json import Jsonb

from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.lists import lookup_claims, lookup_outcome
from app.lists.lookups import CHARGES_SQL, OPEN_EMAIL_SQL, email_lookup, phone_lookup
from app.lists.row_view import ROW_COLS, with_lookups
from app.lists.rows import add_rows, create_list
from app.llm.base import ToolCall
from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_row_charges_sql import _turn as _charged_turn

pytestmark = needs_db


def _turn(db, status='running', minutes_ago=0) -> tuple[str, str, str, str]:
    """``_charged_turn`` in ``status``, with its two people never searched. (job, list, ada, bo)."""
    jid, lid, ada, bo = _charged_turn(db)
    db.execute(
        'UPDATE jobs SET status = %s, updated_at = now() - make_interval(mins => %s) WHERE id = %s',
        (status, minutes_ago, jid),
    )
    db.execute("UPDATE list_rows SET cells = cells - 'email' - 'email_status' WHERE list_id = %s", (lid,))
    return jid, lid, ada, bo


def _read(db, row_id: str) -> dict:
    """The row as the API sends it, and whether the next email batch would take it."""
    row = db.execute(
        f'SELECT {ROW_COLS}, {OPEN_EMAIL_SQL} AS open FROM list_rows r WHERE r.id = %s', (row_id,)
    ).fetchone()
    return {**with_lookups(row)['lookups'], 'open': row['open']}


def _states(db, row_id: str) -> tuple[str, str, bool]:
    read = _read(db, row_id)
    return read['email']['state'], read['phone']['state'], read['open']


def test_a_claimed_row_reads_searching_and_no_batch_takes_it(db):
    jid, lid, ada, bo = _turn(db)
    assert _states(db, bo) == ('not_searched', 'not_searched', True)

    lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo])

    assert _states(db, bo) == ('searching', 'not_searched', False)
    assert _states(db, ada) == ('not_searched', 'not_searched', True)  # only the claimed row


def test_a_phone_claim_makes_the_phone_search_and_leaves_the_email_open(db):
    jid, lid, _, bo = _turn(db)
    lookup_claims.claim(db, jid, 'c1', lid, 'phone', [bo])

    assert _states(db, bo) == ('not_searched', 'searching', True)


def test_the_claim_decides_state_only_for_a_row_with_no_value(db):
    jid, lid, _, bo = _turn(db)
    db.execute('UPDATE list_rows SET cells = cells || \'{"email": "bo@acme.it"}\' WHERE id = %s', (bo,))
    lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo])

    assert _read(db, bo)['email'] == {'state': 'found', 'verdict': 'unknown'}


def test_a_retried_claim_changes_nothing(db):
    jid, lid, _, bo = _turn(db)
    lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo])
    lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo])

    assert db.execute('SELECT count(*) AS n FROM lookup_claims').fetchone()['n'] == 1


@pytest.mark.parametrize(
    ('status', 'minutes_ago'), [('running', 30), ('failed', 0), ('succeeded', 0), ('cancelled', 0)]
)
@pytest.mark.parametrize(
    ('cells', 'state'), [({}, 'not_searched'), ({'email': None, 'email_status': 'failed'}, 'failed')]
)
def test_the_claim_of_a_dead_job_stops_counting_at_once(db, status, minutes_ago, cells, state):
    """No janitor ran: the live-job join alone lets the row read its stored answer, and the batch takes it again."""
    jid, lid, _, bo = _turn(db, status, minutes_ago)
    db.execute('UPDATE list_rows SET cells = cells || %s WHERE id = %s', (Jsonb(cells), bo))
    lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo])

    assert _states(db, bo) == (state, 'not_searched', True)


def test_a_released_row_reads_its_answer(db):
    jid, lid, _, bo = _turn(db)
    lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo])
    lookup_claims.claim(db, jid, 'c2', lid, 'email', [bo])
    lookup_claims.release(db, jid, 'c1')
    assert _states(db, bo)[0] == 'searching'  # another call still has it

    lookup_claims.release(db, jid, 'c2')
    db.execute(
        'UPDATE list_rows SET cells = cells || \'{"email": null, "email_status": "not_found"}\' WHERE id = %s', (bo,)
    )

    assert _states(db, bo) == ('not_found', 'not_searched', False)


def test_the_janitor_deletes_the_claims_of_dead_jobs_only(db):
    from app import worker

    live, live_list, _, bo = _turn(db)
    dead, dead_list, _, dead_bo = _turn(db, 'running', 30)
    lookup_claims.claim(db, live, 'c1', live_list, 'email', [bo])
    lookup_claims.claim(db, dead, 'c1', dead_list, 'email', [dead_bo])

    worker.reap_stale_reservations()
    worker.reap_stale_reservations()

    assert [str(r['row_id']) for r in db.execute('SELECT row_id FROM lookup_claims')] == [bo]


def test_deleting_a_row_or_a_job_deletes_its_claims(db):
    jid, lid, ada, bo = _turn(db)
    lookup_claims.claim(db, jid, 'c1', lid, 'email', [ada, bo])
    db.execute('DELETE FROM list_rows WHERE id = %s', (ada,))
    assert db.execute('SELECT count(*) AS n FROM lookup_claims').fetchone()['n'] == 1
    db.execute('DELETE FROM jobs WHERE id = %s', (jid,))
    assert db.execute('SELECT count(*) AS n FROM lookup_claims').fetchone()['n'] == 0


def test_a_row_of_another_list_is_not_claimed(db):
    jid, lid, _, bo = _turn(db)
    _, _, _, other_bo = _turn(db)

    claimed = lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo, other_bo])

    assert claimed == 1
    assert [str(r['row_id']) for r in db.execute('SELECT row_id FROM lookup_claims')] == [bo]
    assert _states(db, other_bo) == ('not_searched', 'not_searched', True)


def test_the_sql_readers_and_the_python_ones_agree_on_a_claim(db):
    jid, lid, _, bo = _turn(db)
    lookup_claims.claim(db, jid, 'c1', lid, 'email', [bo])
    row = db.execute(f'SELECT r.cells, {CHARGES_SQL} AS charges FROM list_rows r WHERE r.id = %s', (bo,)).fetchone()

    assert (email_lookup(row['cells'], row['charges']).state, phone_lookup(row['cells'], row['charges']).state) == (
        'searching',
        'not_searched',
    )


# ── the loop: the claim ends with the call, whatever the call did ─────────────────────────────────────────────


async def _claiming(args, ctx):
    """Claims the row, reads it as a user would while the vendors answer, then answers (or crashes)."""
    from app.core.db import get_conn

    row_id = args['row_id']
    await ctx.claim('email', [row_id])
    with get_conn() as conn:
        DURING.append(_states(conn, row_id))
    if args.get('crash'):
        raise RuntimeError('vendor client blew up')
    if args.get('fail'):
        return ToolResult(ok=False, error='the vendor refused')
    return ToolResult(
        ok=True,
        row_updates=[{'row_id': row_id, 'cells': {'email': None, 'email_status': 'not_found'}}],
        entity='person',
        outcome=lookup_outcome.searched(
            'email', [{'row_id': row_id, 'cells': {'email': None, 'email_status': 'not_found'}}]
        ),
    )


DURING: list[tuple[str, str, bool]] = []
if not registry.get('fake_claiming'):
    registry.register(
        ToolDef(
            name='fake_claiming',
            description='test',
            input_schema={'type': 'object'},
            handler=_claiming,
            found_noun='email',
        )
    )


async def _person_turn(db) -> tuple[str, str, str]:
    """A chat with a person list and one person never searched, and a new job on it. (job, session, row)."""
    from tests.test_agent import _chat, _job, _user

    uid = _user(db)
    sid = _chat(db, uid)
    lid = create_list(db, user_id=uid, session_id=sid, name='People', entity='person')
    (row,) = add_rows(db, lid, 'person', [{'full_name': 'Bo Bianchi'}])
    _, jid = _job(db, uid, 'find their emails', session_id=sid)
    return jid, sid, str(row)


@pytest.mark.parametrize('crash', [False, True])
async def test_the_loop_releases_the_claim_when_the_call_ends(db, crash):
    from tests.test_agent import _run

    DURING.clear()
    jid, _, row = await _person_turn(db)

    events, _ = await _run(jid, [('', [ToolCall('c1', 'fake_claiming', {'row_id': row, 'crash': crash})]), ('ok', [])])

    assert DURING == [('searching', 'not_searched', False)]
    assert db.execute('SELECT count(*) AS n FROM lookup_claims').fetchone()['n'] == 0
    names = [e for e, _ in events]
    assert names.index('list_updated') < names.index('tool_result')  # the claim made the panel refetch
    assert next(d for e, d in events if e == 'list_updated')['rows_updated'] == 1
    status = db.execute('SELECT cells FROM list_rows WHERE id = %s', (row,)).fetchone()['cells'].get('email_status')
    assert (status, _states(db, row)[0]) == ((None, 'not_searched') if crash else ('not_found', 'not_found'))


async def test_the_loop_releases_the_claim_of_a_call_that_returns_not_ok(db):
    from tests.test_agent import _run

    DURING.clear()
    jid, _, row = await _person_turn(db)

    await _run(jid, [('', [ToolCall('c1', 'fake_claiming', {'row_id': row, 'fail': True})]), ('ok', [])])

    assert DURING == [('searching', 'not_searched', False)]
    assert db.execute('SELECT count(*) AS n FROM lookup_claims').fetchone()['n'] == 0
    assert _states(db, row) == ('not_searched', 'not_searched', True)


async def test_the_claim_frame_of_a_company_list_does_not_name_person(db):
    from tests.test_agent import _chat, _job, _run, _user

    DURING.clear()
    uid = _user(db)
    sid = _chat(db, uid)
    lid = create_list(db, user_id=uid, session_id=sid, name='Companies', entity='company')
    (row,) = add_rows(db, lid, 'company', [{'name': 'Acme'}])
    _, jid = _job(db, uid, 'find their emails', session_id=sid)

    events, _ = await _run(
        jid, [('', [ToolCall('c1', 'fake_claiming', {'row_id': str(row), 'fail': True})]), ('ok', [])]
    )

    frame = next(d for e, d in events if e == 'list_updated')
    assert (frame['list_id'], frame['entity']) == (lid, None)
