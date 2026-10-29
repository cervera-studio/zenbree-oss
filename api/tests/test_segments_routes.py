"""/v1/segments (#938): the routes, their scope, their limits and what a publish writes."""

from __future__ import annotations

import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.audience import conflicts
from app.segments import evaluate
from app.segments.evaluate import lock_project
from tests.conftest import conninfo_of, member
from tests.test_agent import _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_audience_sql import _identity, _raise_conflict, _row
from tests.test_segments_sql import MARCO_RULE, people

CAP = 50  # segments per project, as docs/contracts-m1.md states it
DEEP = {'type': 'group', 'op': 'and', 'children': [{'type': 'group', 'op': 'and', 'children': [MARCO_RULE]}]}


def _code(res) -> str | None:
    return res.json()['detail']['code'] if res.is_error else None


def _spend(db) -> tuple[int, int]:
    return (
        db.execute('SELECT count(*) AS n FROM vendor_calls').fetchone()['n'],
        db.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'],
    )


@needs_db
def test_a_segment_is_previewed_saved_published_and_explained(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    preview = tc.post('/v1/segments/preview', json={'entity': 'person', 'rule': MARCO_RULE}).json()
    assert (preview['count'], preview['pending']) == (2, 0)
    made = tc.post('/v1/segments', json={'name': ' CEOs ', 'entity': 'person', 'rule': MARCO_RULE})
    assert made.status_code == 201
    seg = made.json()
    assert (seg['name'], seg['version'], seg['published_at'], seg['members'], seg['problems']) == (
        'CEOs',
        1,
        None,
        0,
        [],
    )
    assert _code(tc.post(f'/v1/segments/{seg["id"]}/refresh')) == 'SEGMENT.NOT_PUBLISHED'

    spent = _spend(db)
    published = tc.post(f'/v1/segments/{seg["id"]}/publish').json()
    assert published['applied'] == {'joined': 2, 'entered': 0, 'exited': 0, 'merged': 0, 'members': 2}
    assert published['max_credits'] == 0
    assert published['segment']['members'] == preview['count']
    assert _spend(db) == spent
    assert tc.post(f'/v1/segments/{seg["id"]}/publish').json()['applied'] == {
        'joined': 0,
        'entered': 0,
        'exited': 0,
        'merged': 0,
        'members': 2,
    }

    page = tc.get(f'/v1/segments/{seg["id"]}/members', params={'limit': 1}).json()
    rest = tc.get(f'/v1/segments/{seg["id"]}/members', params={'after': page['next_cursor']}).json()
    assert len(page['members']) == len(rest['members']) == 1 and rest['next_cursor'] is None
    assert {m['identity_id'] for m in page['members'] + rest['members']} >= {p['marco']}

    why = tc.get(f'/v1/segments/{seg["id"]}/members/{p["marco"]}/why').json()
    assert why['matches'] and why['member']
    lists = [[r['list_name'] for r in c['rows'] if r['hit']] for c in why['rule']['children']]
    assert lists == [['A'], ['B'], ['A']]
    assert [s['name'] for s in tc.get('/v1/segments').json()['segments']] == ['CEOs']


@needs_db
def test_an_edit_needs_the_version_it_read_and_applies_a_published_rule(client, db):
    tc, uid = client
    people(db, uid=uid)
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    tc.post(f'/v1/segments/{seg["id"]}/publish')
    stricter = {**MARCO_RULE, 'children': [*MARCO_RULE['children'], {'type': 'fit', 'op': 'gte', 'low': 88}]}
    edited = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'rule': stricter}).json()
    assert (edited['segment']['version'], edited['segment']['members']) == (2, 1)  # Marco's best fit is 85
    assert (edited['joining'], edited['leaving'], edited['max_credits']) == (0, 1, 0)
    stale = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'name': 'x'})
    assert (stale.status_code, _code(stale), stale.json()['detail']['version']) == (409, 'SEGMENT.STALE_VERSION', 2)
    renamed = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 2, 'name': 'Top'}).json()
    assert (renamed['segment']['name'], renamed['joining'], renamed['leaving']) == ('Top', 0, 0)


def _kinds(db, segment_id) -> dict[str, list[str]]:
    rows = db.execute(
        'SELECT identity_id::text, kind FROM segment_transitions WHERE segment_id = %s ORDER BY seq', (segment_id,)
    ).fetchall()
    found: dict[str, list[str]] = {}
    for r in rows:
        found.setdefault(r['identity_id'], []).append(r['kind'])
    return found


@needs_db
def test_publish_and_a_wider_rule_ask_first_and_join_silently_by_default(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    giulia = _identity(db, p['giulia'])
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    edited = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'name': 'Draft'}).json()
    assert (edited['joining'], edited['leaving']) == (0, 0)
    narrow = {**MARCO_RULE, 'children': [*MARCO_RULE['children'], {'type': 'fit', 'op': 'gte', 'low': 88}]}
    tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 2, 'rule': narrow})
    published = tc.post(f'/v1/segments/{seg["id"]}/publish', json={'include_current': True}).json()
    assert (published['applied']['joined'], published['applied']['entered'], published['max_credits']) == (1, 1, 0)
    assert _kinds(db, seg['id']) == {giulia: ['enter']}

    wider = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 3, 'rule': MARCO_RULE}).json()
    assert (wider['joining'], wider['leaving'], wider['max_credits']) == (1, 0, 0)
    assert _kinds(db, seg['id']) == {giulia: ['enter'], p['marco']: ['baseline']}

    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    tc.post(f'/v1/segments/{seg["id"]}/refresh')
    tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 4, 'rule': narrow})
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))
    again = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 5, 'rule': MARCO_RULE, 'include_new': True}).json()
    assert again['joining'] == 1
    assert _kinds(db, seg['id']) == {giulia: ['enter'], p['marco']: ['baseline', 'exit', 'enter']}


def _history(db, segment_id, identity_id) -> list[tuple[str, int]]:
    rows = db.execute(
        'SELECT kind, episode FROM segment_transitions WHERE segment_id = %s AND identity_id = %s ORDER BY seq',
        (segment_id, identity_id),
    ).fetchall()
    return [(r['kind'], r['episode']) for r in rows]


@needs_db
def test_an_edit_records_a_join_that_the_data_caused_before_it_as_an_entry(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    tc.post(f'/v1/segments/{seg["id"]}/publish')
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    tc.post(f'/v1/segments/{seg["id"]}/refresh')
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))

    edited = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'rule': MARCO_RULE}).json()
    assert (edited['joining'], edited['leaving'], edited['segment']['members']) == (0, 0, 2)
    assert _history(db, seg['id'], p['marco']) == [('baseline', 1), ('exit', 1), ('enter', 2)]
    versions = db.execute(
        "SELECT segment_version FROM segment_transitions WHERE segment_id = %s AND kind = 'enter'", (seg['id'],)
    ).fetchall()
    assert [r['segment_version'] for r in versions] == [1]  # judged by the rule it had before the edit


@needs_db
def test_a_join_the_data_causes_while_an_edit_runs_is_an_entry(client, db, monkeypatch):
    """Cell writers do not take the project's lock, so a change can commit while an edit runs. Each statement reads
    what committed before it: the change lands after the edit read the segment and before it applies the new rule."""
    tc, uid = client
    p = people(db, uid=uid)
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    tc.post(f'/v1/segments/{seg["id"]}/publish')
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    tc.post(f'/v1/segments/{seg["id"]}/refresh')

    real = evaluate.apply

    def data_lands_first(conn, segment, *args, **kwargs):
        if segment['version'] == 2:
            db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))
        return real(conn, segment, *args, **kwargs)

    monkeypatch.setattr(evaluate, 'apply', data_lands_first)
    edited = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'rule': MARCO_RULE}).json()
    assert (edited['joining'], edited['leaving'], edited['segment']['members']) == (0, 0, 2)
    assert _history(db, seg['id'], p['marco']) == [('baseline', 1), ('exit', 1), ('enter', 2)]


@needs_db
def test_a_person_the_data_and_the_edit_move_apart_keeps_their_membership(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    any_fit = {**MARCO_RULE, 'children': MARCO_RULE['children'][:2]}
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    tc.post(f'/v1/segments/{seg["id"]}/publish')
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    stays = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'rule': any_fit, 'include_new': True}).json()
    assert (stays['joining'], stays['leaving'], stays['segment']['members']) == (0, 0, 2)
    assert _history(db, seg['id'], p['marco']) == [('baseline', 1)]

    tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 2, 'rule': MARCO_RULE})
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))
    away = tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 3, 'rule': NARROW}).json()
    assert (away['joining'], away['leaving'], away['segment']['members']) == (0, 0, 1)
    assert _history(db, seg['id'], p['marco']) == [('baseline', 1), ('exit', 1)]


@pytest.mark.parametrize(('include_new', 'kind'), [(False, 'baseline'), (True, 'enter')])
@needs_db
def test_the_edit_that_fixes_a_frozen_rule_asks_for_every_join_it_makes(client, db, include_new, kind):
    """A frozen rule judges nobody, so the edit cannot tell a join the data caused while it was frozen from a join
    the new rule causes: the edit's answer decides both, and ``joining`` counts both."""
    tc, uid = client
    p = people(db, uid=uid)
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    frozen = {
        **MARCO_RULE,
        'children': [*MARCO_RULE['children'], {'type': 'presence', 'field': column, 'op': 'is_missing'}],
    }
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': frozen}).json()
    tc.post(f'/v1/segments/{seg["id"]}/publish')
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    tc.post(f'/v1/segments/{seg["id"]}/refresh')
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'uses_hubspot'", (p['a'],))
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))

    body = {'version': 1, 'rule': MARCO_RULE, 'include_new': include_new}
    edited = tc.patch(f'/v1/segments/{seg["id"]}', json=body).json()
    assert (edited['joining'], edited['leaving'], edited['segment']['problems']) == (1, 0, [])
    assert _history(db, seg['id'], p['marco']) == [('baseline', 1), ('exit', 1), (kind, 2)]


NARROW = {**MARCO_RULE, 'children': [*MARCO_RULE['children'], {'type': 'fit', 'op': 'gte', 'low': 88}]}


def _asked_then_edited(tc, seg_id, version, rule) -> tuple[tuple, tuple]:
    body = {'entity': 'person', 'rule': rule, 'segment_id': seg_id}
    asked = tc.post('/v1/segments/preview', json=body).json()
    edited = tc.patch(f'/v1/segments/{seg_id}', json={'version': version, 'rule': rule}).json()
    keys = ('joining', 'leaving', 'max_credits')
    return tuple(asked[k] for k in keys), tuple(edited[k] for k in keys)


@needs_db
def test_the_preview_of_an_edit_answers_what_the_edit_then_does(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    plain = tc.post('/v1/segments/preview', json={'entity': 'person', 'rule': NARROW}).json()
    assert (plain['joining'], plain['leaving'], plain['max_credits']) == (None, None, None)
    assert _asked_then_edited(tc, seg['id'], 1, MARCO_RULE) == ((0, 0, 0), (0, 0, 0))  # a draft changes no member

    tc.post(f'/v1/segments/{seg["id"]}/publish')
    spent, ledger = _spend(db), _kinds(db, seg['id'])
    asked = tc.post('/v1/segments/preview', json={'entity': 'person', 'rule': MARCO_RULE, 'segment_id': seg['id']})
    assert (asked.json()['count'], _spend(db), _kinds(db, seg['id'])) == (2, spent, ledger)
    assert _asked_then_edited(tc, seg['id'], 2, NARROW) == ((0, 1, 0), (0, 1, 0))
    assert _asked_then_edited(tc, seg['id'], 3, MARCO_RULE) == ((1, 0, 0), (1, 0, 0))

    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    tc.post(f'/v1/segments/{seg["id"]}/refresh')
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))
    assert _asked_then_edited(tc, seg['id'], 4, MARCO_RULE) == ((0, 0, 0), (0, 0, 0))  # the data's join, not the edit's
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    assert _asked_then_edited(tc, seg['id'], 5, NARROW) == ((0, 0, 0), (0, 0, 0))  # the data's exit, not the edit's

    companies = tc.post(
        '/v1/segments/preview',
        json={
            'entity': 'company',
            'rule': {'type': 'group', 'op': 'and', 'children': [{'type': 'fit', 'op': 'gte', 'low': 1}]},
            'segment_id': seg['id'],
        },
    )
    assert (companies.status_code, _code(companies)) == (422, 'SEGMENT.INVALID_RULE')


@needs_db
def test_the_preview_of_an_edit_counts_a_merged_member_at_the_identity_left_standing(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    twin = _identity(db, _row(db, p['b'], {'full_name': 'M. Bianchi', 'title': 'CTO', 'email': 'mb@acme.verify.test'}))
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    frozen = {
        **MARCO_RULE,
        'children': [*MARCO_RULE['children'], {'type': 'presence', 'field': column, 'op': 'is_missing'}],
    }
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': frozen}).json()
    tc.post(f'/v1/segments/{seg["id"]}/publish')
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'uses_hubspot'", (p['a'],))
    conflicts.resolve(
        db, _raise_conflict(db, p['org'], p['pid'], p['marco'], twin), p['pid'], p['uid'], 'merge', into=twin
    )
    assert _asked_then_edited(tc, seg['id'], 1, MARCO_RULE) == ((0, 0, 0), (0, 0, 0))


@needs_db
def test_a_bad_rule_is_refused_with_its_path(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    deep = {'type': 'group', 'op': 'and', 'children': [DEEP]}
    res = tc.post('/v1/segments/preview', json={'entity': 'person', 'rule': deep})
    assert res.status_code == 422
    assert (res.json()['detail']['code'], res.json()['detail']['path']) == (
        'SEGMENT.INVALID_RULE',
        '/children/0/children/0/children/0',
    )
    theirs = people(db)  # another organization's list
    accounts = db.execute(
        "INSERT INTO lists (organization_id, user_id, project_id, name, entity) VALUES (%s, %s, %s, 'Accounts', 'company')"
        ' RETURNING id::text',
        (p['org'], uid, p['pid']),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, 'hq', 'HQ', 'ai', 1)", (accounts,)
    )
    for list_id, key, reason in (
        (theirs['a'], 'uses_hubspot', 'list_not_found'),
        (accounts, 'hq', 'list_not_found'),  # a list of the project, of accounts, in a rule over people
        (p['a'], 'nope', 'column_not_found'),
    ):
        column = {'kind': 'column', 'list_id': list_id, 'key': key}
        rule = {'type': 'group', 'op': 'or', 'children': [{'type': 'presence', 'field': column, 'op': 'is_filled'}]}
        res = tc.post('/v1/segments', json={'name': 'x', 'entity': 'person', 'rule': rule})
        assert (res.status_code, _code(res)) == (422, 'SEGMENT.INVALID_RULE')
        assert (res.json()['detail']['path'], res.json()['detail']['reason']) == ('/children/0/field', reason)


@needs_db
def test_the_fifty_first_segment_is_refused(client, db, monkeypatch):
    tc, uid = client
    people(db, uid=uid)
    monkeypatch.setattr('app.segments.routes.MAX_PER_PROJECT', 2)
    body = {'name': 'x', 'entity': 'person', 'rule': MARCO_RULE}
    first = tc.post('/v1/segments', json=body).json()
    assert tc.post('/v1/segments', json=body).status_code == 201
    full = tc.post('/v1/segments', json=body)
    assert (full.status_code, _code(full)) == (409, 'SEGMENT.LIMIT_REACHED')
    assert tc.delete(f'/v1/segments/{first["id"]}').status_code == 204
    assert tc.post('/v1/segments', json=body).status_code == 201


@needs_db
def test_the_list_reads_as_often_for_six_segments_as_for_two(client, db, monkeypatch):
    """At 50 segments a project, a read for each segment on the list would add up across clients."""
    tc, uid = client
    p = people(db, uid=uid)
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    hubspot = {'type': 'group', 'op': 'and', 'children': [{'type': 'presence', 'field': column, 'op': 'is_filled'}]}
    statements: list[object] = []
    reads: list[int] = []
    execute = psycopg.Connection.execute

    def counted(self, query, *args, **kwargs):
        statements.append(query)
        return execute(self, query, *args, **kwargs)

    def listed() -> list[dict]:
        before = len(statements)
        segments = tc.get('/v1/segments').json()['segments']
        reads.append(len(statements) - before)
        return segments

    assert tc.get('/v1/segments').json() == {'segments': []}
    monkeypatch.setattr(psycopg.Connection, 'execute', counted)
    ceos = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    tc.post(f'/v1/segments/{ceos["id"]}/publish')
    tc.post('/v1/segments', json={'name': 'HubSpot', 'entity': 'person', 'rule': hubspot})
    assert [(s['name'], s['members']) for s in listed()] == [('HubSpot', 0), ('CEOs', 2)]
    for _ in range(4):
        tc.post('/v1/segments', json={'name': 'HubSpot', 'entity': 'person', 'rule': hubspot})
    assert len(listed()) == 6
    assert reads[1] == reads[0]
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'uses_hubspot'", (p['a'],))
    assert [len(s['problems']) for s in listed()] == [1, 1, 1, 1, 1, 0]


def _waiting_on_a_lock(db, n: int, alive) -> None:
    deadline = time.monotonic() + 10
    while (
        db.execute(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ).fetchone()['n']
        < n
    ):
        assert alive() and time.monotonic() < deadline, f'fewer than {n} requests waited on a lock'
        time.sleep(0.01)


@needs_db
def test_two_creates_at_the_last_free_place_make_one_segment(client, db):
    """The cap holds when two creates race: the second one counts after the first one committed."""
    tc, uid = client
    p = people(db, uid=uid)
    for _ in range(CAP - 1):
        db.execute(
            "INSERT INTO segments (organization_id, project_id, name, entity, rule) VALUES (%s, %s, 'S', 'person', '{}')",
            (p['org'], p['pid']),
        )
    holder = psycopg.connect(conninfo_of(db), row_factory=dict_row)
    # Holds both creates before their count, or, without the route's lock, after it at the insert's key check.
    holder.execute('SELECT 1 FROM projects WHERE id = %s FOR UPDATE', (p['pid'],))
    body = {'name': 'Last', 'entity': 'person', 'rule': MARCO_RULE}
    codes: list[int] = []

    def create() -> None:
        codes.append(tc.post('/v1/segments', json=body).status_code)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for t in threads:
        t.start()
    _waiting_on_a_lock(db, 2, lambda: all(t.is_alive() for t in threads))
    holder.rollback()
    holder.close()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 409]
    count = 'SELECT count(*) AS n FROM segments WHERE project_id = %s AND deleted_at IS NULL'
    assert db.execute(count, (p['pid'],)).fetchone()['n'] == CAP


def _theirs(db, mine, uid, where: str) -> dict:
    if where == 'organization':
        return people(db, uid=_user(db))
    other = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Other client') RETURNING id",
        (mine['org'], uid),
    ).fetchone()['id']
    return people(db, uid=uid, pid=other)


@needs_db
@pytest.mark.parametrize('where', ['project', 'organization'])
def test_another_organizations_or_projects_segment_is_not_found(client, db, where):
    tc, uid = client
    mine = people(db, uid=uid)
    p = _theirs(db, mine, uid, where)
    sid = db.execute(
        'INSERT INTO segments (organization_id, project_id, name, entity, rule, published_at)'
        " VALUES (%s, %s, 'Theirs', 'person', %s, now()) RETURNING id",
        (member(db, p['uid']).org_id, p['pid'], Jsonb(MARCO_RULE)),
    ).fetchone()['id']
    calls = [
        tc.get(f'/v1/segments/{sid}'),
        tc.patch(f'/v1/segments/{sid}', json={'version': 1, 'name': 'x'}),
        tc.post(f'/v1/segments/{sid}/publish'),
        tc.post(f'/v1/segments/{sid}/refresh'),
        tc.get(f'/v1/segments/{sid}/members'),
        tc.get(f'/v1/segments/{sid}/members/{p["marco"]}/why'),
        tc.post('/v1/segments/preview', json={'entity': 'person', 'rule': MARCO_RULE, 'segment_id': str(sid)}),
        tc.delete(f'/v1/segments/{sid}'),
    ]
    assert [(r.status_code, _code(r)) for r in calls] == [(404, 'SEGMENT.NOT_FOUND')] * len(calls)
    assert db.execute('SELECT name, deleted_at FROM segments WHERE id = %s', (sid,)).fetchone() == {
        'name': 'Theirs',
        'deleted_at': None,
    }
    seg = tc.post('/v1/segments', json={'name': 'Mine', 'entity': 'person', 'rule': MARCO_RULE}).json()
    assert [s['id'] for s in tc.get('/v1/segments').json()['segments']] == [seg['id']]
    stranger = tc.get(f'/v1/segments/{seg["id"]}/members/{p["marco"]}/why')
    assert (stranger.status_code, _code(stranger)) == (404, 'SEGMENT.IDENTITY_NOT_FOUND')
    assert tc.post('/v1/segments/preview', json={'entity': 'person', 'rule': MARCO_RULE}).json()['count'] == 2


@needs_db
def test_a_segment_whose_column_is_deleted_reports_it_and_keeps_its_members(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    rule = {'type': 'group', 'op': 'and', 'children': [{'type': 'presence', 'field': column, 'op': 'is_missing'}]}
    seg = tc.post('/v1/segments', json={'name': 'No HubSpot yet', 'entity': 'person', 'rule': rule}).json()
    assert tc.post(f'/v1/segments/{seg["id"]}/publish').json()['segment']['members'] == 2
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'uses_hubspot'", (p['a'],))
    refreshed = tc.post(f'/v1/segments/{seg["id"]}/refresh').json()
    assert refreshed['applied'] == {'joined': 0, 'entered': 0, 'exited': 0, 'merged': 0, 'members': 2}
    gone = [{'path': '/children/0/field', 'reason': 'column_not_found', 'list_id': p['a'], 'key': 'uses_hubspot'}]
    assert refreshed['segment']['problems'] == gone
    why = tc.get(f'/v1/segments/{seg["id"]}/members/{p["marco"]}/why').json()
    assert (why['member'], why.get('problems')) == (True, gone)


@needs_db
def test_the_why_answer_links_the_source_of_an_ai_filled_cell(client, db):
    tc, uid = client
    p = people(db, uid=uid)
    source = {'url': 'https://acme.verify.test/stack', 'title': 'Our stack'}
    filled = {'status': 'filled', 'source': source, 'error': None, 'at': '2026-10-21T09:00:00+00:00'}
    db.execute(
        'UPDATE list_rows SET cells = cells || %s, cell_meta = cell_meta || %s WHERE id = %s',
        (Jsonb({'uses_hubspot': 'yes'}), Jsonb({'uses_hubspot': filled}), p['marco_a']),
    )
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    rule = {'type': 'group', 'op': 'and', 'children': [{'type': 'presence', 'field': column, 'op': 'is_filled'}]}
    seg = tc.post('/v1/segments', json={'name': 'On HubSpot', 'entity': 'person', 'rule': rule}).json()
    res = tc.get(f'/v1/segments/{seg["id"]}/members/{p["marco"]}/why')
    assert res.status_code == 200
    [row] = res.json()['rule']['children'][0]['rows']
    assert {k: row[k] for k in ('hit', 'value', 'status', 'at', 'source')} == {
        'hit': True,
        'value': 'yes',
        'status': 'filled',
        'at': filled['at'],
        'source': source,
    }


@needs_db
def test_a_publish_waits_for_the_project_lock_before_it_locks_the_segment(client, db):
    """An edit takes the project's lock, then the segment's row. A publish that took them the other way round could
    deadlock with it."""
    tc, uid = client
    p = people(db, uid=uid)
    seg = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    holder = psycopg.connect(conninfo_of(db), row_factory=dict_row)
    lock_project(holder, p['pid'])
    answers: list = []
    thread = threading.Thread(target=lambda: answers.append(tc.post(f'/v1/segments/{seg["id"]}/publish')))
    thread.start()
    deadline = time.monotonic() + 10
    while not db.execute(
        "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event = 'advisory'"
    ).fetchone():
        assert thread.is_alive() and time.monotonic() < deadline, 'the publish never waited on the project lock'
        time.sleep(0.01)
    with db.transaction():
        assert db.execute('SELECT 1 FROM segments WHERE id = %s FOR NO KEY UPDATE NOWAIT', (seg['id'],)).fetchone()
    holder.rollback()
    holder.close()
    thread.join()
    assert answers[0].json()['applied'] == {'joined': 2, 'entered': 0, 'exited': 0, 'merged': 0, 'members': 2}
