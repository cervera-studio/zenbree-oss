"""#687, #720: a hand-edited lookup status is normalised as an import's is, and a phone stands for its status."""

from app.billing.row_charges import UNFINISHED_SQL
from app.lists.rows import DELIVERED_SQL, add_rows, create_list
from tests.test_agent import _chat, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db


def _person(db, uid, cells):
    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='CTOs', entity='person')
    (rid,) = add_rows(db, lid, 'person', [{'full_name': 'Ada Rossi', **cells}])
    return lid, rid


@needs_db
def test_a_hand_edited_status_is_stored_as_an_import_would(client, db):
    tc, uid = client
    lid, rid = _person(db, uid, {'email': 'ada@acme.test', 'email_status': 'verified'})

    def patch(cells):
        tc.patch(f'/v1/lists/{lid}/rows/{rid}', json={'cells': cells}).raise_for_status()
        return db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']

    assert patch({'email_status': ' Catch_All '})['email_status'] == 'catch_all'
    assert patch({'email_status': 'looks fine'})['email_status'] == 'catch_all'  # unknown: dropped, stored one stays
    assert patch({'phone': '', 'phone_status': 'maybe'})['phone_status'] == 'not_found'  # no number
    assert patch({'phone': '+39 1', 'phone_status': 'Revealed'})['phone_status'] == 'revealed'


@needs_db
def test_a_status_patch_leaves_other_cells_and_reads_null_as_empty(client, db):
    tc, uid = client
    lid, rid = _person(db, uid, {'email': 'ada@acme.test', 'email_status': 'verified'})

    def patch(cells):
        tc.patch(f'/v1/lists/{lid}/rows/{rid}', json={'cells': cells}).raise_for_status()
        return db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']

    assert patch({'lead_status': 'Hot', 'email_status': 'Verified'})['lead_status'] == 'Hot'
    assert patch({'email_status': None}).get('email_status') is None  # null clears, as before
    cells = patch({'phone': None, 'phone_status': 'x'})
    assert cells['phone_status'] == 'not_found'  # a null phone is no number, not the text 'None'


@needs_db
def test_a_required_phone_status_is_filled_by_a_phone(db, client):
    _, uid = client
    lid, rid = _person(db, uid, {})
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'phone_status', 'Stato telefono', 'static', 99, '{\"required\": true}')",
        (lid,),
    )

    def unfinished():
        return bool(db.execute(f'SELECT 1 FROM list_rows r WHERE r.id = %s AND {UNFINISHED_SQL}', (rid,)).fetchone())

    assert unfinished()
    db.execute('UPDATE list_rows SET cells = cells || \'{"phone": "+39 1"}\' WHERE id = %s', (rid,))
    assert not unfinished()


@needs_db
def test_a_hand_typed_phone_or_email_resets_its_stale_status(client, db):
    tc, uid = client
    lid, rid = _person(db, uid, {'phone_status': 'not_found', 'email': 'a@acme.test', 'email_status': 'verified'})

    def patch(cells):
        tc.patch(f'/v1/lists/{lid}/rows/{rid}', json={'cells': cells}).raise_for_status()
        return db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']

    assert patch({'email': 'a@acme.test'})['email_status'] == 'verified'  # same value: kept
    cells = patch({'phone': '+39 1', 'email': 'b@acme.test'})
    assert cells.get('phone_status') is None and cells.get('email_status') is None
    assert patch({'phone': '+39 2', 'phone_status': 'revealed'})['phone_status'] == 'revealed'  # explicit wins


@needs_db
def test_a_reformatted_value_keeps_its_status_and_a_new_email_stays_delivered(client, db):
    tc, uid = client
    lid, rid = _person(
        db, uid, {'phone': '+391', 'phone_status': 'revealed', 'email': 'a@acme.test', 'email_status': 'verified'}
    )
    db.execute(
        "UPDATE list_columns SET config = '{\"required\": true}' WHERE list_id = %s AND key = 'email_status'", (lid,)
    )

    def patch(cells):
        tc.patch(f'/v1/lists/{lid}/rows/{rid}', json={'cells': cells}).raise_for_status()
        return db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']

    cells = patch({'phone': '+39 1', 'email': ' A@Acme.test '})
    assert cells['phone_status'] == 'revealed' and cells['email_status'] == 'verified'
    patch({'email': 'b@acme.test'})
    assert db.execute(f'SELECT 1 FROM list_rows r WHERE r.id = %s AND {DELIVERED_SQL}', (rid,)).fetchone()


@needs_db
def test_a_phone_without_digits_is_not_the_stored_empty_one(client, db):
    tc, uid = client
    lid, rid = _person(db, uid, {'phone_status': 'not_found'})
    tc.patch(f'/v1/lists/{lid}/rows/{rid}', json={'cells': {'phone': 'ask reception'}}).raise_for_status()
    cells = db.execute('SELECT cells FROM list_rows WHERE id = %s', (rid,)).fetchone()['cells']
    assert cells.get('phone_status') is None
