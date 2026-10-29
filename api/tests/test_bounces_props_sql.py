"""Idempotency of a bounce read from the mailbox (#747): Gmail redelivers pushes and the task retries, so any sequence
of reports about one send, repeated in any order, ends as its first hard report alone would."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_agent import needs_db
from tests.test_bounces_sql import ADA, _bounce, _refunds, _setup
from tests.test_bounces_sql import db as db
from tests.test_bounces_sql import told as told

pytestmark = needs_db

reports = st.lists(st.tuples(st.sampled_from(['hard', 'soft']), st.booleans()), min_size=1, max_size=6)


@settings(
    max_examples=25,
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(sequence=reports)
def test_any_sequence_of_reports_refunds_and_excludes_at_most_once(db, told, sequence):
    s = _setup(db)  # its own user, organization and campaign: examples never see each other
    for kind, reverified in sequence:
        _bounce(s, kind=kind, reverified_invalid=reverified)

    first_hard = next((reverified for kind, reverified in sequence if kind == 'hard'), None)
    send = db.execute(
        'SELECT bounce_kind FROM outreach_campaign_sends WHERE campaign_id = %s', (s['campaign'],)
    ).fetchone()
    assert send['bounce_kind'] == ('hard' if first_hard is not None else 'soft')
    assert _refunds(db, s) == ([('email_bounce', 1)] if first_hard else [])
    target = db.execute(
        'SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s', (s['campaign'],)
    ).fetchone()
    assert target['status'] == ('excluded' if first_hard is not None else 'active')
    rules = db.execute(
        "SELECT count(*) AS n FROM exclusions WHERE project_id = %s AND kind = 'email' AND value = %s",
        (s['project'], ADA),
    ).fetchone()['n']
    assert rules == (1 if first_hard is not None else 0)
