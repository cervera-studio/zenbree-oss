"""#659: the migration that converts old lookup values, and the SQL readers that must agree with ``email_lookup``."""

import pytest
from psycopg.types.json import Jsonb

from app.core.migrate import MIGRATIONS
from app.lists.lookups import (
    CHARGES_SQL,
    EMAIL_OUTCOME_SQL,
    OPEN_EMAIL_SQL,
    EmailFound,
    deliverable,
    deliverable_email_sql,
    email_lookup,
    phone_lookup,
    verified_email_sql,
)
from tests.conftest import hold, project
from tests.test_agent import _chat, _user, needs_db
from tests.test_agent import db as db

pytestmark = needs_db

MIGRATION = '20261012152552_lookup_status.sql'
# #660 moves the refunds of cell_meta into row_charges after it, as production ran them.
ROW_CHARGES = '20261012173606_row_charges.sql'
# #519 reopens the not_found emails a lookup wrote before #506 after both.
NOT_FOUND_RETRY = '20261013091036_email_not_found_retry.sql'
# Charges name their hold after all three.
HOLD = '20261013183923_row_charges_hold.sql'
NULL = None  # a JSON null stored in the cell, as apply_row_updates writes it

# Each stored shape a lookup has left, as (cells, cell_meta) -> what the migrations make of the cells and how the
# row reads after them.
OLD_ROWS = {
    'never searched': ({'full_name': 'A'}, {}, {'full_name': 'A'}, 'not_searched', 'not_searched'),
    'verified': (
        {'email': 'a@acme.it', 'email_status': 'verified'},
        {},
        {'email': 'a@acme.it', 'email_status': 'verified'},
        ('found', 'verified'),
        'not_searched',
    ),
    'not found by a lookup before #506 (#519)': (
        {'email': NULL, 'email_status': 'not_found'},
        {},
        {'email': NULL, 'email_status': 'failed'},
        'failed',
        'not_searched',
    ),
    'not found with an address': (
        {'email': 'a@acme.it', 'email_status': 'not_found'},
        {},
        None,
        ('found', 'unknown'),
        'not_searched',
    ),
    'not found, then cleared by hand': (
        {'email': '', 'email_status': 'not_found'},
        {'email': {'status': 'manual'}},
        None,
        'not_found',
        'not_searched',
    ),
    # A failure keeps the refund open, so the batch still skips the row.
    'refunded, then not found by a lookup before #506': (
        {'email': NULL, 'email_status': 'not_found'},
        {'email': {'status': 'refunded'}},
        {'email': NULL, 'email_status': 'failed'},
        'refunded',
        'not_searched',
    ),
    'source failed after #506': (
        {'email': NULL},
        {},
        {'email': NULL, 'email_status': 'failed'},
        'failed',
        'not_searched',
    ),
    'refunded': ({}, {'email': {'status': 'refunded'}}, {}, 'refunded', 'not_searched'),
    'CSV status, no address': (
        {'email_status': 'unavailable'},
        {},
        {'email_status': 'not_found'},
        'not_found',
        'not_searched',
    ),
    'CSV status in capitals': (
        {'email': 'a@acme.it', 'email_status': ' Verified'},
        {},
        {'email': 'a@acme.it', 'email_status': 'verified'},
        ('found', 'verified'),
        'not_searched',
    ),
    'CSV status with an address': (
        {'email': 'a@acme.it', 'email_status': 'guessed'},
        {},
        {'email': 'a@acme.it'},
        ('found', 'unknown'),
        'not_searched',
    ),
    'phone search reopened by a found email': (
        {'email': 'a@acme.it', 'email_status': 'mx_ok', 'phone_status': NULL},
        {},
        {'email': 'a@acme.it', 'email_status': 'mx_ok'},
        ('found', 'mx_ok'),
        'not_searched',
    ),
    'phone found': (
        {'phone': '+39000', 'phone_status': 'revealed'},
        {},
        {'phone': '+39000', 'phone_status': 'revealed'},
        'not_searched',
        'found',
    ),
    'phone not found': ({'phone': NULL, 'phone_status': 'not_found'}, {}, None, 'not_searched', 'not_found'),
    'phone failed or nothing to search by': ({'phone': NULL}, {}, None, 'not_searched', 'not_searched'),
    'phone status from a CSV': (
        {'phone_status': 'Revealed'},
        {},
        {'phone_status': 'revealed'},
        'not_searched',
        'not_found',
    ),
}


def _list(db) -> str:
    uid = _user(db)
    return str(
        db.execute(
            'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity)'
            " VALUES (default_organization(%s), %s, %s, %s, 'People', 'person') RETURNING id",
            (uid, project(db, uid), uid, _chat(db, uid)),
        ).fetchone()['id']
    )


def _insert(db, list_id: str, rows: dict[str, tuple[dict, dict]]) -> dict[str, str]:
    """Stores the cells as they are, JSON nulls included (add_rows would drop them). Row id by name."""
    return {
        name: str(
            db.execute(
                'INSERT INTO list_rows (list_id, cells, cell_meta, dedupe_key) VALUES (%s, %s, %s, %s) RETURNING id',
                (list_id, Jsonb(cells), Jsonb(meta), name),
            ).fetchone()['id']
        )
        for name, (cells, meta) in rows.items()
    }


def _state(lookup) -> str | tuple[str, str]:
    return (lookup.state, lookup.verdict) if isinstance(lookup, EmailFound) else lookup.state


def _stored(db, list_id: str) -> dict[str, dict]:
    return {
        r['id']: r
        for r in db.execute(
            f'SELECT id::text, cells, {CHARGES_SQL} AS charges FROM list_rows r WHERE list_id = %s', (list_id,)
        )
    }


def test_the_migration_turns_every_old_value_into_one_state(db):
    list_id = _list(db)
    ids = _insert(db, list_id, {name: (cells, meta) for name, (cells, meta, *_) in OLD_ROWS.items()})
    db.execute((MIGRATIONS / MIGRATION).read_text())  # the fixture ran it on an empty schema: run it on this data
    db.execute('DROP TABLE row_charges')
    db.execute((MIGRATIONS / ROW_CHARGES).read_text())
    db.execute((MIGRATIONS / NOT_FOUND_RETRY).read_text())
    # the column 20261013175602_tenant_rows_lists.sql gave the table, which the hold's key names
    db.execute('ALTER TABLE row_charges ADD COLUMN organization_id uuid')
    db.execute('UPDATE row_charges c SET organization_id = r.organization_id FROM list_rows r WHERE r.id = c.row_id')
    db.execute((MIGRATIONS / HOLD).read_text())

    stored = _stored(db, list_id)
    for name, (cells, _, after, email, phone) in OLD_ROWS.items():
        row = stored[ids[name]]
        assert row['cells'] == (cells if after is None else after), name
        assert (
            _state(email_lookup(row['cells'], row['charges'])),
            _state(phone_lookup(row['cells'], row['charges'])),
        ) == (
            email,
            phone,
        ), name


def test_the_not_found_retry_changes_nothing_the_second_time(db):
    list_id = _list(db)
    ids = _insert(db, list_id, {'not found by a lookup': ({'email': NULL, 'email_status': 'not_found'}, {})})
    db.execute((MIGRATIONS / NOT_FOUND_RETRY).read_text())
    once = _stored(db, list_id)
    db.execute((MIGRATIONS / NOT_FOUND_RETRY).read_text())

    assert once[ids['not found by a lookup']]['cells'] == {'email': NULL, 'email_status': 'failed'}
    assert _stored(db, list_id) == once


# Every value email_status holds, with and without an address, plus the states a turn's charges give: as (cells,
# cell_meta, the states of the email's charge records, oldest first).
_STATUSES = ['verified', 'catch_all', 'mx_ok', 'unknown', 'invalid', 'not_found', 'failed', None]
_ROWS = {
    **{
        f'{status} {"with" if address else "without"} an address': (
            {k: v for k, v in (('email', 'a@acme.it' if address else NULL), ('email_status', status)) if v is not None},
            {},
            [],
        )
        for status in _STATUSES
        for address in (True, False)
    },
    'refunded': ({}, {}, ['refunded']),
    'refunded, then not found by row id': ({'email': NULL, 'email_status': 'not_found'}, {}, ['refunded']),
    'refunded, then failed by row id': ({'email': NULL, 'email_status': 'failed'}, {}, ['refunded']),
    'refunded, then bought again': ({'email': 'a@acme.it', 'email_status': 'verified'}, {}, ['refunded', 'charged']),
    'refunded, then a dead turn bought it again': ({}, {}, ['refunded', 'held']),
    'refunded, then cleared by hand': ({'email': ''}, {'email': {'status': 'manual'}}, ['refunded']),
    'pending': ({'email': 'a@acme.it', 'email_status': 'verified'}, {'email': {'status': 'filled'}}, ['live']),
    'cleared by hand': ({'email': '', 'email_status': 'verified'}, {'email': {'status': 'manual'}}, []),
}


def _charge(db, row_id: str, states: list[str]) -> None:
    """The email's charge records, one statement each so the later one is newer. ``live``: held on the reserved hold
    of a holder that runs; ``held``: on a hold closed without its holder, which died."""
    for state in states:
        hid = None
        if state in ('live', 'held'):
            owner = db.execute(  # a hold of the row's organization: row_charges names it with the hold
                'SELECT l.user_id FROM list_rows r JOIN lists l ON l.id = r.list_id WHERE r.id = %s', (row_id,)
            ).fetchone()['user_id']
            hid = hold(db, owner)
            if state == 'held':
                db.execute('SELECT release_reservation(%s)', (hid,))
        db.execute(
            "INSERT INTO row_charges (hold_id, row_id, keys, credits, state) VALUES (%s, %s, '{email}', 1, %s)",
            (hid, row_id, 'held' if state == 'live' else state),
        )


@pytest.mark.parametrize('name', list(_ROWS))
def test_the_sql_readers_agree_with_email_lookup(db, name):
    """``next_unenriched`` takes the open rows, the home and the export count the verified ones, the automation
    preview the deliverable ones and the chat state counts the outcomes: the same rows ``email_lookup`` reads as
    not_searched or failed, as found and verified, as deliverable, and as found with its verdict, not_found or failed."""
    list_id = _list(db)
    cells, meta, charges = _ROWS[name]
    row_id = _insert(db, list_id, {name: (cells, meta)})[name]
    _charge(db, row_id, charges)
    sql = db.execute(
        f'SELECT {OPEN_EMAIL_SQL} AS open, {verified_email_sql("r.cells")} AS verified,'
        f' {deliverable_email_sql("r.cells")} AS deliverable, {EMAIL_OUTCOME_SQL} AS outcome, r.cells,'
        f' {CHARGES_SQL} AS charges FROM list_rows r WHERE r.id = %s',
        (row_id,),
    ).fetchone()
    lookup = email_lookup(sql['cells'], sql['charges'])  # raw cells: a pending address is there, as SQL sees it
    assert (sql['open'], sql['verified'], sql['deliverable'], sql['outcome']) == (
        lookup.state in ('not_searched', 'failed'),
        _state(lookup) == ('found', 'verified'),
        deliverable(lookup),
        lookup.verdict
        if isinstance(lookup, EmailFound)
        else lookup.state
        if lookup.state in ('not_found', 'failed')
        else None,
    )


@pytest.mark.parametrize(
    ('name', 'state'),
    [
        ('refunded', 'refunded'),
        ('refunded, then bought again', 'found'),
        ('refunded, then a dead turn bought it again', 'not_searched'),
        ('refunded, then cleared by hand', 'not_searched'),
        ('pending', 'searching'),
    ],
)
def test_the_latest_charge_of_the_email_decides_its_state(db, name, state):
    """``searching`` while a live turn holds the cell (the list hides it); ``refunded`` while the latest charge of
    the cell is the refunded one."""
    from app.lists.row_view import ROW_COLS, with_lookups

    cells, meta, charges = _ROWS[name]
    row_id = _insert(db, _list(db), {name: (cells, meta)})[name]
    _charge(db, row_id, charges)
    row = db.execute(f'SELECT {ROW_COLS} FROM list_rows r WHERE r.id = %s', (row_id,)).fetchone()
    assert with_lookups(row)['lookups']['email']['state'] == state
