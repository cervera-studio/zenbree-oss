"""The segments beat (#938): marked people are judged again, and the members end as a fresh full evaluation would
leave them."""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

import psycopg
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from structlog.testing import capture_logs

from app.admin import settings as admin_settings
from app.audience import conflicts
from app.core.system_reasons import SystemReason
from app.segments import drain, routes
from app.segments.compile import compile_rule, judged
from app.segments.evaluate import stored_rule
from tests.conftest import act_for, call_system, conninfo_of, hold, member, project
from tests.test_agent import _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_audience_sql import _identity, _raise_conflict, _row
from tests.test_home_overview import _list
from tests.test_segments_sql import MARCO_RULE, live, people, run, segment, transitions

pytestmark = needs_db


def step_deadline() -> float:
    return time.monotonic() + drain.BUDGET_S


def batch(db, pid) -> int | None:
    with db.transaction():
        return drain.batch(db, pid, step_deadline())


def full_due(db, pid) -> bool:
    with db.transaction():
        return drain.full_due(db, pid, step_deadline())


def drained(db, pid) -> None:
    """What one project's share of the beat does, on the test's connection, until no mark is due."""
    for _ in range(100):
        if not full_due(db, pid) and not batch(db, pid):
            return
    raise AssertionError('the drain did not finish')


def due_marks(db, pid) -> int:
    return db.execute(
        'SELECT count(*) AS n FROM segment_dirty WHERE project_id = %s AND not_before <= now()', (pid,)
    ).fetchone()['n']


def fresh(db, seg) -> set[str]:
    """Who a full evaluation joins now."""
    query, params = judged(compile_rule(stored_rule(seg)), seg['project_id'])
    return {str(r['identity_id']) for r in db.execute(query, params).fetchall() if r['matched'] and not r['deferred']}


def set_title(db, row, title) -> None:
    db.execute('UPDATE list_rows SET cells = cells || %s WHERE id = %s', (Jsonb({'title': title}), row))


def test_a_cell_edit_moves_the_person_on_the_next_drain_and_a_second_drain_writes_nothing(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    giulia = _identity(db, p['giulia'])
    set_title(db, p['giulia'], 'CTO')
    drained(db, p['pid'])
    set_title(db, p['giulia'], 'CEO')
    drained(db, p['pid'])
    assert [t for t in transitions(db, seg) if t[0] == giulia] == [
        (giulia, 'baseline', 1),
        (giulia, 'exit', 1),
        (giulia, 'enter', 2),
    ]
    before = transitions(db, seg)
    assert batch(db, p['pid']) == 0
    assert transitions(db, seg) == before


def test_a_batch_judges_only_its_people(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    db.execute('UPDATE list_rows SET rating = %s WHERE id = %s', ('good', p['marco_a']))
    db.execute('UPDATE list_rows SET relevance_score = 91 WHERE id = %s', (p['giulia'],))
    assert batch(db, p['pid']) == 1
    assert set(live(db, seg)) == {p['marco'], _identity(db, p['giulia'])}
    assert [t[1] for t in transitions(db, seg)] == ['baseline', 'baseline']


def test_a_batch_claims_at_most_its_size(db, monkeypatch):
    monkeypatch.setattr(drain, 'BATCH', 2)
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    db.execute('UPDATE list_rows SET relevance_score = 10 WHERE list_id = ANY(%s)', ([p['a'], p['b']],))
    assert batch(db, p['pid']) == 2
    assert due_marks(db, p['pid']) == 1
    assert batch(db, p['pid']) == 1
    assert live(db, seg) == {}


def test_more_marks_than_the_threshold_turn_into_one_full_evaluation_of_each_segment(db, monkeypatch):
    monkeypatch.setattr(drain, 'FULL_EVAL_AT', 3)
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    db.execute('UPDATE list_rows SET relevance_score = 10 WHERE list_id = ANY(%s)', ([p['a'], p['b']],))
    assert batch(db, p['pid']) == 3
    assert db.execute('SELECT full_now_at IS NULL AS n FROM segments WHERE id = %s', (seg['id'],)).fetchone()['n']
    db.execute('UPDATE list_rows SET relevance_score = 95 WHERE list_id = ANY(%s)', ([p['a'], p['b']],))
    set_title(db, p['giulia'], 'Founder')
    assert due_marks(db, p['pid']) == 4
    assert batch(db, p['pid']) == 4
    assert due_marks(db, p['pid']) == 0
    owed = db.execute('SELECT full_now_at <= now() AS due FROM segments WHERE id = %s', (seg['id'],)).fetchone()
    assert owed['due']
    assert full_due(db, p['pid'])
    assert set(live(db, seg)) == fresh(db, seg) == {p['marco']}
    assert db.execute('SELECT full_now_at FROM segments WHERE id = %s', (seg['id'],)).fetchone()['full_now_at'] is None


def test_the_catch_up_joins_a_write_that_left_no_mark(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE, published=False)
    set_title(db, p['giulia'], 'CTO')
    db.execute('UPDATE segments SET published_at = now() WHERE id = %s', (seg['id'],))
    run(db, seg, 'baseline')
    db.execute('ALTER TABLE list_rows DISABLE TRIGGER segment_mark_rows')
    set_title(db, p['giulia'], 'CEO')
    db.execute('ALTER TABLE list_rows ENABLE TRIGGER segment_mark_rows')
    db.execute("UPDATE segments SET full_due_at = now() + interval '1 minute' WHERE id = %s", (seg['id'],))
    assert not full_due(db, p['pid'])
    db.execute('UPDATE segments SET full_due_at = now() WHERE id = %s', (seg['id'],))
    assert full_due(db, p['pid'])
    giulia = _identity(db, p['giulia'])
    assert transitions(db, seg)[-1] == (giulia, 'enter', 1)
    assert not full_due(db, p['pid'])


def test_a_person_with_a_held_cell_keeps_a_mark_and_joins_after_the_charge_settles(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    paolo = _row(db, p['a'], {'full_name': 'Paolo Neri', 'email': 'paolo@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 88 WHERE id = %s', (paolo,))
    reservation = hold(db, p['uid'])
    set_title(db, paolo, 'CEO')
    db.execute(
        "INSERT INTO row_charges (hold_id, row_id, keys, credits, state) VALUES (%s, %s, '{title}', 1, 'held')",
        (reservation, paolo),
    )
    drained(db, p['pid'])
    identity = _identity(db, paolo)
    assert identity not in live(db, seg)
    waiting = db.execute('SELECT identity_id::text, not_before > now() AS later FROM segment_dirty').fetchall()
    assert waiting == [{'identity_id': identity, 'later': True}]
    db.execute('SELECT release_reservation(%s)', (reservation,))
    db.execute('UPDATE segment_dirty SET not_before = now()')
    drained(db, p['pid'])
    assert transitions(db, seg)[-1] == (identity, 'enter', 1)


def held_ceo(db, p, name) -> tuple[str, str]:
    row = _row(db, p['a'], {'full_name': name, 'email': f'{name.lower().replace(" ", ".")}@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 88 WHERE id = %s', (row,))
    reservation = hold(db, p['uid'])
    set_title(db, row, 'CEO')
    db.execute(
        "INSERT INTO row_charges (hold_id, row_id, keys, credits, state) VALUES (%s, %s, '{title}', 1, 'held')",
        (reservation, row),
    )
    return row, reservation


def waiting_marks(db, pid) -> int:
    return db.execute(
        'SELECT count(*) AS n FROM segment_dirty WHERE project_id = %s AND not_before > now()', (pid,)
    ).fetchone()['n']


def test_a_batch_judges_people_and_companies_each_with_their_own_rows(db):
    p = people(db)
    accounts = _list(db, p['uid'], 'Accounts', entity='company', pid=p['pid'])
    acme = _row(db, accounts, {'name': 'Acme', 'domain': 'acme.verify.test', 'city': 'Milano'}, entity='company')
    rule = {
        'type': 'group',
        'op': 'and',
        'children': [{'type': 'text', 'field': {'kind': 'canonical', 'key': 'city'}, 'op': 'eq', 'values': ['roma']}],
    }
    companies = db.execute(
        'INSERT INTO segments (organization_id, project_id, name, entity, rule, published_at)'
        " VALUES (%s, %s, 'Rome', 'company', %s, now()) RETURNING *",
        (p['org'], p['pid'], Jsonb(rule)),
    ).fetchone()
    persons = segment(db, p, MARCO_RULE)
    for seg in (companies, persons):
        run(db, seg, 'baseline')
    db.execute(
        'UPDATE list_rows SET cells = cells || %s WHERE id = ANY(%s)', (Jsonb({'city': 'Roma'}), [acme, p['giulia']])
    )
    set_title(db, p['giulia'], 'CTO')
    drained(db, p['pid'])
    assert set(live(db, companies)) == fresh(db, companies) == {_identity(db, acme)}
    assert set(live(db, persons)) == fresh(db, persons) == {p['marco']}


def test_a_full_pass_of_many_segments_marks_each_held_person_once(db, monkeypatch):
    monkeypatch.setattr(drain, 'FULL_EVAL_AT', 1)
    p = people(db)
    for _ in range(3):
        run(db, segment(db, p, MARCO_RULE), 'baseline')
    held_ceo(db, p, 'Paolo Neri')
    held_ceo(db, p, 'Anna Riva')
    drained(db, p['pid'])
    assert due_marks(db, p['pid']) == 0
    assert waiting_marks(db, p['pid']) == 2
    db.execute('UPDATE segment_dirty SET not_before = now()')
    drained(db, p['pid'])
    assert waiting_marks(db, p['pid']) == 2


def test_a_full_pass_leaves_no_mark_for_a_held_row_the_list_excludes(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    paolo, _ = held_ceo(db, p, 'Paolo Neri')
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (paolo,))
    db.execute('DELETE FROM segment_dirty WHERE project_id = %s', (p['pid'],))
    db.execute('UPDATE segments SET full_due_at = now() WHERE id = %s', (seg['id'],))
    assert full_due(db, p['pid'])
    assert waiting_marks(db, p['pid']) == 0


def test_a_charge_that_settles_during_a_batch_leaves_the_person_a_mark(db, monkeypatch):
    p = people(db)
    first, second = segment(db, p, MARCO_RULE), segment(db, p, MARCO_RULE)
    for seg in (first, second):
        run(db, seg, 'baseline')
    paolo, reservation = held_ceo(db, p, 'Paolo Neri')
    applied = drain.apply

    def settle_after_the_first(conn, *args, **kwargs):
        done = applied(conn, *args, **kwargs)
        conn.execute('SELECT release_reservation(%s)', (reservation,))
        return done

    monkeypatch.setattr(drain, 'apply', settle_after_the_first)
    assert batch(db, p['pid'])
    monkeypatch.setattr(drain, 'apply', applied)
    db.execute('UPDATE segment_dirty SET not_before = now()')
    drained(db, p['pid'])
    identity = _identity(db, paolo)
    assert [identity in live(db, seg) for seg in (first, second)] == [True, True]


def test_a_charge_that_settles_during_a_full_evaluation_leaves_the_person_a_mark(db, monkeypatch):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    paolo, reservation = held_ceo(db, p, 'Paolo Neri')
    db.execute('DELETE FROM segment_dirty WHERE project_id = %s', (p['pid'],))
    db.execute('UPDATE segments SET full_due_at = now() WHERE id = %s', (seg['id'],))
    applied = drain.apply

    def settle_after_it(conn, *args, **kwargs):
        done = applied(conn, *args, **kwargs)
        conn.execute('SELECT release_reservation(%s)', (reservation,))
        return done

    monkeypatch.setattr(drain, 'apply', settle_after_it)
    assert full_due(db, p['pid'])
    monkeypatch.setattr(drain, 'apply', applied)
    db.execute('UPDATE segment_dirty SET not_before = now()')
    drained(db, p['pid'])
    assert _identity(db, paolo) in live(db, seg)


def test_the_fallback_keeps_a_catch_up_that_a_publish_owes(db, monkeypatch):
    monkeypatch.setattr(drain, 'FULL_EVAL_AT', 1)
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    owed = db.execute(
        'UPDATE segments SET full_due_at = now() + %s WHERE id = %s RETURNING full_due_at', (drain.CATCH_UP, seg['id'])
    ).fetchone()['full_due_at']
    db.execute('UPDATE list_rows SET relevance_score = 10 WHERE list_id = ANY(%s)', ([p['a'], p['b']],))
    drained(db, p['pid'])
    assert live(db, seg) == {}
    after = db.execute('SELECT full_due_at FROM segments WHERE id = %s', (seg['id'],)).fetchone()['full_due_at']
    assert after == owed


def test_the_beat_takes_the_organizations_with_the_oldest_work_first(db, monkeypatch):
    monkeypatch.setattr(drain, 'ORGANIZATIONS', 3)
    owing = people(db)
    db.execute('UPDATE segments SET full_due_at = now() WHERE id = %s', (segment(db, owing, MARCO_RULE)['id'],))
    newest_first = sorted((str(uuid.uuid4()) for _ in range(4)), reverse=True)
    for org in newest_first:
        db.execute(
            'INSERT INTO segment_dirty (organization_id, project_id, identity_id) VALUES (%s, %s, %s)',
            (org, uuid.uuid4(), uuid.uuid4()),
        )
    assert call_system(SystemReason.DUE_WORK, drain.due_organizations) == [owing['org'], *newest_first[:2]]


def test_a_project_that_fails_does_not_stop_the_next_one(db, monkeypatch):
    first = people(db)
    other = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Other') RETURNING id",
        (first['org'], first['uid']),
    ).fetchone()['id']
    second = people(db, uid=first['uid'], pid=str(other))
    for p in (first, second):
        run(db, segment(db, p, MARCO_RULE), 'baseline')
        set_title(db, p['giulia'], 'CTO')
    worked = drain.drain_project
    failed = []

    def fail_the_first(project_id, deadline):
        if not failed:
            failed.append(str(project_id))
            raise RuntimeError('planted')
        return worked(project_id, deadline)

    monkeypatch.setattr(drain, 'drain_project', fail_the_first)
    admin_settings.invalidate()
    call_system(SystemReason.DUE_WORK, drain.run)
    left = {
        str(r['project_id'])
        for r in db.execute('SELECT DISTINCT project_id FROM segment_dirty WHERE not_before <= now()').fetchall()
    }
    assert left == set(failed)


def test_a_merge_split_across_batches_writes_no_entry(db, monkeypatch):
    monkeypatch.setattr(drain, 'BATCH', 1)
    p = people(db)
    twin_row = _row(db, p['b'], {'full_name': 'M. Bianchi', 'title': 'CEO', 'email': 'mb@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 82 WHERE id = %s', (twin_row,))
    twin = _identity(db, twin_row)
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    assert p['marco'] not in live(db, seg)
    conflict = _raise_conflict(db, p['org'], p['pid'], p['marco'], twin)
    conflicts.resolve(db, conflict, p['pid'], p['uid'], 'merge', into=p['marco'])
    drained(db, p['pid'])
    after = [t for t in transitions(db, seg) if t[1] != 'baseline']
    assert after == [(twin, 'exit', 1), (p['marco'], 'merged', 1)]


def after_the_first_stage(monkeypatch, write) -> None:
    staged = drain.stage
    done = []

    def stage(conn, *args, **kwargs):
        people = staged(conn, *args, **kwargs)
        if not done:
            done.append(write(conn))
        return people

    monkeypatch.setattr(drain, 'stage', stage)


def test_a_merge_between_the_stage_and_the_apply_gives_the_folded_person_no_entry(db, monkeypatch):
    p = people(db)
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    twin_row = _row(db, p['b'], {'full_name': 'M. Bianchi', 'title': 'CEO', 'email': 'mb@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 82 WHERE id = %s', (twin_row,))
    twin = _identity(db, twin_row)
    conflict = _raise_conflict(db, p['org'], p['pid'], p['marco'], twin)
    after_the_first_stage(
        monkeypatch, lambda conn: conflicts.resolve(conn, conflict, p['pid'], p['uid'], 'merge', into=p['marco'])
    )
    drained(db, p['pid'])
    assert [t for t in transitions(db, seg) if t[1] != 'baseline'] == [(p['marco'], 'enter', 1)]


def test_a_row_that_moves_to_a_member_between_the_stage_and_the_apply_keeps_the_member(db, monkeypatch):
    p = people(db)
    paolo = _row(db, p['a'], {'full_name': 'Paolo Neri', 'title': 'CEO', 'email': 'paolo@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 88 WHERE id = %s', (paolo,))
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    giulia, left = _identity(db, p['giulia']), _identity(db, paolo)
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (p['giulia'],))
    after_the_first_stage(
        monkeypatch,
        lambda conn: conn.execute(
            'UPDATE audience_row_links SET identity_id = %s WHERE list_row_id = %s', (giulia, paolo)
        ),
    )
    drained(db, p['pid'])
    assert [t for t in transitions(db, seg) if t[1] != 'baseline'] == [(left, 'exit', 1)]
    assert live(db, seg)[giulia] == (uuid.UUID(paolo), 1)


def test_the_beat_works_each_organization_in_its_scope_and_drops_the_marks_of_a_deleted_project(db):
    first, second = people(db), people(db)
    for p in (first, second):
        run(db, segment(db, p, MARCO_RULE), 'baseline')
        set_title(db, p['giulia'], 'CTO')
    gone = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Gone') RETURNING id",
        (first['org'], first['uid']),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO segment_dirty (organization_id, project_id, identity_id) VALUES (%s, %s, %s)',
        (first['org'], gone, first['marco']),
    )
    db.execute('DELETE FROM projects WHERE id = %s', (gone,))
    admin_settings.invalidate()
    assert call_system(SystemReason.DUE_WORK, drain.run) == {'paused': False, 'organizations': 2, 'projects': 2}
    assert db.execute('SELECT count(*) AS n FROM segment_dirty').fetchone()['n'] == 0
    for p in (first, second):
        assert _identity(db, p['giulia']) not in live(db, segment_of(db, p))


def segment_of(db, p) -> dict:
    return db.execute('SELECT * FROM segments WHERE project_id = %s', (p['pid'],)).fetchone()


def test_the_pause_keeps_every_mark_and_refuses_evaluation(db, client, monkeypatch):
    tc, uid = client
    p = people(db, uid=uid)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    set_title(db, p['giulia'], 'CTO')
    draft = segment(db, p, MARCO_RULE, published=False)
    with monkeypatch.context() as m:
        m.setattr(admin_settings, 'all_settings', lambda: {drain.PAUSED: True})
        assert call_system(SystemReason.DUE_WORK, drain.run)['paused']
        assert due_marks(db, p['pid']) == 1
        refused = [
            tc.post('/v1/segments/preview', json={'entity': 'person', 'rule': MARCO_RULE}),
            tc.post(f'/v1/segments/{draft["id"]}/publish'),
            tc.post(f'/v1/segments/{seg["id"]}/refresh'),
            tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'rule': MARCO_RULE}),
        ]
        assert [(r.status_code, r.json()['detail']['code']) for r in refused] == [
            (503, 'SEGMENT.EVALUATION_PAUSED')
        ] * 4
        assert tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'name': 'Renamed'}).status_code == 200
        published = db.execute('SELECT published_at FROM segments WHERE id = %s', (draft['id'],)).fetchone()
        assert published['published_at'] is None
    call_system(SystemReason.DUE_WORK, drain.run)
    assert _identity(db, p['giulia']) not in live(db, seg)


def test_a_publish_and_a_rule_edit_owe_a_catch_up(db, client):
    tc, uid = client
    people(db, uid=uid)
    made = tc.post('/v1/segments', json={'name': 'CEOs', 'entity': 'person', 'rule': MARCO_RULE}).json()
    due = 'SELECT full_due_at - now() AS wait FROM segments WHERE id = %s'
    assert db.execute(due, (made['id'],)).fetchone()['wait'] is None
    tc.post(f'/v1/segments/{made["id"]}/publish')
    assert db.execute(due, (made['id'],)).fetchone()['wait'] > drain.CATCH_UP * 0.9
    db.execute('UPDATE segments SET full_due_at = NULL WHERE id = %s', (made['id'],))
    tc.patch(f'/v1/segments/{made["id"]}', json={'version': 1, 'name': 'Renamed'})
    assert db.execute(due, (made['id'],)).fetchone()['wait'] is None
    tc.patch(f'/v1/segments/{made["id"]}', json={'version': 2, 'rule': MARCO_RULE})
    assert db.execute(due, (made['id'],)).fetchone()['wait'] > drain.CATCH_UP * 0.9


PEOPLE = 4
edit = st.one_of(
    st.tuples(st.just('title'), st.integers(0, PEOPLE - 1), st.sampled_from(['CEO', 'CTO'])),
    st.tuples(st.just('failed'), st.integers(0, PEOPLE - 1), st.booleans()),
    st.tuples(st.just('fit'), st.integers(0, PEOPLE - 1), st.sampled_from([50, 85, None])),
    st.tuples(st.just('excluded'), st.integers(0, PEOPLE - 1), st.booleans()),
    st.tuples(st.just('list_deleted'), st.booleans()),
    st.tuples(st.just('link'), st.integers(0, PEOPLE - 1), st.integers(0, PEOPLE - 1)),
    st.tuples(st.just('drain'), st.integers(1, 3)),
)


def _apply_edit(db, rows, identities, lists, e) -> None:
    kind, *args = e
    if kind == 'title':
        set_title(db, rows[args[0]], args[1])
    elif kind == 'failed':
        meta = Jsonb({'title': {'status': 'failed' if args[1] else 'filled'}})
        db.execute('UPDATE list_rows SET cell_meta = cell_meta || %s WHERE id = %s', (meta, rows[args[0]]))
    elif kind == 'fit':
        db.execute('UPDATE list_rows SET relevance_score = %s WHERE id = %s', (args[1], rows[args[0]]))
    elif kind == 'excluded':
        db.execute('UPDATE list_rows SET excluded = %s WHERE id = %s', (args[1], rows[args[0]]))
    elif kind == 'list_deleted':
        db.execute('UPDATE lists SET deleted_at = CASE WHEN %s THEN now() END WHERE id = %s', (args[0], lists[1]))
    elif kind == 'link':
        db.execute(
            'UPDATE audience_row_links SET identity_id = %s WHERE list_row_id = %s',
            (identities[args[1]], rows[args[0]]),
        )


def test_the_drain_converges_on_a_fresh_full_evaluation(db, monkeypatch):
    uid = _user(db)
    org, pid = member(db, uid).org_id, project(db, uid)
    lists = [_list(db, uid, 'A'), _list(db, uid, 'B')]
    rule = {
        'type': 'group',
        'op': 'and',
        'children': [
            {'type': 'text', 'field': {'kind': 'canonical', 'key': 'title'}, 'op': 'eq', 'values': ['ceo']},
            {'type': 'fit', 'op': 'gte', 'low': 80},
        ],
    }

    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(st.lists(edit, max_size=12), st.integers(1, 3))
    def converge(edits, size):
        monkeypatch.setattr(drain, 'BATCH', size)
        with db.transaction(force_rollback=True):
            identities, rows = [], []
            for i in range(PEOPLE):
                identity = db.execute(
                    'INSERT INTO audience_identities (organization_id, project_id, entity)'
                    " VALUES (%s, %s, 'person') RETURNING id",
                    (org, pid),
                ).fetchone()['id']
                row = db.execute(
                    'INSERT INTO list_rows (list_id, cells, dedupe_key, relevance_score) VALUES (%s, %s, %s, 85)'
                    ' RETURNING id',
                    (lists[i % 2], Jsonb({'full_name': f'P{i}', 'title': 'CEO'}), f'k{i}'),
                ).fetchone()['id']
                db.execute(
                    'INSERT INTO audience_row_links (list_row_id, organization_id, identity_id) VALUES (%s, %s, %s)',
                    (row, org, identity),
                )
                identities.append(identity)
                rows.append(row)
            seg = db.execute(
                'INSERT INTO segments (organization_id, project_id, name, entity, rule, published_at)'
                " VALUES (%s, %s, 'S', 'person', %s, now()) RETURNING *",
                (org, pid, Jsonb(rule)),
            ).fetchone()
            drain.apply(db, seg, 'baseline')
            db.execute('DELETE FROM segment_dirty')
            for e in edits:
                if e[0] == 'drain':
                    for _ in range(e[1]):
                        drain.batch(db, pid, step_deadline())
                else:
                    _apply_edit(db, rows, identities, lists, e)
            while drain.batch(db, pid, step_deadline()):
                while drain.full_due(db, pid, step_deadline()):
                    pass
            assert set(live(db, seg)) == fresh(db, seg)
            before = transitions(db, seg)
            assert drain.batch(db, pid, step_deadline()) == 0
            assert transitions(db, seg) == before

    converge()


class _SeenAtCharge:
    """A recorder that reads, from another connection, the billed cells at the moment the charge is recorded: a
    reader must not see them yet, or a person is judged on a cell no charge hides."""

    def __init__(self, reader, key: str) -> None:
        self.reader, self.key, self.seen = reader, key, []

    def record(self, conn, call_id, list_id, row_ids, price) -> int:
        assert row_ids, 'nothing to charge'
        self.seen += [
            r['seen']
            for r in self.reader.execute(
                'SELECT r.cells ? %s AS seen FROM list_rows r WHERE r.id = ANY(%s::uuid[])', (self.key, list(row_ids))
            ).fetchall()
        ]
        return 0

    def lock(self, conn) -> None:
        pass

    def close(self, conn) -> int:
        return 0


def test_every_writer_of_a_billed_cell_charges_it_in_the_transaction_that_writes_it(db, monkeypatch):
    from types import SimpleNamespace

    from app.lists import ai_columns, delivery
    from app.scheduling import run_credits

    p = people(db)
    reader = psycopg.connect(conninfo_of(db), autocommit=True, row_factory=dict_row)
    every = SimpleNamespace(price=SimpleNamespace(found=lambda cells: True), rows=10)

    chat = _SeenAtCharge(reader, 'email')
    with db.transaction():
        update = {'row_id': p['marco_a'], 'cells': {'email': 'marco.a@acme.verify.test'}}
        delivery.deliver(db, chat, every, 'call', [update], p['a'], 'person')

    scheduled = _SeenAtCharge(reader, 'email')
    monkeypatch.setattr(run_credits, 'quote', lambda config: every)
    with db.transaction():
        run_credits.deliver(
            db, {'id': 'run', 'config': {'list_id': p['b']}}, scheduled, [{'email': 'new@acme.verify.test'}], 'person'
        )

    filled = _SeenAtCharge(reader, 'uses_hubspot')
    fill = ai_columns._Run(p['a'], 'uses_hubspot')
    fill.tab = filled
    monkeypatch.setattr(fill, 'publish', lambda event, data: None)
    act_for(p['org'])
    cell = {'status': 'filled', 'source': 'ai', 'error': None, 'value': 'yes'}
    assert ai_columns._write_cell(fill, p['giulia'], cell)

    reader.close()
    assert chat.seen == [False]
    assert scheduled.seen == [], 'the new row is not there for a reader at all'
    assert filled.seen == [False]
    assert (
        db.execute("SELECT cells->>'uses_hubspot' AS v FROM list_rows WHERE id = %s", (p['giulia'],)).fetchone()['v']
        == 'yes'
    )


def owed_now(db, seg) -> None:
    db.execute("UPDATE segments SET full_now_at = full_now_at - interval '1 day' WHERE id = %s", (seg['id'],))


def test_a_segment_frozen_while_its_people_change_catches_up_once_its_column_is_back(db):
    p = people(db)
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    rule = {'type': 'group', 'op': 'and', 'children': [{'type': 'presence', 'field': column, 'op': 'is_missing'}]}
    seg = segment(db, p, rule)
    run(db, seg, 'baseline')
    giulia = _identity(db, p['giulia'])
    assert giulia in live(db, seg)
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'uses_hubspot'", (p['a'],))
    db.execute('UPDATE list_rows SET cells = cells || %s WHERE id = %s', (Jsonb({'uses_hubspot': 'yes'}), p['giulia']))
    drained(db, p['pid'])
    owed_now(db, seg)
    drained(db, p['pid'])
    db.execute("UPDATE list_columns SET deleted_at = NULL WHERE list_id = %s AND key = 'uses_hubspot'", (p['a'],))
    owed_now(db, seg)
    drained(db, p['pid'])
    assert set(live(db, seg)) == fresh(db, seg) == {p['marco']}


def test_a_segment_whose_evaluation_fails_does_not_stop_the_others(db, monkeypatch):
    p = people(db)
    failing, other = segment(db, p, MARCO_RULE), segment(db, p, MARCO_RULE)
    for seg in (failing, other):
        run(db, seg, 'baseline')
    db.execute('UPDATE segments SET full_due_at = now() WHERE project_id = %s', (p['pid'],))
    set_title(db, p['giulia'], 'CTO')
    applied = drain.apply

    def fail_one(conn, segment, *args, **kwargs):
        if segment['id'] == failing['id']:
            conn.execute('SELECT 1 / 0')
        return applied(conn, segment, *args, **kwargs)

    monkeypatch.setattr(drain, 'apply', fail_one)
    admin_settings.invalidate()
    with capture_logs() as logs:
        call_system(SystemReason.DUE_WORK, drain.run)
    assert set(live(db, other)) == fresh(db, other) == {p['marco']}
    assert due_marks(db, p['pid']) == 0
    owed = db.execute(
        'SELECT least(full_due_at, full_now_at) > now() AS later FROM segments WHERE id = %s', (failing['id'],)
    ).fetchone()
    assert owed['later']
    assert {e['segment_id'] for e in logs if e['event'] == 'segments.drain.segment_failed'} == {str(failing['id'])}


def test_a_segment_too_slow_for_the_beat_fails_alone_and_the_next_beat_goes_on(db, monkeypatch):
    monkeypatch.setattr(drain, 'BUDGET_S', 0.2)
    monkeypatch.setattr(drain, 'GRACE_S', 0.3)
    p = people(db)
    slow, other = segment(db, p, MARCO_RULE), segment(db, p, MARCO_RULE)
    for seg in (slow, other):
        run(db, seg, 'baseline')
    set_title(db, p['giulia'], 'CTO')
    db.execute('DELETE FROM segment_dirty WHERE project_id = %s', (p['pid'],))
    db.execute("UPDATE segments SET full_due_at = now() - interval '1 minute' WHERE id = %s", (slow['id'],))
    db.execute('UPDATE segments SET full_due_at = now() WHERE id = %s', (other['id'],))
    assert set(live(db, other)) != fresh(db, other)
    applied = drain.apply

    def sleep_in_one(conn, segment, *args, **kwargs):
        if segment['id'] == slow['id']:
            conn.execute('SELECT pg_sleep(3)')
        return applied(conn, segment, *args, **kwargs)

    monkeypatch.setattr(drain, 'apply', sleep_in_one)
    admin_settings.invalidate()
    with capture_logs() as logs:
        for _ in range(2):
            call_system(SystemReason.DUE_WORK, drain.run)
    assert set(live(db, other)) == fresh(db, other)
    owed = db.execute(
        "SELECT full_now_at - now() > interval '4 minutes' AS later FROM segments WHERE id = %s", (slow['id'],)
    ).fetchone()
    assert owed['later']
    assert {e['segment_id'] for e in logs if e['event'] == 'segments.drain.segment_failed'} == {str(slow['id'])}


def test_a_segment_that_fails_in_every_batch_keeps_its_owed_full_evaluation(db, monkeypatch):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    applied = drain.apply

    def fail_staged(conn, segment, *args, people=None, **kwargs):
        if people is not None:
            conn.execute('SELECT 1 / 0')
        return applied(conn, segment, *args, people=people, **kwargs)

    monkeypatch.setattr(drain, 'apply', fail_staged)
    set_title(db, p['giulia'], 'CTO')
    assert batch(db, p['pid'])
    # The next batch comes one minute later, when the owed evaluation is 4 minutes away.
    db.execute("UPDATE segments SET full_now_at = now() + interval '4 minutes' WHERE id = %s", (seg['id'],))
    set_title(db, p['giulia'], 'CEO')
    assert batch(db, p['pid'])
    wait = db.execute('SELECT full_now_at - now() AS wait FROM segments WHERE id = %s', (seg['id'],)).fetchone()
    assert wait['wait'] <= timedelta(minutes=4)


def test_held_people_judged_again_never_turn_into_a_full_evaluation(db, monkeypatch):
    monkeypatch.setattr(drain, 'FULL_EVAL_AT', 1)
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    held_ceo(db, p, 'Paolo Neri')
    held_ceo(db, p, 'Anna Riva')
    drained(db, p['pid'])
    db.execute('UPDATE segment_dirty SET not_before = now()')
    assert batch(db, p['pid']) == 2
    assert db.execute('SELECT full_now_at FROM segments WHERE id = %s', (seg['id'],)).fetchone()['full_now_at'] is None
    assert waiting_marks(db, p['pid']) == 2


def test_the_drain_works_the_projects_of_an_organization_oldest_mark_first(db, monkeypatch):
    p = people(db)
    oldest_first = [uuid.uuid4() for _ in range(5)]
    for pid in reversed(oldest_first):
        db.execute(
            "INSERT INTO projects (id, organization_id, user_id, name) VALUES (%s, %s, %s, 'P')",
            (pid, p['org'], p['uid']),
        )
    for pid in oldest_first:
        db.execute(
            'INSERT INTO segment_dirty (organization_id, project_id, identity_id) VALUES (%s, %s, %s)',
            (p['org'], pid, uuid.uuid4()),
        )
    worked = []
    monkeypatch.setattr(drain, 'drain_project', lambda project_id, deadline: worked.append(project_id))
    admin_settings.invalidate()
    call_system(SystemReason.DUE_WORK, drain.run)
    assert worked == oldest_first


def test_a_rule_edit_reads_the_pause_before_it_takes_the_project_lock(db, client, monkeypatch):
    tc, uid = client
    p = people(db, uid=uid)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    held = []

    def paused(key):
        with psycopg.connect(conninfo_of(db), row_factory=dict_row) as other:
            got = other.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended('segments:' || %s::text, 0)) AS got", (p['pid'],)
            ).fetchone()['got']
        held.append(not got)
        return False

    monkeypatch.setattr(routes, 'is_disabled', paused)
    assert tc.patch(f'/v1/segments/{seg["id"]}', json={'version': 1, 'rule': MARCO_RULE}).status_code == 200
    assert held == [False]
