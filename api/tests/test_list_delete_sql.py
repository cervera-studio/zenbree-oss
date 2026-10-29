"""#341: deleting a list or an AI column hides it, and the undo restores it with its rows and cells."""

from psycopg.types.json import Jsonb

from app.agent.loop import Turn
from app.agent.tools.handlers import propose_plan
from app.agent.tools.registry import ToolContext
from app.auth.middleware import CurrentUser
from app.home.overview import load_overview
from app.lists.rows import add_rows
from tests.conftest import act_in_own_org, give_role, member, project, sign_in
from tests.test_agent import FakeProvider, _job, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_plan import _plan_args

pytestmark = needs_db


def _list(db, uid, sid, entity='person') -> str:
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity, chat_session_id)'
        " VALUES (default_organization(%s), %s, %s, 'L', %s, %s) RETURNING id::text",
        (uid, project(db, uid), uid, entity, sid),
    ).fetchone()['id']
    add_rows(db, lid, 'person', [{'full_name': 'Ada', 'email': 'ada@acme.test'}])
    return lid


def test_delete_hides_the_list_everywhere_and_restore_brings_it_back(client, db):
    from app.main import app

    tc, uid = client
    sid, jid = _job(db, uid, 'find people')
    lid = _list(db, uid, sid)
    db.execute("UPDATE jobs SET status = 'succeeded', finished_at = now(), list_id = %s WHERE id = %s", (lid, jid))

    def home():
        snap = load_overview(db, uid, member(db, uid).org_id, project(db, uid)).overview
        return [x.id for x in snap.lists] + [x.list_id for x in snap.finished]

    assert home() == [lid, lid]
    assert tc.delete(f'/v1/lists/{lid}').status_code == 204
    assert tc.get('/v1/lists').json()['lists'] == []
    assert home() == []
    for path in (f'/v1/lists/{lid}', f'/v1/lists/{lid}/rows', f'/v1/lists/{lid}/export.csv'):
        assert tc.get(path).status_code == 404, path
    assert tc.get(f'/v1/chat/sessions/{sid}').json()['lists'] == []
    sign_in(app, member(db, uid))
    assert tc.post('/v1/campaigns', json={'name': 'C', 'list_id': lid}).status_code == 404
    assert tc.delete(f'/v1/lists/{lid}').status_code == 404

    # The chat's next turn neither loads it nor plans on it: it starts a new list.
    act_in_own_org(db, uid)  # the turn's worker runs as the job's member
    turn = Turn(
        db.execute('SELECT id, organization_id, user_id, chat_session_id FROM jobs WHERE id = %s', (jid,)).fetchone(),
        FakeProvider([]),
        None,
    )
    turn._load()
    assert turn.lists == {}
    ctx = ToolContext(user_id=uid, job_id=str(jid), session_id=str(sid), project_id=project(db, uid))
    stored = propose_plan._store({}, propose_plan.plans.build(_plan_args()), ctx)
    assert isinstance(stored, dict) and stored['list_id'] != lid

    assert tc.post(f'/v1/lists/{lid}/restore').status_code == 204
    assert lid in {lst['id'] for lst in tc.get('/v1/lists').json()['lists']}
    assert [r['cells']['full_name'] for r in tc.get(f'/v1/lists/{lid}/rows').json()['rows']] == ['Ada']
    assert tc.post(f'/v1/lists/{lid}/restore').status_code == 404


def test_delete_is_refused_while_the_chat_runs_and_for_other_members(client, db):
    from app.main import app

    tc, uid = client
    sid, jid = _job(db, uid, 'find people')
    lid = _list(db, uid, sid)
    db.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (jid,))
    assert tc.delete(f'/v1/lists/{lid}').json()['detail']['code'] == 'LIST.BUSY'
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (jid,))

    other = _user(db)
    org = member(db, uid).org_id
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, other, 'member'),
    )
    sign_in(app, give_role(db, CurrentUser(id=other, org_id=org, org_role='member')))
    assert tc.delete(f'/v1/lists/{lid}').json()['detail']['code'] == 'LIST.NOT_OWNER'
    sign_in(app, give_role(db, CurrentUser(id=other, org_id=org, org_role='admin')))
    assert tc.delete(f'/v1/lists/{lid}').status_code == 204


def test_an_ai_column_deletes_and_restores_with_its_cells(client, db):
    tc, uid = client
    sid, _ = _job(db, uid, 'find people')
    lid = _list(db, uid, sid)
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'ai_hq', 'HQ', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Where is the HQ?', 'fill': {'status': 'done'}})),
    )
    db.execute('UPDATE list_rows SET cells = cells || \'{"ai_hq": "Milan"}\' WHERE list_id = %s', (lid,))

    def keys():
        return [c['key'] for c in tc.get(f'/v1/lists/{lid}').json()['columns']]

    assert tc.delete(f'/v1/lists/{lid}/columns/email').status_code == 404  # only AI columns
    assert tc.delete(f'/v1/lists/{lid}/columns/ai_hq').status_code == 204
    assert 'ai_hq' not in keys()
    assert tc.post(f'/v1/lists/{lid}/columns/ai_hq/fill').status_code == 404
    # A new column with the same label gets a fresh key: the deleted one keeps its own.
    from app.lists import routes

    routes_enqueue, routes._enqueue_fill = routes._enqueue_fill, lambda *a: None
    try:
        added = tc.post(f'/v1/lists/{lid}/columns', json={'label': 'HQ', 'prompt': 'Where is the HQ?'}).json()
    finally:
        routes._enqueue_fill = routes_enqueue
    assert added['key'] != 'ai_hq'

    restored = tc.post(f'/v1/lists/{lid}/columns/ai_hq/restore').json()
    assert restored['key'] == 'ai_hq' and restored['position'] == 9
    assert 'ai_hq' in keys()
    assert tc.get(f'/v1/lists/{lid}/rows').json()['rows'][0]['cells']['ai_hq'] == 'Milan'
    # A fill in flight keeps its column.
    assert tc.delete(f'/v1/lists/{lid}/columns/{added["key"]}').json()['detail']['code'] == 'LIST.COLUMN_FILLING'


def test_restore_is_scoped_to_the_organization(client, db):
    tc, _ = client
    other = _user(db)
    sid, _ = _job(db, other, 'find people')
    lid = _list(db, other, sid)
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (lid,))
    assert tc.post(f'/v1/lists/{lid}/restore').json()['detail']['code'] == 'LIST.NOT_FOUND'
    assert db.execute('SELECT deleted_at IS NOT NULL AS d FROM lists WHERE id = %s', (lid,)).fetchone()['d']


def test_a_plan_never_reuses_a_deleted_research_column(db):
    from app.agent import plan as plans

    uid = _user(db)
    sid, _ = _job(db, uid, 'find people')
    lid = _list(db, uid, sid)
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config, deleted_at)'
        " VALUES (%s, 'ai_score', 'Score', 'ai', 9, '{}', now())",
        (lid,),
    )
    args = _plan_args()
    args['columns'] = [*args['columns'], {'label': 'Score', 'research': 'How good is it?'}]
    plan = plans.build(args)
    plans.claim_research_keys(db, lid, plan)
    key = plan['columns'][-1]['key']
    assert key != 'ai_score'
    plans.apply_columns(db, lid, plan)
    live = db.execute(
        "SELECT key FROM list_columns WHERE list_id = %s AND kind = 'ai' AND deleted_at IS NULL", (lid,)
    ).fetchall()
    assert [r['key'] for r in live] == [key]
