import pytest

from app.outreach import send_states
from app.outreach.send_states import ALLOWED


def test_no_state_moves_to_itself():
    """A claim guards on ``sources('sending')``: a self-loop there would claim an in-flight send a second time."""
    assert [s for s, targets in ALLOWED.items() if s in targets] == []


def test_every_target_is_a_state():
    assert set().union(*ALLOWED.values()) <= ALLOWED.keys()


def test_sources_reads_the_table_backwards():
    assert send_states.sources('sending') == {'approved', 'failed'}
    assert send_states.sources('awaiting_approval') == {'skipped'}


def test_guard_refuses_a_source_the_table_forbids():
    with pytest.raises(ValueError, match='sent'):
        send_states.guard('approved', frm=('sending', 'sent'))


def test_guard_refuses_an_unknown_target():
    with pytest.raises(ValueError, match='queued'):
        send_states.guard('queued')
