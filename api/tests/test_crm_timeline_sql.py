"""The HubSpot timeline ledger (#932) against real SQL: what a source records, how a page is read and kept, and the
claim, retry and resolve the writer and the user rely on. Needs TEST_DATABASE_URL."""

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.core.scope import acting_for, system
from app.core.system_reasons import SystemReason
from app.crm import activities, contacts, gate, write_back
from app.crm.timeline import facts, instantly_feed, ledger, queries, writer
from app.integrations.instantly_sync import mapping, reconcile
from tests import test_dispatcher_sql as dispatcher_tests
from tests.conftest import call_as, member, scoped_conn, wait_for_a_lock
from tests.test_instantly_events_sql import CAMPAIGN, email, hook, record, seed
from tests.test_instantly_events_sql import pytestmark as pytestmark  # skipped without TEST_DATABASE_URL

dispatcher_db = dispatcher_tests.db  # the dispatcher's throwaway-schema fixture
NOW = datetime.now(UTC).replace(microsecond=0)
DAY = timedelta(days=1)
T0 = NOW - timedelta(hours=10)  # the feed refuses a time in the future


@pytest.fixture
def db(dispatcher_db, monkeypatch):
    """The dispatcher's throwaway schema, with every module of the timeline and of the write-back reading it."""
    for module in (ledger, queries, instantly_feed, writer, contacts, gate, activities, write_back):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(dispatcher_db))
    for module in (ledger, writer, contacts, gate, activities):
        monkeypatch.setattr(module, 'is_disabled', lambda key: False)
    return dispatcher_db


def hubspot(db, uid, *, since=NOW - DAY, on=True, status='connected', scopes=None):
    """A HubSpot connection of ``uid`` whose write-back went on at ``since``."""
    from tests.test_crm_contacts_sql import WRITE

    db.execute(
        'INSERT INTO user_oauth_connections (user_id, organization_id, provider, refresh_token_encrypted,'
        ' granted_scopes, status, provider_account_id, provider_owner_id, crm_write_back, crm_write_back_since)'
        " VALUES (%s, default_organization(%s), 'hubspot', 'ct', %s, %s, '4242', '77', %s, %s)",
        (uid, uid, scopes or WRITE, status, on, since if on else None),
    )


def owner(db) -> str:
    return str(db.execute('SELECT created_by FROM instantly_enrollments').fetchone()['created_by'])


def ingest(binding, *, pages=5) -> int:
    with system(SystemReason.HUBSPOT_WEBHOOK):
        w = instantly_feed.read_windows()
        return instantly_feed.ingest(binding.project_id, binding.organization_id, w, max_pages=pages)


def rows(db, *columns) -> list[tuple]:
    cols = ', '.join(columns or ('state', 'code'))
    return [
        tuple(r.values()) for r in db.execute(f'SELECT {cols} FROM crm_timeline_events ORDER BY occurred_at, source_id')
    ]


def sent(db, b, ref, *, at=T0, step=1):
    record(db, b, email(1, ref, at=at, step=step))


def test_the_sql_lists_are_the_enums(db):
    """The CHECK lists of the migration are the Python enums: a member added to one and not the other fails here."""

    def check_values(table, column):
        defs = db.execute(
            "SELECT pg_get_constraintdef(c.oid) AS d FROM pg_constraint c WHERE c.conrelid = %s::regclass AND c.contype = 'c'",
            (table,),
        ).fetchall()
        found = {
            m
            for d in defs
            if re.match(rf"CHECK \(\({column} = (ANY|')", d['d'])
            for m in re.findall(r"'([a-z_]+)'", d['d'])
        }
        return found

    assert check_values('crm_timeline_events', 'source') == {s.value for s in facts.Source}
    assert check_values('crm_timeline_events', 'kind') == {k.value for k in facts.Kind}
    assert check_values('crm_timeline_events', 'state') == {s.value for s in facts.State}
    assert check_values('crm_timeline_cursors', 'source') == {s.value for s in facts.Source}
    stored = {
        m
        for d in db.execute(
            'SELECT pg_get_constraintdef(c.oid) AS d FROM pg_constraint c'
            " WHERE c.conrelid = 'crm_timeline_events'::regclass AND c.contype = 'c'"
        ).fetchall()
        if 'CRM_TIMELINE' in d['d']
        for m in re.findall(r"'(CRM_TIMELINE\.[A-Z_]+)'", d['d'])
    }
    assert stored == {c.value for c in facts.TERMINAL | facts.RETRYABLE | facts.RESOLVABLE}
    assert {c.value for c in facts.TERMINAL | facts.RETRYABLE | facts.RESOLVABLE | facts.WAITING} == {
        c.value for c in facts.Code
    }


def test_a_page_replayed_twice_is_one_row_and_the_cursor_moved(db):
    b = seed(db)
    hubspot(db, owner(db))
    sent(db, b, 's1')
    assert ingest(b) == 1
    first = rows(db, 'state', 'source_id')
    after = db.execute('SELECT after FROM crm_timeline_cursors').fetchone()['after']
    assert after > 0

    db.execute('UPDATE crm_timeline_cursors SET after = 0')  # the page is read again, as after a crash
    assert ingest(b) == 0
    assert rows(db, 'state', 'source_id') == first
    assert db.execute('SELECT after FROM crm_timeline_cursors').fetchone()['after'] == after


def test_a_crash_before_the_commit_moves_nothing(db, monkeypatch):
    b = seed(db)
    hubspot(db, owner(db))
    sent(db, b, 's1')

    def boom(conn, project_id, source, next_after):
        raise RuntimeError('crash before commit')

    monkeypatch.setattr(ledger, 'advance', boom)
    with pytest.raises(RuntimeError):
        ingest(b)
    assert rows(db) == []
    assert db.execute('SELECT count(*) AS n FROM crm_timeline_cursors WHERE after > 0').fetchone()['n'] == 0


def test_a_duplicate_send_of_a_known_step_is_skipped_and_an_unknown_step_is_not(db):
    b = seed(db)
    hubspot(db, owner(db))
    sent(db, b, 'a', step=1)
    sent(db, b, 'b', at=T0 + timedelta(hours=1), step=1)  # the same step again: a retried delivery
    sent(db, b, 'c', at=T0 + timedelta(hours=2), step=None)
    sent(db, b, 'd', at=T0 + timedelta(hours=3), step=None)
    ingest(b)
    assert rows(db) == [
        ('pending', None),
        ('skipped', facts.Code.DUPLICATE),
        ('pending', None),
        ('pending', None),
    ]


def test_a_fact_with_no_owner_is_skipped_when_somebody_expects_history_and_dropped_otherwise(db):
    b = seed(db)
    uid = owner(db)
    hubspot(db, uid)
    sent(db, b, 's1')
    db.execute('UPDATE instantly_enrollments SET created_by = NULL')
    ingest(b)
    assert rows(db) == [('skipped', facts.Code.NO_OWNER)]

    db.execute('DELETE FROM crm_timeline_events')
    db.execute('UPDATE user_oauth_connections SET crm_write_back = false, crm_write_back_since = NULL')
    db.execute('UPDATE crm_timeline_cursors SET after = 0')
    ingest(b)
    assert rows(db) == []  # nobody in the organization has the switch on


def test_admit_drops_only_what_can_never_be_due():
    w = instantly_feed.Windows(NOW, {'on': NOW - DAY}, {'o': NOW - DAY})
    admit = instantly_feed.admit
    before, during, later = NOW - 2 * DAY, NOW - timedelta(hours=1), NOW + timedelta(minutes=5)
    assert admit(before, 'on', 'o', w) is instantly_feed.Admit.DROP  # older than the start: never due
    assert admit(during, 'on', 'o', w) is instantly_feed.Admit.RECORD
    assert admit(during, 'off', 'o', w) is instantly_feed.Admit.DROP  # the switch was off at the read
    assert admit(later, 'off', 'o', w) is instantly_feed.Admit.RECORD  # a switch turned on after the read covers it
    assert admit(during, None, 'o', w) is instantly_feed.Admit.RECORD
    assert admit(before, None, 'o', w) is instantly_feed.Admit.DROP
    assert admit(during, None, 'elsewhere', w) is instantly_feed.Admit.DROP


def test_a_revoked_grant_keeps_its_window_and_the_facts_of_that_time_become_due(db):
    b = seed(db)
    uid = owner(db)
    hubspot(db, uid, status='error')  # revoked: the switch and the start stay
    sent(db, b, 's1', at=NOW - timedelta(hours=2))
    ingest(b)
    assert rows(db) == [('pending', None)]
    db.execute("UPDATE user_oauth_connections SET status = 'connected'")  # the same portal again
    with system(SystemReason.HUBSPOT_WEBHOOK):
        due = queries.due(NOW, 10)
    assert [r['kind'] for r in due] == ['outgoing_email']


def test_a_reply_with_a_message_id_is_keyed_by_it_and_an_auto_reply_is_not_recorded(db):
    b = seed(db)
    hubspot(db, owner(db))
    record(db, b, email(2, 'r1', at=NOW), email(2, 'r2', at=T0 + timedelta(minutes=30), auto=1))
    ingest(b)
    assert rows(db, 'kind', 'dedupe_key', 'message_id') == [('incoming_email', 'msg:r1@mail', 'r1@mail')]


def test_an_unsubscribe_is_an_opt_out_once_per_campaign_and_person(db):
    b = seed(db)
    hubspot(db, owner(db))
    record(db, b, hook('lead_unsubscribed', at=NOW))
    ingest(b)
    assert rows(db, 'kind', 'dedupe_key', 'state') == [
        ('opt_out', f'instantly:optout:{CAMPAIGN}:ana@acme.test', 'pending')
    ]


def fact_row(db) -> dict:
    return db.execute('SELECT * FROM crm_timeline_events').fetchone()


def test_the_claim_is_a_compare_and_set_and_freezes_the_request(db):
    b = seed(db)
    hubspot(db, owner(db))
    sent(db, b, 's1')
    ingest(b)
    row = fact_row(db)
    frozen = facts.freeze(facts.Kind.OUTGOING_EMAIL, row['occurred_at'])
    with acting_for(b.organization_id, 'test'):
        assert ledger.claim(row, frozen, '4242') is not None
        assert ledger.claim(row, frozen, '4242') is None  # the second sweep loses
    claimed = fact_row(db)
    assert claimed['state'] == 'claimed' and claimed['request']['hs_timestamp'] == frozen.hs_timestamp

    # A stale claim is taken once, by the sweep that saw it.
    db.execute("UPDATE crm_timeline_events SET claimed_at = claimed_at - interval '2 minutes'")
    stale = fact_row(db)
    with acting_for(b.organization_id, 'test'):
        assert ledger.claim(stale, frozen, '4242') is not None
        assert ledger.claim(stale, frozen, '4242') is None


def test_the_request_stays_frozen_when_the_time_sharpens(db):
    b = seed(db)
    hubspot(db, owner(db))
    sent(db, b, 's1')
    ingest(b)
    row = fact_row(db)
    with acting_for(b.organization_id, 'test'):
        ledger.claim(row, facts.freeze(facts.Kind.OUTGOING_EMAIL, row['occurred_at']), '4242')
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE crm_timeline_events SET occurred_at = occurred_at + interval '3 seconds'")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE crm_timeline_events SET request = '{}'")
    db.execute("UPDATE crm_timeline_events SET activity_id = 'a1', state = 'logged'")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE crm_timeline_events SET activity_id = 'a2'")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE crm_timeline_events SET email = 'bob@acme.test'")


def test_a_refusal_releases_the_row_until_the_fifth_and_then_fails_it(db):
    b = seed(db)
    uid = owner(db)
    hubspot(db, uid)
    sent(db, b, 's1')
    ingest(b)
    with acting_for(b.organization_id, 'test'):
        for _ in range(facts.MAX_ATTEMPTS - 1):
            ledger.settle_refused(fact_row(db), facts.Code.REFUSED)
            assert fact_row(db)['state'] == 'pending'
        ledger.settle_refused(fact_row(db), facts.Code.REFUSED)
    assert rows(db) == [('failed', facts.Code.REFUSED)]


def as_user(db, uid, fn, *args, **kwargs):
    return call_as(member(db, uid), fn, *args, **kwargs)


def test_retry_moves_the_callers_failed_rows_of_that_code_only(db):
    b = seed(db)
    uid = owner(db)
    hubspot(db, uid)
    sent(db, b, 's1')
    sent(db, b, 's2', at=T0 + timedelta(hours=1), step=2)
    ingest(b)
    db.execute(
        "UPDATE crm_timeline_events SET attempts = 5, state = 'failed', code = %s, settled_at = now()",
        (facts.Code.REFUSED.value,),
    )
    db.execute(
        'UPDATE crm_timeline_events SET code = %s WHERE source_id = (SELECT source_id FROM crm_timeline_events ORDER BY occurred_at LIMIT 1)',
        (facts.Code.CONTACT_REFUSED.value,),
    )
    db.execute(
        "INSERT INTO crm_contact_writes (owner_id, email, contact_id, created) VALUES (%s, 'ana@acme.test', '1', false)",
        (uid,),
    )

    assert as_user(db, uid, ledger.retry, uid, facts.Code.REFUSED) == 1
    assert as_user(db, uid, ledger.retry, uid, facts.Code.ANSWER_LOST) == 0  # not a retryable code
    assert sorted(rows(db, 'state')) == [('failed',), ('pending',)]
    assert fact_row_count(db, 'crm_contact_writes') == 0  # the contact is read again, and repaired if it is gone


def fact_row_count(db, table) -> int:
    return db.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']


def test_resolve_sends_again_or_takes_it_as_logged(db):
    b = seed(db)
    uid = owner(db)
    hubspot(db, uid)
    record(db, b, hook('lead_unsubscribed', at=NOW))
    ingest(b)
    frozen = facts.freeze(facts.Kind.OPT_OUT, NOW)
    db.execute(
        "UPDATE crm_timeline_events SET state = 'uncertain', code = %s, request = %s::jsonb, claimed_at = now()",
        (facts.Code.ANSWER_LOST.value, frozen.as_json()),
    )
    assert as_user(db, uid, ledger.resolve, uid, facts.Code.ANSWER_LOST, 'log_again') == 1
    assert rows(db) == [('pending', None)] and fact_row(db)['request'] is not None

    db.execute("UPDATE crm_timeline_events SET state = 'uncertain', code = %s", (facts.Code.ANSWER_LOST.value,))
    assert as_user(db, uid, ledger.resolve, uid, facts.Code.ANSWER_LOST, 'logged') == 1
    assert rows(db, 'state', 'activity_id') == [('logged', '')]
    assert as_user(db, uid, ledger.resolve, uid, facts.Code.REFUSED, 'logged') == 0


def test_status_counts_what_is_logged_and_names_why_the_rest_is_not(db):
    b = seed(db)
    uid = owner(db)
    hubspot(db, uid, status='error')
    sent(db, b, 's1')
    sent(db, b, 's2', at=T0 + timedelta(hours=1), step=2)
    sent(db, b, 's3', at=T0 + timedelta(hours=2), step=3)
    ingest(b)
    db.execute(
        "UPDATE crm_timeline_events SET state = 'logged', activity_id = 'x', request = '{}'::jsonb"
        ' WHERE source_id = (SELECT source_id FROM crm_timeline_events ORDER BY occurred_at LIMIT 1)'
    )
    db.execute(
        "UPDATE crm_timeline_events SET state = 'failed', code = %s WHERE state = 'pending' AND occurred_at > %s",
        (facts.Code.REFUSED.value, T0 + timedelta(hours=1, minutes=30)),
    )
    status = as_user(db, uid, ledger.status, uid, b.project_id)
    assert (status.logged, status.pending) == (1, 1)
    assert [(i.code, i.state, i.count, i.action) for i in status.issues] == [
        (facts.Code.REFUSED, 'failed', 1, 'retry'),
        (facts.Code.GRANT_REVOKED, 'waiting', 1, None),
    ]


def test_two_transactions_recording_one_event_leave_one_live_row_and_neither_raises(db):
    b = seed(db)
    uid = owner(db)
    conninfo = make_conninfo(os.environ['TEST_DATABASE_URL'], dbname=db.info.dbname)

    def fact(source_id):
        return facts.TimelineFact(
            facts.Source.INSTANTLY, source_id, b.organization_id, b.project_id, uid, facts.Kind.OUTGOING_EMAIL,
            'ana@acme.test', T0, 'same-event', None, facts.Person(), facts.Context('instantly', 'c', 'p', None, 1),
        )  # fmt: skip

    with (
        psycopg.connect(conninfo, row_factory=dict_row) as first,
        psycopg.connect(conninfo, row_factory=dict_row) as second,
    ):
        assert ledger.record(first, [fact(str(uuid.uuid4()))]) == 1  # holds the key's advisory lock, uncommitted
        with ThreadPoolExecutor(1) as pool:
            later = pool.submit(ledger.record, second, [fact(str(uuid.uuid4()))])
            wait_for_a_lock(conninfo, 'the second record', second.info.backend_pid)
            assert not later.done()
            first.commit()
            assert later.result(timeout=10) == 1  # recorded skipped: the unique index never raised
        second.commit()
    assert sorted(rows(db, 'state', 'code')) == [
        ('pending', None),
        ('skipped', facts.Code.DUPLICATE),
    ]


def test_a_send_hidden_behind_the_cursor_is_recorded_when_the_key_loses_its_emails_scope(db, monkeypatch):
    b = seed(db)
    hubspot(db, owner(db))
    monkeypatch.setattr(reconcile, 'get_conn', scoped_conn(db))
    record(db, b, hook('email_sent', at=T0))  # a webhook send nothing confirmed: the feed hides it
    record(db, b, hook('lead_unsubscribed', at=T0 + timedelta(hours=1)))
    assert ingest(b) == 1  # the opt-out moves the cursor past the send
    with acting_for(b.organization_id, 'test'):
        reconcile._save_refusal(b.connection_id, NOW)
    assert ingest(b) == 1
    assert sorted(rows(db, 'kind')) == [('opt_out',), ('outgoing_email',)]


def test_a_reply_is_logged_by_the_class_the_native_stop_uses(db):
    """#931: the safer of Instantly's vote and Zenbree's own. An opt-out Instantly calls an autoresponder is the email
    the person wrote; an autoresponder with no marker of Zenbree's is not logged."""
    b = seed(db)
    hubspot(db, owner(db))
    opt_out = mapping.from_email(
        {
            'id': 'r9',
            'message_id': '<r9@mail>',
            'ue_type': 2,
            'lead': 'ana@acme.test',
            'campaign_id': CAMPAIGN,
            'timestamp_email': T0.isoformat(),
            'is_auto_reply': 1,
            'subject': 'Re: hello',
            'body': {'text': 'unsubscribe'},
        },
        now=NOW,
    )
    record(db, b, opt_out, email(2, 'r2', at=T0 + timedelta(hours=1), auto=1))
    ingest(b)
    assert rows(db, 'kind', 'message_id') == [('incoming_email', 'r9@mail')]


def test_an_unvoted_reply_behind_the_cursor_is_logged_when_the_key_loses_its_emails_scope(db, monkeypatch):
    """With no emails scope no vote comes, and the native stop counts the reply as human: so does the timeline."""
    b = seed(db)
    hubspot(db, owner(db))
    monkeypatch.setattr(reconcile, 'get_conn', scoped_conn(db))
    record(db, b, hook('reply_received', at=T0))  # a webhook reply nobody voted on: hidden while a vote may come
    record(db, b, hook('lead_unsubscribed', at=T0 + timedelta(hours=1)))
    assert ingest(b) == 1
    with acting_for(b.organization_id, 'test'):
        reconcile._save_refusal(b.connection_id, NOW)
    assert ingest(b) == 1
    assert sorted(rows(db, 'kind')) == [('incoming_email',), ('opt_out',)]


def test_a_reply_webhook_delivered_twice_is_one_email_once_the_api_lists_it(db):
    """The copy the API record does not match keeps no vote of Instantly's; Zenbree's opt-out marker must not show it."""
    b = seed(db)
    hubspot(db, owner(db))
    marked = {'reply_subject': 'Re: hello', 'reply_text': 'unsubscribe'}
    record(
        db, b, hook('reply_received', at=T0, **marked), hook('reply_received', at=T0 + timedelta(minutes=2), **marked)
    )
    ingest(b)
    assert rows(db, 'kind') == []  # no Message-ID and no vote of Instantly's yet: wait for the API record
    record(db, b, email(2, 'r1', at=T0 + timedelta(minutes=1)))
    ingest(b)
    assert rows(db, 'kind', 'message_id') == [('incoming_email', 'r1@mail')]


def test_a_reply_webhook_delivered_twice_is_one_email_when_the_key_may_not_read_emails(db, monkeypatch):
    b = seed(db)
    hubspot(db, owner(db))
    monkeypatch.setattr(reconcile, 'get_conn', scoped_conn(db))
    with acting_for(b.organization_id, 'test'):
        reconcile._save_refusal(b.connection_id, NOW)
    record(db, b, hook('reply_received', at=T0))
    ingest(b)
    record(db, b, hook('reply_received', at=T0 + timedelta(minutes=2)))  # the same mail, retried with a new time
    ingest(b)
    assert rows(db, 'kind') == [('incoming_email',)]


def test_an_autoresponder_does_not_hide_a_human_reply_after_it_when_the_key_may_not_read_emails(db, monkeypatch):
    b = seed(db)
    hubspot(db, owner(db))
    monkeypatch.setattr(reconcile, 'get_conn', scoped_conn(db))
    with acting_for(b.organization_id, 'test'):
        reconcile._save_refusal(b.connection_id, NOW)
    record(db, b, hook('auto_reply_received', at=T0))
    ingest(b)
    record(db, b, hook('reply_received', at=T0 + timedelta(minutes=5)))
    ingest(b)
    assert rows(db, 'kind') == [('incoming_email',)]
