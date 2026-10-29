"""The model reads a first-list chat with its whole brief in place of the short first message."""

from hypothesis import given
from hypothesis import strategies as st

from app.agent.turn_input import with_brief

MESSAGE = st.fixed_dictionaries(
    {'role': st.sampled_from(['user', 'assistant']), 'content': st.text(max_size=20), 'question': st.none()}
)


@given(st.lists(MESSAGE, max_size=6), st.one_of(st.none(), st.text(max_size=20)))
def test_the_brief_replaces_only_the_first_user_message(msgs, brief):
    out = with_brief(msgs, brief)
    assert len(out) == len(msgs)
    users = [i for i, m in enumerate(msgs) if m['role'] == 'user']
    for i, (before, after) in enumerate(zip(msgs, out, strict=True)):
        if brief and users and i == users[0]:
            assert after == {**before, 'content': brief}
        else:
            assert after == before
