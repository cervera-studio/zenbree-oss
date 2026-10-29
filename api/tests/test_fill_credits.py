"""The chat's credits used count its AI column fills (#239): session payload and list summary agree."""

from tests.conftest import project
from tests.test_agent import _job, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db


def _fill(db, uid, list_id, held, filled=None):
    """One fill run's hold on the user's organization as ``ai_columns._open`` takes it; settled on ``filled`` cells, else left open."""
    rid = db.execute(
        "SELECT reserve_credits(default_organization(%s), %s, %s, 'ai_column', %s, 'ai_column', NULL, 'AI column fill')"
        ' AS r',
        (uid, uid, held, f'{list_id}:ai_x'),
    ).fetchone()['r']
    if filled == 0:
        db.execute('SELECT release_reservation(%s)', (rid,))
    elif filled is not None:
        db.execute('SELECT settle_reservation_with_slack(%s, %s)', (rid, held - filled))


@needs_db
def test_chat_credits_count_settled_ai_column_fills(client, db):
    tc, uid = client
    db.execute('UPDATE user_credits SET credits_remaining = 500 WHERE user_id = %s', (uid,))
    sid, jid = _job(db, uid, 'agencies in Milan')
    db.execute("UPDATE jobs SET status = 'succeeded', credits_spent = 7 WHERE id = %s", (jid,))
    db.execute(
        "INSERT INTO chat_messages (session_id, user_id, role, credits_used) VALUES (%s, %s, 'assistant', 7)",
        (sid, uid),
    )

    def new_list(entity, session):
        return db.execute(
            'INSERT INTO lists (organization_id, project_id, user_id, name, entity, chat_session_id) VALUES (default_organization(%s), %s, %s, %s, %s, %s) RETURNING id::text',
            (uid, project(db, uid), uid, entity, entity, session),
        ).fetchone()['id']

    companies, people = new_list('company', sid), new_list('person', sid)
    _fill(db, uid, companies, 30, filled=12)  # 18 slack refunded
    _fill(db, uid, people, 10, filled=10)
    _fill(db, uid, people, 10, filled=0)  # found nothing: released, free
    _fill(db, uid, people, 25)  # still running: held, not spent
    alone = new_list('company', None)  # its chat was deleted: only its own fills
    _fill(db, uid, alone, 5, filled=5)
    other = _user(db, credits=100)  # another organization's fill on a list id of this chat never counts
    _fill(db, other, companies, 9, filled=9)

    assert tc.get(f'/v1/chat/sessions/{sid}').json()['fill_credits'] == 22
    for lid in (companies, people):
        assert tc.get(f'/v1/lists/{lid}').json()['credits_used'] == 7 + 22
    assert tc.get(f'/v1/lists/{alone}').json()['credits_used'] == 5
    assert {lst['id']: lst['credits_used'] for lst in tc.get('/v1/lists').json()['lists']} == {
        companies: 29,
        people: 29,
        alone: 5,
    }
