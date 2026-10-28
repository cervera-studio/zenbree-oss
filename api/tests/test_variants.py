"""Which copy each person of an A/B test gets (#934)."""

from hypothesis import example, given
from hypothesis import strategies as st

from app.outreach.variants import Split, assign, copy_to_fill, every_copy, has_test, sequence

CAMPAIGN = '7b1c2a40-0000-4000-8000-000000000001'
ids = st.lists(st.uuids().map(str), unique=True, max_size=60)
percents = st.integers(min_value=0, max_value=100)

STEPS = [
    {'step': 1, 'channel': 'email', 'subject': 'Quick idea', 'body': 'Hi {{firstName}}', 'send_after_days': 0,
     'threaded': False, 'personalised': 'Hi', 'b': {'subject': '{{firstName}}, 2 minutes?', 'body': 'Hey {{role}}'}},
    {'step': 2, 'channel': 'email', 'subject': '', 'body': 'Following up', 'send_after_days': 3, 'threaded': True},
]  # fmt: skip

copy = st.fixed_dictionaries({'subject': st.text(max_size=20), 'body': st.text(min_size=1, max_size=20)})
step = st.fixed_dictionaries(
    {
        'step': st.integers(1, 12),
        'channel': st.sampled_from(['email', 'linkedin_connect', 'linkedin_message', 'hubspot_task']),
        'subject': st.text(max_size=20),
        'body': st.text(max_size=20),
        'send_after_days': st.integers(0, 30),
        'threaded': st.booleans(),
        'personalised': st.text(max_size=5),
    },
    optional={'b': copy},
)


@given(batches=st.lists(ids, max_size=8), b_percent=percents)
@example(batches=[[f'p{n}' for n in range(30)]], b_percent=30)
@example(batches=[[f'p{n}'] for n in range(29)], b_percent=50)
def test_from_an_empty_split_any_batches_stay_within_one_person_of_the_percent(batches, b_percent):
    split, seen = Split(0, 0), set()
    for batch in batches:
        fresh = [t for t in batch if t not in seen]
        seen |= set(fresh)
        chosen = assign(split, fresh, b_percent, CAMPAIGN)
        assert chosen.keys() == set(fresh)  # each joiner gets exactly one variant
        split = Split(split.a + list(chosen.values()).count('a'), split.b + list(chosen.values()).count('b'))
        assert abs(100 * split.b - b_percent * (split.a + split.b)) < 100


@given(joining=ids, b_percent=percents, data=st.data())
def test_the_order_people_are_listed_in_never_chooses_their_copy(joining, b_percent, data):
    shuffled = data.draw(st.permutations(joining))
    assert assign(Split(3, 1), shuffled, b_percent, CAMPAIGN) == assign(Split(3, 1), joining, b_percent, CAMPAIGN)


@given(joining=ids, existing=st.tuples(st.integers(0, 50), st.integers(0, 50)))
def test_zero_percent_is_all_a_and_a_hundred_is_all_b(joining, existing):
    assert set(assign(Split(*existing), joining, 0, CAMPAIGN).values()) <= {'a'}
    assert set(assign(Split(*existing), joining, 100, CAMPAIGN).values()) <= {'b'}


def _shape(steps: list[dict]) -> list[dict]:
    return [{k: s[k] for k in ('step', 'channel', 'send_after_days', 'threaded')} for s in steps]


@given(steps=st.lists(step, max_size=12))
def test_both_variants_keep_the_shared_shape_and_none_is_a(steps):
    a, b = sequence(steps, 'a'), sequence(steps, 'b')
    assert _shape(a) == _shape(b) == _shape(steps)
    assert sequence(steps, None) == a
    assert all('b' not in s for s in a + b)
    assert [(s['subject'], s['body']) for s in a] == [(s['subject'], s['body']) for s in steps]


def test_b_gets_its_own_copy_only_where_a_step_has_one():
    b = sequence(STEPS, 'b')
    assert [(s['subject'], s['body']) for s in b] == [
        ('{{firstName}}, 2 minutes?', 'Hey {{role}}'),
        ('', 'Following up'),
    ]
    assert b[0]['personalised'] == '' and sequence(STEPS, 'a')[0]['personalised'] == 'Hi'
    assert has_test(STEPS) and not has_test(sequence(STEPS, 'b'))


def tokens(steps):
    return ' '.join(f'{s["subject"]} {s["body"]}' for s in steps)


def test_a_joiner_needs_both_copies_while_the_test_allocates_and_anyone_else_only_their_own():
    assert '{{role}}' in tokens(copy_to_fill(STEPS, None, joins_test=True))
    assert '{{role}}' not in tokens(copy_to_fill(STEPS, None, joins_test=False))
    assert '{{role}}' not in tokens(copy_to_fill(STEPS, 'a', joins_test=True))
    assert '{{role}}' in tokens(copy_to_fill(STEPS, 'b', joins_test=False))
    assert copy_to_fill(STEPS, None, joins_test=True) == every_copy(STEPS)
