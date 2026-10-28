"""#473: one live job per chat. A second member's send, plan approval included, waits for the running turn."""

import uuid

import pytest
from fastapi import HTTPException

from app.agent.routes import ChatRequest
from app.auth.middleware import CurrentUser
from app.core.migrate import MIGRATIONS
from app.llm.base import ToolCall
from tests.conftest import member, start_turn
from tests.test_agent import _job, _org, _run, _user, needs_db
from tests.test_agent import db as db
from tests.test_plan import _plan_args

PER_CHAT = '20261008110744_one_live_job_per_chat.sql'


def _columns_and_plan(c, list_id):
    return (
        c.execute('SELECT key, config FROM list_columns WHERE list_id = %s ORDER BY key', (list_id,)).fetchall(),
        c.execute('SELECT * FROM list_plans WHERE list_id = %s', (list_id,)).fetchall(),
    )


@needs_db
async def test_a_second_member_cannot_start_a_turn_on_a_chat_that_runs_one(db):
    ada, bo = _user(db, credits=500), _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (_org(db, ada), bo, 'member'),
    )
    as_bo = CurrentUser(id=bo, org_id=_org(db, ada), org_role='member')
    sid, jid = _job(db, ada, '100 CTOs with emails')
    events, _ = await _run(jid, [('', [ToolCall('p1', 'propose_plan', _plan_args())]), ('unused', [])])
    plan = dict(events)['message_saved']['question']['plan']
    _, running = start_turn(member(db, ada), ChatRequest(message='also in Rome', session_id=uuid.UUID(sid)))
    db.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (running,))
    before = _columns_and_plan(db, plan['list_id'])

    approval = {'list_id': plan['list_id'], 'version': 1, 'rows': 100}
    for req in (
        ChatRequest(message='Approve', session_id=uuid.UUID(sid), plan_approval=approval),
        ChatRequest(message='hi', session_id=uuid.UUID(sid)),
    ):
        with pytest.raises(HTTPException) as e:
            start_turn(as_bo, req)
        assert (e.value.status_code, e.value.detail['code']) == (409, 'CHAT.JOB_RUNNING')
    assert _columns_and_plan(db, plan['list_id']) == before
    assert db.execute('SELECT count(*) AS n FROM jobs WHERE user_id = %s', (bo,)).fetchone()['n'] == 0

    # a turn past the time limit lost its worker: it no longer blocks the chat
    db.execute("UPDATE jobs SET updated_at = now() - interval '1 hour' WHERE id = %s", (running,))
    _, approved = start_turn(as_bo, ChatRequest(message='Approve', session_id=uuid.UUID(sid), plan_approval=approval))
    assert db.execute('SELECT status, error FROM jobs WHERE id = %s', (running,)).fetchone() == {
        'status': 'failed',
        'error': 'stale',
    }
    assert db.execute('SELECT approved_job_id::text AS j FROM list_plans').fetchone()['j'] == approved


@needs_db
def test_the_migration_keeps_the_newest_live_job_of_a_chat(db):
    db.execute('DROP INDEX uq_jobs_one_active_per_chat')
    ada, bo, cy = _user(db), _user(db), _user(db)
    sid, older = _job(db, ada, 'first')
    _, newer = _job(db, bo, 'second', session_id=sid)
    _, done = _job(db, cy, 'earlier', session_id=sid)
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (done,))
    _, alone = _job(db, cy, 'other chat')
    db.execute("UPDATE jobs SET status = 'running' WHERE id IN (%s, %s)", (older, alone))
    db.execute("UPDATE jobs SET created_at = created_at - interval '1 minute' WHERE id = %s", (older,))

    db.execute((MIGRATIONS / PER_CHAT).read_text())  # the fixture ran it on an empty schema: run it on this data
    status = {r['id']: (r['status'], r['error']) for r in db.execute('SELECT id::text, status, error FROM jobs')}
    assert status == {
        older: ('failed', 'superseded'),
        newer: ('queued', None),
        done: ('succeeded', None),
        alone: ('running', None),
    }


@needs_db
def test_a_turn_on_a_chat_deleted_under_it_is_a_404(db):
    """#494: the chat passes the existence check, then a concurrent delete wins the race before the job insert."""
    ada = _user(db, credits=500)
    sid, done = _job(db, ada, 'first')
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (done,))
    db.execute(
        'CREATE FUNCTION delete_chat_first() RETURNS trigger LANGUAGE plpgsql AS $$'
        ' BEGIN DELETE FROM chat_sessions WHERE id = NEW.chat_session_id; RETURN NEW; END $$;'
        ' CREATE TRIGGER delete_chat BEFORE INSERT ON jobs FOR EACH ROW EXECUTE FUNCTION delete_chat_first()'
    )
    with pytest.raises(HTTPException) as e:
        start_turn(member(db, ada), ChatRequest(message='hi', session_id=uuid.UUID(sid)))
    assert (e.value.status_code, e.value.detail['code']) == (404, 'SESSION.NOT_FOUND')
