"""Which ready account sends which queued email this beat (#935)."""

from datetime import UTC, datetime

from app.outreach.allocation import assign, predict


def _rows(*ids: str) -> list[dict]:
    return [{'id': i} for i in ids]


def test_accounts_on_one_backlog_each_send_a_different_row():
    backlog = _rows('r1', 'r2', 'r3', 'r4')
    picked = assign({'a': backlog, 'b': backlog, 'c': backlog}, {'a': 5, 'b': 5, 'c': 5})
    assert sorted(r['id'] for r in picked.values()) == ['r1', 'r2', 'r3']


def test_the_account_with_fewer_choices_picks_first():
    # b can send only its pinned r1; a can send r1 or a new r2. b first, so both send.
    picked = assign({'a': _rows('r1', 'r2'), 'b': _rows('r1')}, {'a': 9, 'b': 1})
    assert {k: r['id'] for k, r in picked.items()} == {'a': 'r2', 'b': 'r1'}


def test_a_tie_goes_to_the_account_with_more_cap_left():
    picked = assign({'a': _rows('r1'), 'b': _rows('r1')}, {'a': 1, 'b': 7})
    assert {k: r['id'] for k, r in picked.items()} == {'b': 'r1'}


def test_an_account_whose_rows_are_all_taken_sends_nothing():
    assert assign({'a': _rows('r1'), 'b': _rows('r1')}, {'a': 3, 'b': 3}).keys() == {'a'}
    assert assign({}, {}) == {}


def _target(tid: str, pin: str | None = None, *, pinned: bool = False) -> dict:
    return {'id': tid, 'sender_id': pin, 'sender_pinned_at': datetime(2026, 10, 1, tzinfo=UTC) if pinned else None}


def test_a_conversation_is_predicted_on_its_own_account_even_one_that_cannot_send():
    assert predict(_target('t1', 'held', pinned=True), ['a', 'b']) == 'held'
    assert predict(_target('t1', None, pinned=True), ['a', 'b']) is None


def test_a_new_conversation_is_predicted_on_a_ready_account_the_same_way_every_time():
    picks = {predict(_target(f't{n}'), ['b', 'a']) for n in range(40)}
    assert picks == {'a', 'b'}
    assert predict(_target('t7'), ['a', 'b']) == predict(_target('t7'), ['b', 'a'])
    assert predict(_target('t7'), []) is None
