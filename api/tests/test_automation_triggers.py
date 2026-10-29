"""The trigger of an automation (#1030) and the columns that store it."""

from hypothesis import given
from hypothesis import strategies as st

from app.automations.triggers import ManualTrigger, ScheduleTrigger, SignalTrigger, WebhookTrigger, columns, of_row

_TRIGGERS = st.one_of(
    st.just(ManualTrigger()),
    st.builds(ScheduleTrigger, kind=st.just('schedule'), cadence=st.sampled_from(['daily', 'weekly'])),
    st.just(WebhookTrigger(kind='webhook')),
    st.builds(
        SignalTrigger,
        kind=st.just('signal'),
        watchlist_id=st.one_of(st.none(), st.uuids()),
        min_score=st.floats(min_value=0, max_value=10000, exclude_min=True),
    ),
)


@given(_TRIGGERS)
def test_a_stored_trigger_reads_back_as_the_trigger_that_was_saved(trigger):
    assert of_row(columns(trigger)) == trigger


@given(_TRIGGERS)
def test_a_trigger_stores_no_column_of_another_kind(trigger):
    stored = columns(trigger)
    assert (stored['cadence'] is None) == (trigger.kind != 'schedule')
    assert (stored['signal_min_score'] is None) == (trigger.kind != 'signal')
    assert trigger.kind == 'signal' or stored['watchlist_id'] is None
