"""What GET /v1/lists and /v1/lists/{id} serve the lists screens (#228)."""

from psycopg.types.json import Jsonb

from tests.conftest import project
from tests.test_agent import _job, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db


@needs_db
def test_list_summary_status_delivered_credits_brief_and_title(client, db):
    tc, uid = client
    sid, jid = _job(db, uid, 'CEOs of agencies in Milan')
    db.execute("UPDATE chat_sessions SET title = 'Agenzie Milano' WHERE id = %s", (sid,))
    db.execute("UPDATE jobs SET status = 'succeeded', credits_spent = 7 WHERE id = %s", (jid,))
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity, chat_session_id) VALUES (default_organization(%s), %s, %s, 'L', 'person', %s) RETURNING id::text",
        (uid, project(db, uid), uid, sid),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'email', 'Email', 'static', 1, '{\"required\": true}')",
        (lid,),
    )
    for cells, excluded in (({'email': 'a@x.test'}, False), ({'email': ''}, False), ({'email': 'c@x.test'}, True)):
        db.execute(
            'INSERT INTO list_rows (list_id, cells, excluded) VALUES (%s, %s, %s)', (lid, Jsonb(cells), excluded)
        )
    db.execute(
        'INSERT INTO list_plans (list_id, version, plan, approved_at, approved_rows) VALUES (%s, 1, %s, now(), 40)',
        (lid, Jsonb({'target_rows': 50})),
    )

    def summary():
        one = tc.get(f'/v1/lists/{lid}').json()
        [listed] = tc.get('/v1/lists').json()['lists']
        assert {k: listed[k] for k in one if k in listed} == {k: one[k] for k in listed if k in one}
        return {k: one[k] for k in ('status', 'row_count', 'delivered_count', 'target_rows', 'credits_used')}

    one = tc.get(f'/v1/lists/{lid}').json()
    assert (one['brief'], one['chat_title']) == ('CEOs of agencies in Milan', 'Agenzie Milano')
    # one row filled, one missing the required email, one excluded; target = the approved rows
    assert summary() == {'status': 'ready', 'row_count': 3, 'delivered_count': 1, 'target_rows': 40, 'credits_used': 7}

    _, j2 = _job(db, uid, 'and in Turin', session_id=sid)
    db.execute("UPDATE jobs SET status = 'running', credits_spent = 3 WHERE id = %s", (j2,))
    assert summary()['status'] == 'running' and summary()['credits_used'] == 10
    db.execute("UPDATE jobs SET status = 'needs_input' WHERE id = %s", (j2,))
    assert summary()['status'] == 'needs_input'
    db.execute('INSERT INTO list_plans (list_id, version, plan) VALUES (%s, 2, %s)', (lid, Jsonb({'target_rows': 200})))
    assert summary()['status'] == 'plan_pending' and summary()['target_rows'] == 200
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", (j2,))
    assert summary()['status'] == 'ready'  # the user moved on: the old plan no longer waits
    db.execute('UPDATE list_rows SET excluded = true WHERE list_id = %s', (lid,))
    assert summary()['status'] == 'empty'  # no delivered row: not Completato · 0 di N (#735)


@needs_db
def test_list_summary_without_chat_and_scoped_to_the_owner(client, db):
    tc, uid = client
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (default_organization(%s), %s, %s, 'Mine', 'company') RETURNING id::text",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    other = _user(db)
    osid, ojid = _job(db, other, 'secret brief')
    db.execute('UPDATE jobs SET credits_spent = 99 WHERE id = %s', (ojid,))
    olid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity, chat_session_id) VALUES (default_organization(%s), %s, %s, 'Theirs', 'company', %s)"
        ' RETURNING id::text',
        (other, project(db, other), other, osid),
    ).fetchone()['id']

    [mine] = tc.get('/v1/lists').json()['lists']
    assert mine['id'] == lid
    assert {
        k: mine[k] for k in ('status', 'delivered_count', 'target_rows', 'credits_used', 'brief', 'chat_title')
    } == {
        'status': 'empty',
        'delivered_count': 0,
        'target_rows': None,
        'credits_used': 0,
        'brief': None,
        'chat_title': None,
    }
    assert tc.get(f'/v1/lists/{olid}').status_code == 404


@needs_db
def test_people_per_company_from_the_chats_person_list(client, db):
    tc, uid = client
    sid, _ = _job(db, uid, 'agencies and their CEOs')

    def new_list(entity, session):
        return db.execute(
            'INSERT INTO lists (organization_id, project_id, user_id, name, entity, chat_session_id) VALUES (default_organization(%s), %s, %s, %s, %s, %s) RETURNING id::text',
            (uid, project(db, uid), uid, entity, entity, session),
        ).fetchone()['id']

    def add(lid, cells, excluded=False):
        db.execute(
            'INSERT INTO list_rows (list_id, cells, excluded) VALUES (%s, %s, %s)', (lid, Jsonb(cells), excluded)
        )

    def counts(lid):
        return {r['cells']['name']: r.get('people_count') for r in tc.get(f'/v1/lists/{lid}/rows').json()['rows']}

    companies = new_list('company', sid)
    for cells in (
        {'name': 'Acme', 'domain': 'https://www.acme.com/'},
        {'name': 'Beta Srl', 'domain': 'beta.it'},
        {'name': 'Gamma'},
        {'name': 'Delta', 'domain': 'delta.it'},
    ):
        add(companies, cells)
    assert set(counts(companies).values()) == {None}  # no person list in the chat: no count

    people = new_list('person', sid)
    add(people, {'full_name': 'A1', 'company_domain': 'acme.com', 'company_name': 'Acme Inc'})
    add(people, {'full_name': 'A2', 'company_domain': 'http://acme.com/team', 'company_name': 'ACME'})
    add(people, {'full_name': 'A3', 'company_domain': 'acme.com'}, excluded=True)  # excluded: not counted
    add(people, {'full_name': 'B1', 'company_name': ' beta srl '})  # no domain: falls back to the name
    add(people, {'full_name': 'B2', 'company_domain': 'other.it', 'company_name': 'Beta Srl'})  # domains differ
    add(people, {'full_name': 'G1', 'company_domain': 'gamma.io', 'company_name': 'Gamma'})  # company has no domain
    # another chat's and another user's people never count
    other_chat = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (default_organization(%s), %s, %s) RETURNING id',
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    add(new_list('person', other_chat), {'full_name': 'X', 'company_domain': 'acme.com'})
    other = _user(db)
    osid, _ = _job(db, other, 'theirs')
    olid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity, chat_session_id) VALUES (default_organization(%s), %s, %s, 'p', 'person', %s) RETURNING id",
        (other, project(db, other), other, osid),
    ).fetchone()['id']
    add(olid, {'full_name': 'Y', 'company_domain': 'acme.com'})

    assert counts(companies) == {'Acme': 2, 'Beta Srl': 1, 'Gamma': 1, 'Delta': 0}
    assert 'people_count' not in tc.get(f'/v1/lists/{people}/rows').json()['rows'][0]

    by_name = {r['cells']['name']: r for r in tc.get(f'/v1/lists/{companies}/rows').json()['rows']}
    assert [p['full_name'] for p in by_name['Acme']['people']] == ['A1', 'A2']
    assert by_name['Delta']['people'] == []
    assert set(by_name['Acme']['people'][0]) == {'row_id', 'list_id', 'full_name', 'title'}  # no vendor cell leaves
    assert by_name['Acme']['people'][0]['list_id'] == people

    for n in range(5):
        add(people, {'full_name': f'D{n}', 'company_domain': 'delta.it'})
    delta = next(r for r in tc.get(f'/v1/lists/{companies}/rows').json()['rows'] if r['cells']['name'] == 'Delta')
    assert delta['people_count'] == 5 and len(delta['people']) == 3  # the count is whole, the names are bounded
