"""#660: the per-row charge records a turn writes, settles and a campaign or the reaper makes final."""

import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.billing import row_charges
from app.billing.row_charges import Refund
from app.core.config import settings
from app.core.scope import for_another_thread
from app.lists.rows import add_rows, apply_row_updates, create_list
from tests.conftest import act_in_own_org, hold, member
from tests.test_agent import _job, _user, needs_db
from tests.test_agent import db as db

ADA = {'full_name': 'Ada Rossi', 'title': 'CTO', 'email': 'ada@acme.test', 'email_status': 'verified'}
BO = {'full_name': 'Bo Bianchi', 'email': 'bo@acme.test', 'email_status': 'verified'}  # no title: unfinished
EMAIL = ('email', 'email_status', 'email_source')


def _turn(db) -> tuple[str, str, str, str]:
    """A running job with its hold, and a person list that requires ``title``: Ada is complete, Bo is not. (job,
    list, ada, bo)."""
    uid = _user(db, credits=100)
    act_in_own_org(db, uid)  # the list's routes run as its owner
    sid, jid = _job(db, uid, 'find their emails')
    db.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (jid,))
    hold(db, uid, jid)
    lid = create_list(db, user_id=uid, session_id=sid, name='CTOs', entity='person')
    ada, bo = add_rows(db, lid, 'person', [ADA, BO])
    db.execute(
        "UPDATE list_columns SET config = config || '{\"required\": true}' WHERE list_id = %s AND key = 'title'", (lid,)
    )
    return jid, str(lid), str(ada), str(bo)


def _hold(conn, job_id) -> str:
    return conn.execute('SELECT reservation_id::text FROM jobs WHERE id = %s', (job_id,)).fetchone()['reservation_id']


def _charge(conn, job_id, call_id, list_id, row_ids, keys, credits) -> None:
    """The charge the job's turn records, on its hold."""
    row_charges.charge(conn, _hold(conn, job_id), job_id, call_id, list_id, row_ids, keys, credits)


def _refund(conn, job_id) -> tuple[list[Refund], list[str]]:
    hold_id = _hold(conn, job_id)
    return row_charges.refund_unfinished(conn, hold_id), row_charges.settled_lists(conn, hold_id)


def _records(db, job_id) -> list[tuple[str, str, int, list[str]]]:
    return [
        (r['name'], r['state'], r['credits'], r['keys'])
        for r in db.execute(
            "SELECT r.cells->>'full_name' AS name, c.state, c.credits, c.keys FROM row_charges c"
            ' JOIN list_rows r ON r.id = c.row_id WHERE c.job_id = %s ORDER BY name',
            (job_id,),
        )
    ]


@needs_db
def test_a_charge_records_its_credits_on_each_row_and_holds_only_the_unfinished_row(db):
    jid, lid, ada, bo = _turn(db)

    _charge(db, jid, 'e1', lid, [ada, bo], EMAIL, 7)

    assert _records(db, jid) == [
        ('Ada Rossi', 'charged', 7, ['email', 'email_status']),
        ('Bo Bianchi', 'held', 7, ['email', 'email_status']),
    ]


@needs_db
def test_a_charge_records_no_row_of_another_list(db):
    jid, lid, ada, _ = _turn(db)
    *_, bo = _turn(db)  # Bo of another list

    _charge(db, jid, 'e1', lid, [ada, bo], EMAIL, 1)

    assert _records(db, jid) == [('Ada Rossi', 'charged', 1, ['email', 'email_status'])]


@needs_db
def test_the_settle_refunds_the_unfinished_row_once_and_keeps_the_rest(db):
    jid, lid, ada, bo = _turn(db)
    db.execute(
        'UPDATE list_rows SET cell_meta = %s WHERE id = %s',
        (Jsonb({'email': {'status': 'filled'}, 'full_name': {'status': 'manual'}}), bo),
    )
    _charge(db, jid, 'e1', lid, [ada, bo], EMAIL, 2)
    _charge(db, jid, 'e2', lid, [bo], ('phone',), 8)  # Bo holds no phone: final, nothing to take back

    refunds, lists = _refund(db, jid)

    assert (refunds, lists) == ([Refund('e1', bo, 2)], [lid])
    bo_row = db.execute('SELECT cells, cell_meta FROM list_rows WHERE id = %s', (bo,)).fetchone()
    assert bo_row == {'cells': {'full_name': 'Bo Bianchi'}, 'cell_meta': {'full_name': {'status': 'manual'}}}
    assert sorted((name, state) for name, state, _, _ in _records(db, jid)) == [
        ('Ada Rossi', 'charged'),
        ('Bo Bianchi', 'charged'),
        ('Bo Bianchi', 'refunded'),
    ]
    assert _refund(db, jid) == ([], [lid])  # a retried settle gives nothing back twice


def _typed_email(db) -> tuple[str, str, object, dict]:
    """Bo's email is held by the live turn when the user types one by hand. (job, list, user, the PATCH response)."""
    from app.lists.routes import RowPatch, patch_row

    jid, lid, _, bo = _turn(db)
    user = member(db, db.execute('SELECT user_id FROM lists WHERE id = %s', (lid,)).fetchone()['user_id'])
    _charge(db, jid, 'e1', lid, [bo], EMAIL, 2)
    return jid, lid, user, patch_row(lid, bo, RowPatch(cells={'email': 'typed@me.test'}), user)


@needs_db
def test_an_email_typed_by_hand_while_the_turn_runs_shows_at_once(db):
    """The typed address is the user's, not the turn's: the held status the turn bought stays hidden."""
    *_, patched = _typed_email(db)

    assert (patched['cells'].get('email'), patched['lookups']['email']) == (
        'typed@me.test',
        {'state': 'found', 'verdict': 'unknown'},
    )


@needs_db
def test_the_settle_never_takes_back_an_email_typed_by_hand(db):
    """The row is still unfinished, so the turn refunds its charge, but the cell is no longer what it bought."""
    from app.lists.routes import get_rows

    jid, lid, user, patched = _typed_email(db)

    refunds, _ = _refund(db, jid)

    (row,) = [r for r in get_rows(lid, user, cursor=0, limit=10)['rows'] if r['id'] == patched['id']]
    assert (refunds, row['cells'].get('email'), row['cell_meta'].get('email'), row['lookups']['email']) == (
        [Refund('e1', str(patched['id']), 2)],
        'typed@me.test',
        {'status': 'manual', 'by': user.id},
        {'state': 'found', 'verdict': 'unknown'},
    )


@needs_db
def test_an_email_cleared_by_hand_before_the_turn_bought_it_is_still_taken_back(db):
    """The vendor email replaced the hand edit, so the stale ``manual`` mark no longer protects the cell."""
    from app.lists.routes import RowPatch, get_rows, patch_row

    jid, lid, _, bo = _turn(db)
    user = member(db, db.execute('SELECT user_id FROM lists WHERE id = %s', (lid,)).fetchone()['user_id'])
    patch_row(lid, bo, RowPatch(cells={'email': ''}), user)
    apply_row_updates(
        db,
        lid,
        'person',
        [{'row_id': bo, 'cells': {'email': 'vendor@acme.test', 'email_status': 'verified', 'email_source': 'apollo'}}],
    )
    _charge(db, jid, 'e1', lid, [bo], EMAIL, 1)

    def bo_email():
        (row,) = [r for r in get_rows(lid, user, cursor=0, limit=10)['rows'] if str(r['id']) == bo]
        return row['cells'].get('email'), row['lookups']['email']['state']

    during = bo_email()
    refunds, _ = _refund(db, jid)

    assert (during, refunds, bo_email()) == (
        (None, 'searching'),
        [Refund('e1', bo, 1)],
        (None, 'refunded'),
    )


def test_the_refunds_of_a_turn_never_add_up_to_more_than_it_spent():
    refunds = [Refund('e1', 'r1', 3), Refund('e1', 'r2', 1), Refund('e2', 'r3', 2), Refund(None, 'r4', 0)]
    assert row_charges.refunds_by_call(refunds, 5) == {'e1': (4, 2), 'e2': (1, 1)}


def test_a_row_a_call_billed_twice_counts_once_in_its_refund():
    refunds = [Refund('e1', 'r1', 1), Refund('e1', 'r1', 1)]
    assert row_charges.refunds_by_call(refunds, 5) == {'e1': (2, 1)}


@needs_db
def test_a_campaign_that_enrolls_the_row_while_the_turn_settles_keeps_its_charge(db):
    """#462: ``finalize`` holds its lock until its commit; the settle waits, then refunds nothing it enrolled."""
    jid, lid, _, bo = _turn(db)
    _charge(db, jid, 'e1', lid, [bo], EMAIL, 2)
    draft = psycopg.connect(settings.database_url, row_factory=dict_row)
    row_charges.finalize(draft, [bo])
    out = {}

    def settle():
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            out['settled'] = _refund(conn, jid)

    thread = threading.Thread(target=settle)
    thread.start()
    deadline = time.monotonic() + 10
    while not db.execute(  # the settle waits on the records the draft locked
        "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone():
        assert time.monotonic() < deadline, 'the settle never waited on the draft'
        time.sleep(0.01)
    draft.commit()
    draft.close()
    thread.join()

    assert out['settled'] == ([], [lid])  # nothing refunded, and the list still reloads
    assert db.execute('SELECT cells FROM list_rows WHERE id = %s', (bo,)).fetchone()['cells']['email'] == 'bo@acme.test'
    assert _records(db, jid) == [('Bo Bianchi', 'charged', 2, ['email', 'email_status'])]


@needs_db
@pytest.mark.parametrize('route', ['patch', 'rate'])
def test_an_edit_that_waits_on_the_turns_row_lock_never_returns_the_held_email(db, route):
    """The edit locks Bo while the turn stores and charges his email, then reads the row after the turn commits."""
    from app.lists.bulk_routes import RowRating, rate_row
    from app.lists.routes import RowPatch, patch_row

    jid, lid, _, bo = _turn(db)
    db.execute("UPDATE list_rows SET cells = cells - 'email' - 'email_status' WHERE id = %s", (bo,))
    user = member(db, db.execute('SELECT user_id FROM lists WHERE id = %s', (lid,)).fetchone()['user_id'])
    turn = psycopg.connect(settings.database_url, row_factory=dict_row)
    apply_row_updates(turn, lid, 'person', [{'row_id': bo, 'cells': {'email': 'vendor@acme.test'}}])
    _charge(turn, jid, 'e1', lid, [bo], EMAIL, 1)
    edit = {
        'patch': lambda: patch_row(lid, bo, RowPatch(cells={'city': 'Milano'}), user),
        'rate': lambda: rate_row(lid, bo, RowRating(rating='good'), user),
    }[route]
    out = {}

    def run():
        out['row'] = edit()

    thread = threading.Thread(target=for_another_thread().run, args=(run,))
    thread.start()
    deadline = time.monotonic() + 10
    while not db.execute(  # the edit waits on the row the turn wrote
        "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone():
        assert time.monotonic() < deadline, 'the edit never waited on the turn'
        time.sleep(0.01)
    turn.commit()
    turn.close()
    thread.join()

    assert (out['row']['cells'].get('email'), out['row']['lookups']['email']['state']) == (None, 'searching')


def _reap(db) -> None:
    from app import worker

    db.execute("UPDATE credit_transactions SET created_at = created_at - interval '30 minutes'")
    worker.reap_stale_reservations()
    worker.reap_stale_reservations()


@needs_db
@pytest.mark.parametrize('dead', [False, True])
def test_the_reaper_makes_the_held_charges_of_a_dead_turn_final(db, dead):
    """A worker killed mid-turn never settles: once the reaper closes its hold the list shows the cells, and the
    record says they are charged. A live turn's hold is fresh, so the reaper leaves its charge held."""
    from app import worker

    jid, lid, _, bo = _turn(db)
    _charge(db, jid, 'e1', lid, [bo], EMAIL, 2)

    if dead:
        _reap(db)
    else:
        worker.reap_stale_reservations()

    assert _records(db, jid) == [('Bo Bianchi', 'charged' if dead else 'held', 2, ['email', 'email_status'])]


@needs_db
def test_the_reaper_makes_a_held_charge_of_a_deleted_job_final(db):
    jid, lid, _, bo = _turn(db)
    _charge(db, jid, 'e1', lid, [bo], EMAIL, 2)
    db.execute('DELETE FROM jobs WHERE id = %s', (jid,))

    _reap(db)

    assert db.execute('SELECT job_id, state FROM row_charges WHERE row_id = %s', (bo,)).fetchall() == [
        {'job_id': None, 'state': 'charged'}
    ]
