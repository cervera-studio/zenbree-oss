"""Segment membership (#938) in the database: what ``apply`` writes, and what it leaves alone."""

from __future__ import annotations

import threading
import time
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.audience import conflicts
from app.lists.rows import add_rows
from app.segments import evaluate
from app.segments.compile import compile_rule, judged
from app.segments.evaluate import Applied, JoinKind, apply, lock_project, preview, why
from app.segments.rules import Rule, RuleGroup
from tests.conftest import conninfo_of, hold, member, project
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_audience_sql import _identity, _raise_conflict, _row
from tests.test_home_overview import _list

LI = 'https://www.linkedin.com/in/marco-bianchi/'


MARCO_RULE = {
    'type': 'group',
    'op': 'and',
    'children': [
        {'type': 'text', 'field': {'kind': 'canonical', 'key': 'title'}, 'op': 'contains', 'values': ['ceo']},
        {'type': 'presence', 'field': {'kind': 'canonical', 'key': 'email'}, 'op': 'is_filled'},
        {'type': 'fit', 'op': 'gte', 'low': 80},
    ],
}


def people(db, uid=None, pid=None):
    """Marco: a CEO with fit 85 and no email in list A, with an email in list B (one identity through his LinkedIn
    URL). Giulia: a CEO with an email in list A, fit 90, whose AI column is empty."""
    uid = uid or _user(db)
    a, b = _list(db, uid, 'A', pid=pid), _list(db, uid, 'B', pid=pid)
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, 'uses_hubspot', 'HubSpot', 'ai', 1)",
        (a,),
    )
    marco_a = _row(db, a, {'full_name': 'Marco Bianchi', 'title': 'CEO', 'linkedin_url': LI})
    marco_b = _row(db, b, {'full_name': 'Marco Bianchi', 'email': 'marco@acme.verify.test', 'linkedin_url': LI})
    giulia = _row(db, a, {'full_name': 'Giulia Verdi', 'title': 'Co-founder & CEO', 'email': 'giulia@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (marco_a,))
    db.execute('UPDATE list_rows SET relevance_score = 60 WHERE id = %s', (marco_b,))
    db.execute('UPDATE list_rows SET relevance_score = 90 WHERE id = %s', (giulia,))
    return {
        'uid': uid,
        'org': member(db, uid).org_id,
        'pid': pid or project(db, uid),
        'a': a,
        'b': b,
        'marco_a': marco_a,
        'marco_b': marco_b,
        'giulia': giulia,
        'marco': _identity(db, marco_a),
    }


def segment(db, p, rule, published=True) -> dict:
    return db.execute(
        'INSERT INTO segments (organization_id, project_id, name, entity, rule, published_at)'
        " VALUES (%s, %s, 'S', 'person', %s, CASE WHEN %s THEN now() END) RETURNING *",
        (p['org'], p['pid'], Jsonb(rule), published),
    ).fetchone()


def run(db, seg, join_kind: JoinKind = 'enter') -> Applied:
    with db.transaction():
        lock_project(db, seg['project_id'])
        return apply(db, seg, join_kind).applied


def transitions(db, seg) -> list[tuple[str, str, int]]:
    rows = db.execute(
        'SELECT identity_id::text, kind, episode FROM segment_transitions WHERE segment_id = %s ORDER BY seq',
        (seg['id'],),
    ).fetchall()
    return [(r['identity_id'], r['kind'], r['episode']) for r in rows]


def versions(db, seg) -> list[dict]:
    """Each member record's row version: an update or a row lock changes ``xmax``, a new version ``xmin`` and ``ctid``."""
    return db.execute(
        'SELECT identity_id, xmin::text, xmax::text, ctid::text FROM segment_members WHERE segment_id = %s'
        ' ORDER BY identity_id',
        (seg['id'],),
    ).fetchall()


def live(db, seg) -> dict:
    rows = db.execute(
        'SELECT identity_id::text, list_row_id, episode FROM segment_members'
        ' WHERE segment_id = %s AND exited_at IS NULL',
        (seg['id'],),
    ).fetchall()
    return {r['identity_id']: (r['list_row_id'], r['episode']) for r in rows}


@needs_db
def test_a_person_is_judged_over_all_their_rows_and_shown_by_the_best_fit(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    assert run(db, seg) == Applied(joined=2, entered=2, exited=0, merged=0, members=2)
    assert live(db, seg)[p['marco']] == (uuid.UUID(p['marco_a']), 1)
    verdict = why(db, seg, p['marco'])
    assert verdict['matches'] and verdict['member']
    title, email, fit = verdict['rule']['children']
    assert [r['list_name'] for r in title['rows'] if r['hit']] == ['A']
    assert [r['list_name'] for r in email['rows'] if r['hit']] == ['B']
    assert [(r['list_name'], r['hit']) for r in fit['rows']] == [('A', True), ('B', False)]


@needs_db
def test_a_second_apply_writes_nothing(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg)
    before = versions(db, seg)
    assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=0, members=2)
    assert versions(db, seg) == before


@needs_db
def test_a_person_who_leaves_and_comes_back_gets_the_next_episode(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg)
    db.execute('UPDATE list_rows SET relevance_score = 60 WHERE id = %s', (p['marco_a'],))
    assert run(db, seg) == Applied(joined=0, entered=0, exited=1, merged=0, members=1)
    assert p['marco'] not in live(db, seg)
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))
    assert run(db, seg) == Applied(joined=1, entered=1, exited=0, merged=0, members=2)
    assert live(db, seg)[p['marco']][1] == 2
    assert [(k, e) for i, k, e in transitions(db, seg) if i == p['marco']] == [('enter', 1), ('exit', 1), ('enter', 2)]


@needs_db
def test_a_merge_carries_the_membership_with_the_higher_episode_and_is_never_an_entry(db):
    p = people(db)
    twin_row = _row(db, p['b'], {'full_name': 'M. Bianchi', 'title': 'CEO', 'email': 'mb@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 82 WHERE id = %s', (twin_row,))
    twin = _identity(db, twin_row)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (twin_row,))
    run(db, seg)
    db.execute('UPDATE list_rows SET relevance_score = 82 WHERE id = %s', (twin_row,))
    run(db, seg)
    assert live(db, seg)[twin][1] == 2

    conflict = _raise_conflict(db, p['org'], p['pid'], p['marco'], twin)
    assert conflicts.resolve(db, conflict, p['pid'], p['uid'], 'merge', into=p['marco'])['identity_id'] == p['marco']
    entries_before = [t for t in transitions(db, seg) if t[1] == 'enter']
    assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=1, members=2)
    assert live(db, seg)[p['marco']] == (uuid.UUID(p['marco_a']), 2)
    assert twin not in live(db, seg)
    assert [t for t in transitions(db, seg) if t[1] == 'enter'] == entries_before
    assert transitions(db, seg)[-2:] == [(twin, 'exit', 2), (p['marco'], 'merged', 2)]
    assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=0, members=2)

    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = ANY(%s)', ([p['marco_a'], twin_row],))
    run(db, seg)
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))
    run(db, seg)
    assert transitions(db, seg)[-2:] == [(p['marco'], 'exit', 2), (p['marco'], 'enter', 3)]


@needs_db
def test_a_merge_that_lands_after_the_carry_waits_for_the_next_apply(db, monkeypatch):
    p = people(db)
    twin_row = _row(db, p['b'], {'full_name': 'M. Bianchi', 'title': 'CEO', 'email': 'mb@acme.verify.test'})
    db.execute('UPDATE list_rows SET relevance_score = 82 WHERE id = %s', (twin_row,))
    twin = _identity(db, twin_row)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    run(db, seg)
    assert p['marco'] not in live(db, seg)

    with monkeypatch.context() as m:
        m.setattr(evaluate, '_MERGED', 'SELECT 0 AS merged')
        conflict = _raise_conflict(db, p['org'], p['pid'], p['marco'], twin)
        conflicts.resolve(db, conflict, p['pid'], p['uid'], 'merge', into=p['marco'])
        before = transitions(db, seg)
        assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=0, members=2)
        assert transitions(db, seg) == before
    assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=1, members=2)
    assert transitions(db, seg)[-2:] == [(twin, 'exit', 1), (p['marco'], 'merged', 1)]


@needs_db
def test_merges_chained_after_the_carry_hold_the_member_until_the_next_apply(db, monkeypatch):
    p = people(db)
    twin_row = _row(db, p['b'], {'full_name': 'M. Bianchi', 'title': 'CTO', 'email': 'mb@acme.verify.test'})
    third_row = _row(db, p['b'], {'full_name': 'Marco B.', 'title': 'CTO', 'email': 'mbi@acme.verify.test'})
    twin, third = _identity(db, twin_row), _identity(db, third_row)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')

    with monkeypatch.context() as m:
        m.setattr(evaluate, '_MERGED', 'SELECT 0 AS merged')
        for folded, into in ((p['marco'], twin), (twin, third)):
            conflicts.resolve(
                db, _raise_conflict(db, p['org'], p['pid'], folded, into), p['pid'], p['uid'], 'merge', into=into
            )
        before = transitions(db, seg)
        assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=0, members=2)
        assert transitions(db, seg) == before
    assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=1, members=2)
    assert transitions(db, seg)[-2:] == [(p['marco'], 'exit', 1), (third, 'merged', 1)]


@needs_db
def test_a_second_join_for_one_episode_is_refused_by_the_database(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg)
    insert = (
        'INSERT INTO segment_transitions (organization_id, segment_id, identity_id, kind, episode, segment_version)'
        ' VALUES (%s, %s, %s, %s, 1, 1)'
    )
    db.execute(insert, (p['org'], seg['id'], p['marco'], 'exit'))
    for kind in ('enter', 'baseline'):
        with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
            db.execute(insert, (p['org'], seg['id'], p['marco'], kind))


@needs_db
def test_excluded_rows_and_rows_of_a_deleted_list_do_not_count(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg)
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (p['marco_b'],))
    assert run(db, seg).exited == 1  # his only email is on an excluded row
    db.execute('UPDATE list_rows SET excluded = false WHERE id = %s', (p['marco_b'],))
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (p['a'],))
    assert run(db, seg) == Applied(joined=0, entered=0, exited=1, merged=0, members=0)  # list A held every title


def _count(db, p, raw) -> int:
    return preview(db, p['pid'], Rule('person', RuleGroup.model_validate(raw)))['count']


@needs_db
def test_a_failed_cell_is_missing_and_a_range_is_never_a_number(db):
    p = people(db)
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    db.execute(
        'UPDATE list_rows SET cells = cells || \'{"uses_hubspot": "yes", "employees": "51-200"}\','
        ' cell_meta = cell_meta || \'{"uses_hubspot": {"status": "failed"}}\' WHERE id = %s',
        (p['giulia'],),
    )
    missing = {'type': 'group', 'op': 'and', 'children': [{'type': 'presence', 'field': column, 'op': 'is_missing'}]}
    number = {
        'type': 'number',
        'field': {'kind': 'canonical', 'key': 'employees'},
        'op': 'between',
        'low': 0,
        'high': 1000000,
    }
    filled = {'type': 'presence', 'field': {'kind': 'canonical', 'key': 'employees'}, 'op': 'is_filled'}
    assert _count(db, p, missing) == 2  # Giulia's failed fill and Marco's empty cell
    assert _count(db, p, {'type': 'group', 'op': 'and', 'children': [number]}) == 0
    assert _count(db, p, {'type': 'group', 'op': 'and', 'children': [filled]}) == 1


@needs_db
def test_a_person_with_a_cell_under_a_held_charge_is_neither_joined_nor_exited(db):
    p = people(db)
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    rule = {
        'type': 'group',
        'op': 'and',
        'children': [{'type': 'text', 'field': column, 'op': 'eq', 'values': ['yes']}],
    }
    db.execute('UPDATE list_rows SET cells = cells || \'{"uses_hubspot": "yes"}\' WHERE id = %s', (p['giulia'],))
    seg = segment(db, p, rule)
    assert run(db, seg).members == 1
    paolo = _row(db, p['a'], {'full_name': 'Paolo Neri', 'email': 'paolo@acme.verify.test', 'uses_hubspot': 'yes'})
    reservation = hold(db, p['uid'])
    for row in (p['giulia'], paolo):
        db.execute(
            "INSERT INTO row_charges (hold_id, row_id, keys, credits, state) VALUES (%s, %s, '{uses_hubspot}', 1, 'held')",
            (reservation, row),
        )
    found = preview(db, p['pid'], Rule('person', RuleGroup.model_validate(rule)))
    assert (found['count'], found['pending']) == (0, 2)
    assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=0, members=1)
    assert why(db, seg, _identity(db, paolo))['pending']
    db.execute('SELECT release_reservation(%s)', (reservation,))
    assert run(db, seg) == Applied(joined=1, entered=1, exited=0, merged=0, members=2)


@needs_db
def test_a_frozen_segment_writes_nothing(db):
    p = people(db)
    column = {'kind': 'column', 'list_id': p['a'], 'key': 'uses_hubspot'}
    rule = {'type': 'group', 'op': 'and', 'children': [{'type': 'presence', 'field': column, 'op': 'is_missing'}]}
    seg = segment(db, p, rule)
    assert run(db, seg).members == 2
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'uses_hubspot'", (p['a'],))
    db.execute('UPDATE list_rows SET cells = cells || \'{"uses_hubspot": "yes"}\' WHERE id = %s', (p['giulia'],))
    before = live(db, seg)
    assert run(db, seg) == Applied(joined=0, entered=0, exited=0, merged=0, members=2)
    assert live(db, seg) == before


@needs_db
def test_another_projects_people_never_match(db):
    p = people(db)
    other = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Other client') RETURNING id",
        (p['org'], p['uid']),
    ).fetchone()['id']
    there = people(db, uid=p['uid'], pid=other)
    seg = segment(db, p, MARCO_RULE)
    assert preview(db, p['pid'], Rule('person', RuleGroup.model_validate(seg['rule'])))['count'] == 2
    run(db, seg)
    assert set(live(db, seg)) == {p['marco'], _identity(db, p['giulia'])}
    assert there['marco'] not in live(db, seg)


@needs_db
def test_the_preview_counts_rows_with_no_identity_yet(db):
    p = people(db)
    _row(db, p['a'], {'full_name': 'Nameless'})  # no email, LinkedIn URL or domain: never linked
    add_rows(db, p['b'], 'person', [{'full_name': 'Soon', 'email': 'soon@acme.verify.test'}])  # marked, not linked
    db.execute("UPDATE list_rows SET excluded = true WHERE cells->>'full_name' = 'Soon'")
    add_rows(db, p['b'], 'person', [{'full_name': 'Later', 'email': 'later@acme.verify.test'}])
    found = preview(db, p['pid'], Rule('person', RuleGroup.model_validate(MARCO_RULE)))
    assert (found['count'], found['unidentified_rows'], found['linking_rows']) == (2, 1, 1)
    assert [s['identity_id'] for s in found['sample']] == sorted(
        uuid.UUID(_identity(db, r)) for r in (p['marco_a'], p['giulia'])
    )


@needs_db
def test_two_applies_at_once_take_turns_and_agree(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    conninfo = conninfo_of(db)
    first = psycopg.connect(conninfo, row_factory=dict_row)
    lock_project(first, p['pid'])
    assert apply(first, seg, 'enter').applied.joined == 2  # not committed yet
    second: list[Applied] = []

    def other():
        with psycopg.connect(conninfo, row_factory=dict_row) as c:
            lock_project(c, p['pid'])
            second.append(apply(c, seg, 'enter').applied)

    thread = threading.Thread(target=other)
    thread.start()
    deadline = time.monotonic() + 10
    while not db.execute(
        "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event = 'advisory'"
    ).fetchone():
        assert thread.is_alive() and time.monotonic() < deadline, 'the second apply never waited on the project lock'
        time.sleep(0.01)
    first.commit()
    first.close()
    committed = versions(db, seg)
    thread.join()
    assert second == [Applied(joined=0, entered=0, exited=0, merged=0, members=2)]
    assert versions(db, seg) == committed
    assert {e for _, e in live(db, seg).values()} == {1}


@needs_db
def test_the_app_cannot_rewrite_history_but_deleting_a_row_or_a_project_still_reaches_it(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg)

    def as_app(statement, params=()):
        with db.transaction():
            db.execute('SET LOCAL ROLE zenbree_app')
            db.execute('SELECT zb.enter_org(%s)', (p['org'],))
            return db.execute(statement, params)

    def as_system(statement):
        with db.transaction():
            db.execute('SET LOCAL ROLE zenbree_system')
            db.execute(statement)

    for statement in ("UPDATE segment_transitions SET kind = 'exit'", 'DELETE FROM segment_transitions'):
        for writer in (as_app, as_system):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                writer(statement)
    as_app('DELETE FROM list_rows WHERE id = %s', (p['marco_a'],))
    shown = 'SELECT list_row_id FROM segment_transitions WHERE identity_id = %s'
    assert [r['list_row_id'] for r in db.execute(shown, (p['marco'],))] == [None]
    as_app('DELETE FROM projects WHERE id = %s', (p['pid'],))
    assert db.execute('SELECT count(*) AS n FROM segment_transitions').fetchone()['n'] == 0


def test_the_judge_refuses_one_identity_of_a_staged_set():
    compiled = compile_rule(Rule('person', RuleGroup.model_validate(MARCO_RULE)))
    with pytest.raises(ValueError, match='identity_id'):
        judged(compiled, uuid.uuid4(), identity_id=uuid.uuid4(), staged=[uuid.uuid4()])
