import psycopg
import pytest

from app.integrations.instantly_sync import mapping
from tests.test_instantly_events_sql import CAMPAIGN, DB_URL, NOW, db, facts, hook, record, seed

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: Instantly store SQL test NOT run')
fixtures = (db,)


def status(code=-1):
    return mapping.from_lead({'email': 'ana@acme.test', 'status': code}, CAMPAIGN, now=NOW)


def test_the_body_is_voted_on_and_never_stored(db):
    b = seed(db)
    record(db, b, hook('reply_received', reply_subject='Re: Ciao', reply_text='Please remove me, ana@acme.test'))
    [row] = facts(db, b)
    assert row['our_class'] == 'opt_out' and row['reply_nature'] == 'unclassified'
    assert 'remove me' not in str(row)


def test_two_sources_of_one_reply_keep_the_safer_vote(db):
    b = seed(db)
    record(db, b, hook('reply_received', reply_subject='Out of office', reply_text='Back on Monday'))
    record(db, b, mapping.from_email(_api_reply('em-1', 'Re: Ciao', 'Please remove me'), now=NOW))
    [row] = facts(db, b)
    assert (row['reply_nature'], row['our_class']) == ('auto', 'opt_out')


def _api_reply(ref, subject, text) -> dict:
    return {
        'id': ref,
        'ue_type': 2,
        'lead': 'ana@acme.test',
        'campaign_id': CAMPAIGN,
        'timestamp_email': NOW.isoformat(),
        'is_auto_reply': 1,
        'subject': subject,
        'body': {'text': text},
    }


def test_a_vote_moves_only_to_a_safer_class_and_a_hard_bounce_stays_hard(db):
    b = seed(db)
    record(db, b, hook('reply_received', reply_subject='Out of office', reply_text='x'), *status())
    for shape in (
        "UPDATE instantly_events SET hard_bounce = true WHERE kind = 'reply'",
        "UPDATE instantly_events SET our_class = 'human' WHERE kind = 'reply'",  # "nothing matched" is no vote
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(shape)
    db.execute("UPDATE instantly_events SET our_class = 'opt_out' WHERE kind = 'reply'")
    for undo in (
        "UPDATE instantly_events SET our_class = 'auto' WHERE kind = 'reply'",
        "UPDATE instantly_events SET our_class = NULL WHERE kind = 'reply'",
        "UPDATE instantly_events SET hard_bounce = false WHERE kind = 'bounced'",
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            db.execute(undo)
