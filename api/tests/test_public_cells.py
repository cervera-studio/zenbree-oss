"""The web reads a contact's state from ``lookups``, never from ``email_status`` or ``phone_status`` in the cells."""

from fastapi.testclient import TestClient

from app.lists.rows import STATUS_KEYS, VENDOR_KEYS, add_rows, create_list, public_cells
from app.main import app
from tests.conftest import member, sign_in
from tests.test_agent import _chat, _user, needs_db
from tests.test_agent import db as db

client = TestClient(app)
HIDDEN = STATUS_KEYS | VENDOR_KEYS


def test_public_cells_drops_vendor_plumbing_and_raw_statuses_only():
    cells = {'full_name': 'A', 'email': 'a@x.it', 'email_status': 'verified', 'phone_status': 'failed', 'source': 'x'}
    assert public_cells({**cells, 'email_source': 'apollo', 'phone_source': 'aiark'}) == {
        'full_name': 'A',
        'email': 'a@x.it',
    }
    assert {'email_status', 'phone_status'} == STATUS_KEYS


@needs_db
def test_no_row_the_routes_return_carries_a_raw_status(db):
    uid = _user(db)
    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='P', entity='person')
    add_rows(
        db,
        lid,
        'person',
        [
            {
                'full_name': 'Found',
                'email': 'f@a.com',
                'email_status': 'verified',
                'linkedin_url': 'linkedin.com/in/f',
                'email_source': 'apollo',
            },
            {
                'full_name': 'Failed',
                'email_status': 'failed',
                'phone_status': 'failed',
                'linkedin_url': 'linkedin.com/in/x',
            },
            {'full_name': 'Never', 'linkedin_url': 'linkedin.com/in/n'},
        ],
    )
    sign_in(app, member(db, uid))
    try:
        listing = client.get(f'/v1/lists/{lid}/rows')
        assert listing.status_code == 200, listing.text
        rows = listing.json()['rows']
        by_name = {r['cells']['full_name']: r for r in rows}
        sent = [
            *rows,
            client.patch(f'/v1/lists/{lid}/rows/{rows[0]["id"]}', json={'excluded': False}).json(),
            client.patch(f'/v1/lists/{lid}/rows/{rows[1]["id"]}', json={'cells': {'title': 'CEO'}}).json(),
            client.put(f'/v1/lists/{lid}/rows/{rows[2]["id"]}/rating', json={'rating': 'good'}).json(),
        ]
        assert len(sent) == 6
        for row in sent:
            assert not HIDDEN & row['cells'].keys(), row
        # The state is still there, read from the raw cells before they were stripped.
        assert by_name['Found']['lookups']['email'] == {'state': 'found', 'verdict': 'verified'}
        assert by_name['Failed']['lookups']['email']['state'] == 'failed'
        assert by_name['Failed']['lookups']['phone']['state'] == 'failed'
        assert by_name['Never']['lookups']['email']['state'] == 'not_searched'
        # The status columns stay in the column list: the web names them as markers of its email and phone cells.
        keys = {c['key'] for c in client.get(f'/v1/lists/{lid}').json()['columns']}
        assert {'email_status', 'phone_status'} <= keys
    finally:
        app.dependency_overrides.clear()


@needs_db
def test_the_csv_export_keeps_the_raw_status(db):
    uid = _user(db)
    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='P', entity='person')
    add_rows(
        db,
        lid,
        'person',
        [{'full_name': 'Found', 'email': 'f@a.com', 'email_status': 'verified', 'linkedin_url': 'linkedin.com/in/f'}],
    )
    sign_in(app, member(db, uid))
    try:
        res = client.get(f'/v1/lists/{lid}/export.csv')
        assert res.status_code == 200, res.text
        header, line = res.text.splitlines()[:2]
        assert 'Email status' in header.split(',')  # the column label
        assert 'verified' in line.split(',')
    finally:
        app.dependency_overrides.clear()
