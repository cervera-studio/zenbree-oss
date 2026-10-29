"""#981: the daily cleanup of auth_email_sends keeps the last 24 hours that the web's per-address cap counts."""

from app.worker import llm_usage_ttl_cleanup
from tests.test_billing_admin_sql import db as db  # the throwaway database fixture


def test_the_cleanup_deletes_only_rows_older_than_a_day(db):
    db.execute(
        """INSERT INTO auth_email_sends (email, sent_at) VALUES
           ('old@x.test', now() - interval '25 hours'), ('new@x.test', now() - interval '23 hours')"""
    )
    assert llm_usage_ttl_cleanup() == {'deleted': 0, 'auth_email_sends': 1}
    assert [r['email'] for r in db.execute('SELECT email FROM auth_email_sends').fetchall()] == ['new@x.test']
