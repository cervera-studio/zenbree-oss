from __future__ import annotations

from collections import defaultdict

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb

from app.segments.entries import entries_after
from app.segments.evaluate import apply, lock_project, preview
from app.segments.rules import Rule, RuleGroup
from tests.conftest import member, project
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_home_overview import _list
from tests.test_segments_sql import MARCO_RULE, people, run, segment

FIT = {'type': 'group', 'op': 'and', 'children': [{'type': 'fit', 'op': 'gte', 'low': 80}]}
RULES = [
    FIT,
    {'type': 'group', 'op': 'and', 'children': [{'type': 'fit', 'op': 'gte', 'low': 60}]},
    {'type': 'group', 'op': 'and', 'children': [{'type': 'fit', 'op': 'lte', 'high': 70}]},
]
PEOPLE = 4

step = st.one_of(
    st.tuples(st.just('fit'), st.integers(0, PEOPLE - 1), st.sampled_from([50, 85, None])),
    st.tuples(st.just('merge'), st.integers(0, PEOPLE - 1), st.integers(0, PEOPLE - 1)),
    st.tuples(st.just('apply'), st.sampled_from(['enter', 'baseline'])),
    st.tuples(st.just('edit'), st.integers(0, len(RULES) - 1), st.sampled_from(['enter', 'baseline'])),
)


def _people(db, org, pid, lid) -> list[dict]:
    found = []
    for i in range(PEOPLE):
        identity = db.execute(
            "INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (%s, %s, 'person') RETURNING id",
            (org, pid),
        ).fetchone()['id']
        row = db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id',
            (lid, Jsonb({'full_name': f'P{i}'}), f'k{i}'),
        ).fetchone()['id']
        db.execute(
            'INSERT INTO audience_row_links (list_row_id, organization_id, identity_id) VALUES (%s, %s, %s)',
            (row, org, identity),
        )
        found.append({'identity': identity, 'row': row})
    return found


def _ledger(db, segment_id) -> list[dict]:
    return db.execute(
        'SELECT seq, identity_id, kind, episode FROM segment_transitions WHERE segment_id = %s ORDER BY seq',
        (segment_id,),
    ).fetchall()


def _live(db, segment_id) -> list[dict]:
    return db.execute(
        'SELECT identity_id FROM segment_members WHERE segment_id = %s AND exited_at IS NULL', (segment_id,)
    ).fetchall()


def _snapshot(db, segment_id) -> list[dict]:
    return db.execute(
        'SELECT identity_id, xmin::text, xmax::text, ctid::text FROM segment_members WHERE segment_id = %s'
        ' ORDER BY identity_id',
        (segment_id,),
    ).fetchall()


@needs_db
def test_a_repeated_apply_records_nothing_and_each_join_takes_a_new_episode(db):
    uid = _user(db)
    org, pid = member(db, uid).org_id, project(db, uid)
    lid = _list(db, uid, 'A')

    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(st.lists(step, max_size=14))
    def converge(steps):
        with db.transaction(force_rollback=True):
            people = _people(db, org, pid, lid)
            standing = list(range(PEOPLE))
            seg = db.execute(
                'INSERT INTO segments (organization_id, project_id, name, entity, rule, published_at)'
                " VALUES (%s, %s, 'S', 'person', %s, now()) RETURNING *",
                (org, pid, Jsonb(FIT)),
            ).fetchone()
            lock_project(db, pid)
            for s in [*steps, ('apply', 'enter')]:
                if s[0] == 'fit':
                    db.execute('UPDATE list_rows SET relevance_score = %s WHERE id = %s', (s[2], people[s[1]]['row']))
                elif s[0] == 'merge':
                    winner, loser = standing[s[1]], standing[s[2]]
                    if winner == loser:
                        continue
                    db.execute(
                        'UPDATE audience_row_links SET identity_id = %s WHERE identity_id = %s',
                        (people[winner]['identity'], people[loser]['identity']),
                    )
                    db.execute(
                        'UPDATE audience_identities SET merged_into = %s WHERE id = %s',
                        (people[winner]['identity'], people[loser]['identity']),
                    )
                    standing = [winner if w == loser else w for w in standing]
                else:
                    if s[0] == 'edit':
                        rule = Rule('person', RuleGroup.model_validate(RULES[s[1]]))
                        asked = preview(db, pid, rule, was=seg)
                        edited = db.execute(
                            'UPDATE segments SET rule = %s, version = version + 1 WHERE id = %s RETURNING *',
                            (Jsonb(RULES[s[1]]), seg['id']),
                        ).fetchone()
                        done = apply(db, edited, s[2], was=seg)
                        assert (asked['joining'], asked['leaving']) == (done.joining, done.leaving), (
                            'the preview agrees'
                        )
                        seg = edited
                    else:
                        apply(db, seg, s[1])
                    recorded, members = _ledger(db, seg['id']), _snapshot(db, seg['id'])
                    again = apply(db, seg, s[-1]).applied
                    assert (again.joined, again.exited, again.merged) == (0, 0, 0)
                    assert _ledger(db, seg['id']) == recorded
                    assert _snapshot(db, seg['id']) == members

            episodes: dict = defaultdict(list)
            last: dict = {}
            for t in _ledger(db, seg['id']):
                if t['kind'] in ('enter', 'baseline'):
                    assert all(e < t['episode'] for e in episodes[t['identity_id']]), 'an episode was reused'
                episodes[t['identity_id']].append(t['episode'])
                last[t['identity_id']] = t['kind']
            in_ledger = {i for i, kind in last.items() if kind != 'exit'}
            assert in_ledger == {m['identity_id'] for m in _live(db, seg['id'])}, 'the ledger replays to the members'

    converge()


@needs_db
def test_entries_after_a_cursor_are_the_enter_records_in_order_with_their_payload(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg, 'baseline')
    assert entries_after(db, seg['id'], 0, 10) == []

    paolo_row = db.execute(
        'INSERT INTO list_rows (list_id, cells, dedupe_key, relevance_score) VALUES (%s, %s, %s, 95) RETURNING id',
        (p['a'], Jsonb({'full_name': 'Paolo', 'title': 'CEO', 'email': 'paolo@acme.verify.test'}), 'paolo'),
    ).fetchone()['id']
    paolo = db.execute(
        "INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (%s, %s, 'person') RETURNING id",
        (p['org'], p['pid']),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO audience_row_links (list_row_id, organization_id, identity_id) VALUES (%s, %s, %s)',
        (paolo_row, p['org'], paolo),
    )
    db.execute('UPDATE list_rows SET relevance_score = 50 WHERE id = %s', (p['marco_a'],))
    run(db, seg)
    db.execute('UPDATE list_rows SET relevance_score = 85 WHERE id = %s', (p['marco_a'],))
    db.execute('UPDATE segments SET version = 7 WHERE id = %s', (seg['id'],))
    run(db, {**seg, 'version': 7})

    entries = entries_after(db, seg['id'], 0, 10)
    assert [(str(e.identity_id), e.episode, e.segment_version) for e in entries] == [
        (str(paolo), 1, 1),
        (p['marco'], 2, 7),
    ]
    first = entries[0]
    assert (first.segment_id, str(first.project_id), first.entity, first.list_row_id) == (
        seg['id'],
        str(p['pid']),
        'person',
        paolo_row,
    )
    assert entries[0].seq < entries[1].seq
    assert entries_after(db, seg['id'], entries[0].seq, 10) == entries[1:]
    assert entries_after(db, seg['id'], 0, 1) == entries[:1]
    assert entries_after(db, seg['id'], entries[1].seq, 10) == []


@needs_db
def test_a_deleted_segment_delivers_no_further_entries(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    run(db, seg)
    assert len(entries_after(db, seg['id'], 0, 10)) == 2
    db.execute('UPDATE segments SET deleted_at = now() WHERE id = %s', (seg['id'],))
    assert entries_after(db, seg['id'], 0, 10) == []
