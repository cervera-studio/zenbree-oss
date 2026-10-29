"""The campaign report's split by copy variant (#934): each count of the row is the sum of its groups."""

from hypothesis import given
from hypothesis import strategies as st

from app.reports.variant_split import CHANNELS, PEOPLE, split

count = st.integers(min_value=0, max_value=10_000)
row = st.fixed_dictionaries(
    {'has_target': st.booleans(), 'variant': st.sampled_from(['a', 'b', None])},
    optional=dict.fromkeys((*PEOPLE, *CHANNELS), count),
)


@given(rows=st.lists(row, max_size=30))
def test_each_count_summed_over_the_groups_is_the_total_of_the_rows(rows):
    groups = split(rows)
    for key in PEOPLE:
        assert sum(g[key] for g in groups) == sum(r.get(key, 0) for r in rows)
    for key in CHANNELS:
        assert sum(g['sent'][key] for g in groups) == sum(r.get(key, 0) for r in rows)
    names = [g['group'] for g in groups]
    assert names[:2] == ['a', 'b'] and len(set(names)) == len(names)
    assert all(g['bounced'] is None for g in groups)


def test_a_person_with_no_variant_is_outside_and_an_event_of_nobody_is_unknown():
    groups = split([
        {'has_target': True, 'variant': 'b', 'contacted': 2},
        {'has_target': True, 'variant': None, 'contacted': 3},
        {'has_target': False, 'variant': None, 'contacted': 5},
    ])  # fmt: skip
    assert {g['group']: g['contacted'] for g in groups} == {'a': 0, 'b': 2, 'outside': 3, 'unknown': 5}


def test_a_test_with_no_one_outside_and_nothing_unmatched_lists_only_a_and_b():
    groups = split([{'has_target': True, 'variant': 'a', 'enrolled': 4}])
    assert [(g['group'], g['enrolled']) for g in groups] == [('a', 4), ('b', 0)]
