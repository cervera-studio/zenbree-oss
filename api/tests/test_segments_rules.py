"""Segment rules (#938): the bounds and paths of ``rules.parse``, and the SQL judge against the Python oracle."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb

from app.lists.rows import CANONICAL
from app.segments import rules
from app.segments.evaluate import apply, lock_project, preview
from app.segments.rules import (
    CanonicalField,
    ColumnField,
    FitCondition,
    InListCondition,
    NumberCondition,
    PresenceCondition,
    Rule,
    RuleError,
    RuleGroup,
    TextCondition,
)
from tests import segments_oracle
from tests.conftest import member, project
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_home_overview import _list
from tests.web_fixtures import web_fixture

TITLE = {'type': 'text', 'field': {'kind': 'canonical', 'key': 'title'}, 'op': 'contains', 'values': ['ceo']}


def _group(*children, op='and') -> dict:
    return {'type': 'group', 'op': op, 'children': list(children)}


def _nested(depth: int) -> dict:
    node = _group(TITLE)
    for _ in range(depth - 1):
        node = _group(node)
    return node


def _reason(raw, entity='person') -> tuple[str, str]:
    with pytest.raises(RuleError) as e:
        rules.parse(None, uuid.uuid4(), entity, raw)
    return e.value.path, e.value.reason


def test_a_rule_three_groups_deep_is_kept_and_a_fourth_is_refused_at_its_path():
    assert rules.parse(None, uuid.uuid4(), 'person', _nested(3)).root.children[0].children[0].type == 'group'
    assert _reason(_nested(4)) == ('/children/0/children/0/children/0', 'too_deep')


def test_twenty_conditions_are_kept_and_the_twenty_first_is_refused():
    assert len(rules.parse(None, uuid.uuid4(), 'person', _group(*[TITLE] * 20)).root.children) == 20
    assert _reason(_group(_group(*[TITLE] * 10), _group(*[TITLE] * 11))) == (
        '/children/1/children/10',
        'too_many_conditions',
    )


def test_a_shape_error_names_the_json_path_of_the_condition():
    bad_list = {'type': 'in_list', 'list_id': 'nope'}
    assert _reason(_group(TITLE, bad_list)) == ('/children/1/list_id', 'uuid_parsing')
    many = {**TITLE, 'op': 'in', 'values': [str(i) for i in range(51)]}
    assert _reason(_group(many)) == ('/children/0/values', 'too_long')
    assert _reason(_group({**TITLE, 'values': ['a', 'b']}))[0] == '/children/0'
    lt_low = {'type': 'number', 'field': {'kind': 'canonical', 'key': 'employees'}, 'op': 'lt', 'low': 3}
    assert _reason(_group(lt_low))[0] == '/children/0'
    assert _reason(_group({'type': 'fit', 'op': 'gte', 'low': 101}))[0] == '/children/0/low'
    assert _reason({'type': 'group', 'op': 'and', 'children': []}) == ('/children', 'too_short')


def test_a_canonical_key_must_be_one_of_the_entity_and_never_vendor_plumbing():
    assert _reason(_group({**TITLE, 'field': {'kind': 'canonical', 'key': 'domain'}})) == (
        '/children/0/field/key',
        'unknown_field',
    )
    assert _reason(_group({**TITLE, 'field': {'kind': 'canonical', 'key': 'source'}}))[1] == 'unknown_field'
    assert rules.parse(
        None, uuid.uuid4(), 'company', _group({**TITLE, 'field': {'kind': 'canonical', 'key': 'domain'}})
    )
    assert set(rules.canonical_keys('person')) < set(CANONICAL['person'])


@pytest.mark.web_fixture
def test_the_web_editor_reads_the_bounds_and_fields_parse_holds():
    values = TextCondition.model_json_schema()['properties']['values']
    fit = FitCondition.model_json_schema()['properties']['low']['anyOf'][0]
    group = RuleGroup.model_json_schema()['$defs']['RuleGroup']['properties']['children']
    web_fixture(
        'segment-rule-bounds',
        {
            'canonical': {entity: rules.canonical_keys(entity) for entity in ('person', 'company')},
            'max_depth': rules.MAX_DEPTH,
            'max_conditions': rules.MAX_LEAVES,
            'max_children': group['maxItems'],
            'max_values': values['maxItems'],
            'max_value_length': values['items']['maxLength'],
            'fit': {'min': fit['minimum'], 'max': fit['maximum']},
        },
    )


CONDITION = st.sampled_from([TITLE, {'type': 'fit', 'op': 'is_filled'}])


def _tree(depth: int):
    child = CONDITION if depth >= 5 else st.one_of(CONDITION, st.deferred(lambda: _tree(depth + 1)))
    return st.builds(lambda kids: _group(*kids), st.lists(child, min_size=1, max_size=8))


def _depth_and_leaves(node: dict, depth: int = 1) -> tuple[int, int]:
    if node['type'] != 'group':
        return depth - 1, 1
    found = [_depth_and_leaves(c, depth + 1) for c in node['children']]
    return max([depth] + [d for d, _ in found]), sum(n for _, n in found)


@given(_tree(1))
def test_parse_keeps_exactly_the_rules_within_the_bounds(raw):
    depth, leaves = _depth_and_leaves(raw)
    within = depth <= rules.MAX_DEPTH and leaves <= rules.MAX_LEAVES
    try:
        rules.parse(None, uuid.uuid4(), 'person', raw)
    except RuleError:
        assert not within
    else:
        assert within


POOL = ['ceo', 'CEO ', 'Co-founder & CEO', 'cto', 'yes', 'no', '50', ' 12', '7.5', '51-200', '1e3', '-3', 'a%b', 'x_y']
SCORES = [None, 0, 60, 79.5, 80, 85, 100]
BOUNDS = [-3, 0, 7.5, 12, 50, 200]
KEYS = ['title', 'email', 'employees', 'hubspot']


def _people_strategy(lists: tuple[str, str]):
    row = st.fixed_dictionaries(
        {
            'identity': st.sampled_from([None, 0, 1, 2]),
            'list_id': st.sampled_from(lists),
            'cells': st.dictionaries(st.sampled_from(KEYS), st.sampled_from([*POOL, '']), max_size=4),
            'failed': st.sets(st.sampled_from(KEYS), max_size=2),
            'relevance_score': st.sampled_from(SCORES),
            'excluded': st.booleans(),
        }
    )
    return st.lists(row, max_size=7)


def _rule_strategy(lists: tuple[str, str]):
    a, b = (uuid.UUID(x) for x in lists)
    fields = st.sampled_from(
        [
            CanonicalField(key='title'),
            CanonicalField(key='email'),
            CanonicalField(key='employees'),
            ColumnField(list_id=a, key='hubspot'),
            ColumnField(list_id=b, key='title'),
        ]
    )
    values = st.sampled_from([v for v in POOL if v.strip()] + ['e', '%', '_'])
    text = st.one_of(
        st.builds(
            TextCondition,
            field=fields,
            op=st.sampled_from(['eq', 'neq', 'contains', 'not_contains']),
            values=st.tuples(values),
        ),
        st.builds(
            TextCondition,
            field=fields,
            op=st.sampled_from(['in', 'not_in']),
            values=st.lists(values, min_size=1, max_size=3).map(tuple),
        ),
    )
    pair = st.tuples(st.sampled_from(BOUNDS), st.sampled_from(BOUNDS)).map(sorted)
    number = st.one_of(
        st.builds(NumberCondition, field=fields, op=st.sampled_from(['gt', 'gte']), low=st.sampled_from(BOUNDS)),
        st.builds(NumberCondition, field=fields, op=st.sampled_from(['lt', 'lte']), high=st.sampled_from(BOUNDS)),
        pair.map(lambda p: NumberCondition(field=CanonicalField(key='employees'), op='between', low=p[0], high=p[1])),
    )
    scores = [s for s in SCORES if s is not None]
    fit = st.one_of(
        st.builds(FitCondition, op=st.just('gte'), low=st.sampled_from(scores)),
        st.builds(FitCondition, op=st.just('lte'), high=st.sampled_from(scores)),
        st.builds(FitCondition, op=st.sampled_from(['is_missing', 'is_filled'])),
    )
    presence = st.builds(PresenceCondition, field=fields, op=st.sampled_from(['is_missing', 'is_filled']))
    in_list = st.builds(
        InListCondition, list_id=st.sampled_from([a, b]), op=st.sampled_from(['in_list', 'not_in_list'])
    )
    condition = st.one_of(text, number, fit, presence, in_list)

    def group(depth: int):
        child = condition if depth >= rules.MAX_DEPTH else st.one_of(condition, st.deferred(lambda: group(depth + 1)))
        return st.builds(
            RuleGroup, op=st.sampled_from(['and', 'or']), children=st.lists(child, min_size=1, max_size=4).map(tuple)
        )

    return group(1)


def _setup(db):
    uid = _user(db)
    a, b = _list(db, uid, 'A'), _list(db, uid, 'B')
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, 'hubspot', 'HubSpot', 'ai', 1),"
        " (%s, 'title', 'Title', 'static', 1)",
        (a, b),
    )
    return member(db, uid).org_id, project(db, uid), (a, b)


def _write(db, org, pid, people) -> list[dict]:
    idents = [
        db.execute(
            "INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (%s, %s, 'person') RETURNING id",
            (org, pid),
        ).fetchone()['id']
        for _ in range(3)
    ]
    written = []
    for i, p in enumerate(people):
        meta = {k: {'status': 'failed'} for k in p['failed']}
        rid = db.execute(
            'INSERT INTO list_rows (list_id, cells, cell_meta, relevance_score, excluded, dedupe_key)'
            ' VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
            (p['list_id'], Jsonb(p['cells']), Jsonb(meta), p['relevance_score'], p['excluded'], f'k{i}'),
        ).fetchone()['id']
        identity = None if p['identity'] is None else idents[p['identity']]
        if identity:
            db.execute(
                'INSERT INTO audience_row_links (list_row_id, organization_id, identity_id) VALUES (%s, %s, %s)',
                (rid, org, identity),
            )
        written.append({**p, 'id': rid, 'identity': identity, 'list_id': uuid.UUID(p['list_id']), 'cell_meta': meta})
    return written


@needs_db
def test_the_sql_judge_agrees_with_the_oracle_and_a_second_apply_writes_nothing(db):
    org, pid, lists = _setup(db)

    @settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(_rule_strategy(lists), _people_strategy(lists))
    def agree(root, people):
        with db.transaction(force_rollback=True):
            rows = _write(db, org, pid, people)
            expected = segments_oracle.members(root, rows)
            segment = db.execute(
                'INSERT INTO segments (organization_id, project_id, name, entity, rule, published_at)'
                " VALUES (%s, %s, 'S', 'person', %s, now()) RETURNING *",
                (org, pid, Jsonb(root.model_dump(mode='json'))),
            ).fetchone()
            assert preview(db, pid, Rule('person', root))['count'] == len(expected)
            lock_project(db, pid)
            assert apply(db, segment, 'enter').applied.joined == len(expected)
            live = 'SELECT identity_id, list_row_id, ctid::text FROM segment_members WHERE exited_at IS NULL AND segment_id = %s'
            first = db.execute(live, (segment['id'],)).fetchall()
            assert {m['identity_id']: m['list_row_id'] for m in first} == expected
            again = apply(db, segment, 'enter').applied
            assert (again.joined, again.exited, again.members) == (0, 0, len(expected))
            assert db.execute(live, (segment['id'],)).fetchall() == first  # no row rewritten: same ctid

    agree()
