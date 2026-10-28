"""Export filters (verified email, off-target rating) and the findable estimate (#344)."""

from fastapi.testclient import TestClient

from app.lists.last_search import remember_search
from app.lists.rows import add_rows, create_list
from app.main import app
from tests.conftest import act_in_own_org, hold, member, sign_in
from tests.test_agent import _chat, _user, needs_db
from tests.test_agent import db as db

client = TestClient(app)


@needs_db
def test_export_drops_unverified_and_off_target_rows(db):
    uid = _user(db)
    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='P', entity='person')
    people = [
        {'full_name': 'Ok', 'email': 'ok@a.com', 'email_status': 'verified', 'linkedin_url': 'linkedin.com/in/ok'},
        {'full_name': 'Guess', 'email': 'g@a.com', 'email_status': 'guessed', 'linkedin_url': 'linkedin.com/in/g'},
        {'full_name': 'Hidden', 'email': 'h@a.com', 'email_status': 'verified', 'linkedin_url': 'linkedin.com/in/h'},
        {'full_name': 'NoMail', 'email_status': 'verified', 'linkedin_url': 'linkedin.com/in/n'},
        {'full_name': 'Off', 'email': 'o@a.com', 'email_status': 'verified', 'linkedin_url': 'linkedin.com/in/o'},
    ]
    add_rows(db, lid, 'person', people)
    db.execute("UPDATE list_rows SET rating = 'bad' WHERE list_id = %s AND cells->>'full_name' = 'Off'", (lid,))
    sid = _chat(db, uid)
    jid = db.execute(
        'INSERT INTO jobs (organization_id, user_id, chat_session_id, status)'
        " VALUES ((SELECT organization_id FROM chat_sessions WHERE id = %s), %s, %s, 'running') RETURNING id",
        (sid, uid, sid),
    ).fetchone()['id']
    db.execute(  # a charge its running turn may still refund
        "INSERT INTO row_charges (hold_id, job_id, row_id, keys, credits, state) SELECT %s, %s, id, '{email}', 1,"
        " 'held' FROM list_rows WHERE list_id = %s AND cells->>'full_name' = 'Hidden'",
        (hold(db, uid, jid), jid, lid),
    )
    sign_in(app, member(db, uid))
    try:

        def names(**params):
            res = client.get(f'/v1/lists/{lid}/export.csv', params={'columns': 'full_name', **params})
            assert res.status_code == 200, res.text
            return res.text.splitlines()[1:]

        assert sorted(names()) == ['Guess', 'Hidden', 'NoMail', 'Off', 'Ok']
        assert sorted(names(verified_only='true')) == ['Off', 'Ok']  # not the hidden email, not the empty one
        assert sorted(names(hide_off_target='true')) == ['Guess', 'Hidden', 'NoMail', 'Ok']
        assert names(verified_only='true', hide_off_target='true') == ['Ok']
        company = create_list(db, user_id=uid, session_id=_chat(db, uid), name='C', entity='company')
        assert client.get(f'/v1/lists/{company}/export.csv?verified_only=true').status_code == 422
    finally:
        app.dependency_overrides.clear()


@needs_db
def test_findable_counts_what_the_same_search_has_not_fetched(db):
    uid = _user(db)
    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='P', entity='person')
    act_in_own_org(db, uid)  # the search tools of uid's turn
    sign_in(app, member(db, uid))
    try:
        findable = lambda: client.get(f'/v1/lists/{lid}').json()['findable']  # noqa: E731 - test shorthand
        assert findable() is None  # no search recorded
        remember_search(lid, 'search_people', {'titles': ['VP']}, None, 10)
        assert findable() is None  # the vendor sent no total
        remember_search(lid, 'search_people', {'titles': ['CEO']}, 40, 10)
        assert findable() == 30
        remember_search(lid, 'search_people', {'titles': ['CEO'], 'page': 2}, 40, 10)
        assert findable() == 20  # pages of one search add up
        # rows from elsewhere in the list do not change it, a new search starts again
        add_rows(db, lid, 'person', [{'full_name': 'A', 'linkedin_url': 'linkedin.com/in/a'}])
        assert findable() == 20
        remember_search(lid, 'search_people', {'titles': ['CTO']}, 500, 10)
        assert findable() == 490
        remember_search(lid, 'search_people', {'titles': ['CFO']}, 10**6, 10)
        assert findable() == 4_990  # 500 pages of 10 rows
        remember_search(lid, 'search_people', {'titles': ['COO']}, 10, 10)
        assert findable() is None  # everything fetched: never "0" while Altre righe is offered
        db.execute('UPDATE lists SET last_search = last_search || \'{"exhausted": true}\' WHERE id = %s', (lid,))
        assert findable() is None
    finally:
        app.dependency_overrides.clear()
