"""#1165: Gmail redelivers a push and the task retries, so any number of deliveries of one reply, mixed with other
mail, end as one delivery would."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_bounces_sql import _setup
from tests.test_mailbox_replies_sql import Gmail, _mail, _stored, _target
from tests.test_mailbox_replies_sql import quiet as quiet

pytestmark = needs_db


@settings(
    max_examples=15,
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(deliveries=st.integers(1, 4), others=st.integers(0, 3))
def test_a_reply_delivered_any_number_of_times_is_recorded_once(db, monkeypatch, http, deliveries, others):
    s = _setup(db)
    mail = {'reply': _mail()} | {
        f'o{i}': _mail('anna@client.test', refs='', mid=f'<o{i}@client.test>') for i in range(others)
    }
    gmail = Gmail(monkeypatch, http, s, mail)

    for _ in range(deliveries):
        gmail.push(db)

    assert [m['kind'] for m in _stored(db, s)] == ['reply']
    assert _target(db, s) == 'replied'
    assert set(gmail.fetched) == {'reply'}
