"""#747: a bounce read from the sending mailbox, against real SQL and row-level security (the task's ``ForOrg``)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.billing import row_charges
from app.core.scope import acting_for
from app.lists.rows import add_rows, create_list
from app.notifications import service as notifications
from app.outreach import bounces
from app.outreach.domain.campaign import validate_steps
from app.outreach.dsn import Bounce
from tests.conftest import hold, project, sender
from tests.test_agent import _balance, _job, _org, _user, needs_db
from tests.test_agent import db as db

STEP = [{'step': 1, 'channel': 'email', 'subject': 'Hi', 'body': 'Hi {{firstName}}', 'send_after_days': 0}]
ADA = 'ada@acme.test'


@pytest.fixture
def told(monkeypatch):
    out: list = []
    monkeypatch.setattr(notifications, 'deliver', out.append)
    return out


def _setup(db, *, verified=True, people=1, sent_ago=timedelta(days=1)) -> dict:
    """A campaign of the user's default client that sent one email to Ada, whose verified address a chat turn charged
    1 credit for, and to ``people - 1`` others, from one Gmail mailbox."""
    uid = _user(db, credits=100)
    org = _org(db, uid)
    sid, jid = _job(db, uid, 'find emails')
    hold_id = hold(db, uid, jid)
    lid = create_list(db, user_id=uid, session_id=sid, name='CTOs', entity='person')
    status = 'verified' if verified else 'unknown'
    (ada,) = add_rows(db, lid, 'person', [{'full_name': 'Ada', 'email': ADA, 'email_status': status}])
    row_charges.charge(db, hold_id, jid, 'e1', lid, [ada], ('email', 'email_status', 'email_source'), 1)
    db.execute('SELECT settle_hold(%s)', (hold_id,))
    mailbox = sender(db, uid, 'google', account_label='me@acme.test')
    campaign = db.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, list_id)
               VALUES (%s, %s, %s, 'Q4', 'active', %s, %s) RETURNING id::text""",
        (org, project(db, uid), uid, json.dumps(validate_steps(STEP)), lid),
    ).fetchone()['id']
    for i in range(people):
        email = ADA if i == 0 else f'p{i}@prospect.test'
        target = db.execute(
            """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, status, next_step)
               VALUES (%s, %s, %s, 'P', 'active', 2) RETURNING id""",
            (campaign, uid, email),
        ).fetchone()['id']
        db.execute(
            """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
                   idempotency_key, delivered_at, message_id, sender_id)
               VALUES (%s, %s, %s, 1, 'email', 'sent', %s, %s, %s, %s, %s)""",
            (
                campaign,
                uid,
                target,
                email,
                str(uuid.uuid4()),
                datetime.now(UTC) - sent_ago,
                f'<m{i}@acme.test>',
                mailbox,
            ),
        )
    return {
        'uid': uid,
        'org': org,
        'mailbox': mailbox,
        'campaign': campaign,
        'project': project(db, uid),
        'list': lid,
        'row': ada,
    }


def _charged_row(db, uid: str, email: str = ADA) -> tuple[str, str]:
    """Another person list of ``uid``'s default client with a row of ``email`` a settled turn charged: (list, row)."""
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE user_id = %s", (uid,))  # one active job per user
    sid, jid = _job(db, uid, 'more emails')
    hold_id = hold(db, uid, jid)
    lid = create_list(db, user_id=uid, session_id=sid, name='Other', entity='person')
    (row,) = add_rows(db, lid, 'person', [{'full_name': 'Ada', 'email': email, 'email_status': 'verified'}])
    row_charges.charge(db, hold_id, jid, 'e2', lid, [row], ('email', 'email_status', 'email_source'), 1)
    db.execute('SELECT settle_hold(%s)', (hold_id,))
    return lid, row


def _row(db, row_id: str) -> dict:
    return db.execute(
        'SELECT r.cells, (SELECT state FROM row_charges WHERE row_id = r.id) AS charge FROM list_rows r WHERE id = %s',
        (row_id,),
    ).fetchone()


def _bounce(s: dict, recipient=ADA, kind='hard', message_id='<m0@acme.test>', *, reverified_invalid=True) -> bool:
    """``reverified_invalid``: what MillionVerifier said again at the bounce (``mailbox_push`` asks it)."""
    bounce = Bounce(recipient, kind, '5.1.1' if kind == 'hard' else '4.4.7', message_id)
    with acting_for(s['org'], 'mailbox push'):
        return bounces.record(s['mailbox'], bounce, reverified_invalid=reverified_invalid)


def _refunds(db, s) -> list[tuple[str, int]]:
    rows = db.execute(
        "SELECT reference_type, amount FROM credit_transactions WHERE organization_id = %s AND type = 'refund'"
        " AND reference_type = 'email_bounce'",
        (s['org'],),
    )
    return [(r['reference_type'], r['amount']) for r in rows]


@needs_db
def test_a_hard_bounce_of_a_verified_address_stops_excludes_and_refunds_once(db, told, webhook_events):
    s = _setup(db)
    before = _balance(db, s['uid'])

    assert _bounce(s)

    send = db.execute('SELECT bounce_kind, bounce_status, bounced_at FROM outreach_campaign_sends').fetchone()
    assert send['bounce_kind'] == 'hard' and send['bounce_status'] == '5.1.1' and send['bounced_at']
    assert db.execute('SELECT status FROM outreach_campaign_targets').fetchone()['status'] == 'excluded'
    excluded = db.execute("SELECT origin FROM exclusions WHERE kind = 'email' AND value = %s", (ADA,)).fetchall()
    assert [e['origin'] for e in excluded] == ['bounced']
    assert _refunds(db, s) == [('email_bounce', 1)] and _balance(db, s['uid']) == before + 1
    assert db.execute('SELECT state FROM row_charges').fetchone()['state'] == 'refunded'
    assert [p['data']['reason'] for _, p in webhook_events if p['type'] == 'sequence.stopped'] == ['bounced']

    assert not _bounce(s)  # Gmail redelivers a push: nothing twice
    assert _refunds(db, s) == [('email_bounce', 1)]


@needs_db
def test_a_hard_bounce_stops_the_person_in_the_clients_other_campaigns(db, told):
    """#931's hard bounce path: the person's other campaign of the client stops and its waiting draft is skipped."""
    s = _setup(db)
    other = db.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps)
           VALUES (%s, %s, %s, 'Q1', 'active', %s) RETURNING id::text""",
        (s['org'], s['project'], s['uid'], json.dumps(validate_steps(STEP))),
    ).fetchone()['id']
    target = db.execute(
        """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, status, next_step)
           VALUES (%s, %s, %s, 'P', 'active', 1) RETURNING id""",
        (other, s['uid'], ADA.upper()),
    ).fetchone()['id']
    db.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'awaiting_approval', %s, %s)""",
        (other, s['uid'], target, ADA, str(uuid.uuid4())),
    )

    assert _bounce(s)

    assert db.execute('SELECT status FROM outreach_campaign_targets WHERE id = %s', (target,)).fetchone() == {
        'status': 'excluded'
    }
    assert db.execute('SELECT status FROM outreach_campaign_sends WHERE target_id = %s', (target,)).fetchone() == {
        'status': 'skipped'
    }


@needs_db
def test_a_soft_bounce_is_recorded_and_nothing_else(db, told):
    s = _setup(db)

    assert _bounce(s, kind='soft')

    assert db.execute('SELECT bounce_kind FROM outreach_campaign_sends').fetchone()['bounce_kind'] == 'soft'
    assert db.execute('SELECT status FROM outreach_campaign_targets').fetchone()['status'] == 'active'
    assert not db.execute('SELECT 1 FROM exclusions WHERE value = %s', (ADA,)).fetchone()
    assert _refunds(db, s) == []
    assert _bounce(s)  # it then fails for good: the hard bounce wins
    assert db.execute('SELECT bounce_kind FROM outreach_campaign_sends').fetchone()['bounce_kind'] == 'hard'


@needs_db
@pytest.mark.parametrize('case', [{'verified': False}, {'sent_ago': timedelta(days=8)}])
def test_no_refund_for_an_unverified_address_or_after_seven_days(db, told, case):
    s = _setup(db, **case)

    assert _bounce(s)

    assert _refunds(db, s) == [] and db.execute('SELECT state FROM row_charges').fetchone()['state'] == 'charged'
    assert db.execute('SELECT status FROM outreach_campaign_targets').fetchone()['status'] == 'excluded'


@needs_db
@pytest.mark.parametrize(
    'forged',
    [
        {'message_id': '<guessed@acme.test>'},  # no send of ours
        {'message_id': '<m0@acme.test>', 'recipient': 'stranger@else.test'},  # our send, to someone else
        {'message_id': ''},  # quotes nothing
    ],
)
def test_a_report_that_names_no_send_of_the_mailbox_changes_nothing(db, told, forged):
    """#747: only the Message-ID we stored at send time, from this mailbox to this recipient, is a bounce."""
    s = _setup(db)
    before = _balance(db, s['uid'])

    assert not _bounce(s, **forged)

    assert db.execute('SELECT bounce_kind FROM outreach_campaign_sends').fetchone()['bounce_kind'] is None
    assert db.execute('SELECT status FROM outreach_campaign_targets').fetchone()['status'] == 'active'
    assert not db.execute('SELECT 1 FROM exclusions').fetchone()
    assert _refunds(db, s) == [] and _balance(db, s['uid']) == before


@needs_db
def test_a_bounce_on_another_mailbox_is_not_this_send(db, told):
    s = _setup(db)
    other = sender(db, s['uid'], 'microsoft', account_label='me@acme.test')
    with acting_for(s['org'], 'mailbox push'):
        assert not bounces.record(other, Bounce(ADA, 'hard', '5.1.1', '<m0@acme.test>'))


@needs_db
def test_the_campaign_pauses_above_five_percent_of_twenty_sends_and_tells_its_owner(db, told):
    s = _setup(db, people=20)

    assert _bounce(s)  # 1 of 20 is 5 %: not above it
    assert db.execute('SELECT status FROM outreach_campaigns').fetchone()['status'] == 'active' and told == [None]

    assert _bounce(s, recipient='p1@prospect.test', message_id='<m1@acme.test>')

    assert db.execute('SELECT status FROM outreach_campaigns').fetchone()['status'] == 'paused'
    (n,) = db.execute("SELECT kind, href, data FROM notifications WHERE kind = 'bounces_high'").fetchall()
    assert n['href'] == f'/campaigns/{s["campaign"]}'
    assert n['data'] == {'campaign': 'Q4', 'invalid': 2, 'attempted': 20, 'paused': True}
    assert told[-1] is not None and told[-1].item['kind'] == 'bounces_high'


@needs_db
def test_fewer_than_twenty_sends_never_pause(db, told):
    s = _setup(db, people=19)
    for i in range(3):
        _bounce(s, recipient=ADA if i == 0 else f'p{i}@prospect.test', message_id=f'<m{i}@acme.test>')
    assert db.execute('SELECT status FROM outreach_campaigns').fetchone()['status'] == 'active'
    assert not db.execute("SELECT 1 FROM notifications WHERE kind = 'bounces_high'").fetchone()


@needs_db
def test_a_report_quoting_the_message_id_the_provider_gave_the_send_matches_it(db, told):
    """#747 review: Gmail and Exchange may replace our Message-ID; the one read back after the send counts too."""
    s = _setup(db)
    db.execute("UPDATE outreach_campaign_sends SET provider_message_id = '<CAgmail@mail.gmail.com>'")

    assert _bounce(s, message_id='<CAgmail@mail.gmail.com>')

    assert db.execute('SELECT bounce_kind FROM outreach_campaign_sends').fetchone()['bounce_kind'] == 'hard'


@needs_db
def test_the_refund_gives_the_project_its_monthly_cap_back(db, told):
    s = _setup(db)
    db.execute(
        "UPDATE credit_transactions SET project_id = %s WHERE type IN ('reservation', 'deduction')", (s['project'],)
    )
    spent = db.execute('SELECT project_month_spend(%s) AS n', (s['project'],)).fetchone()['n']
    assert spent == 1  # the email the turn kept

    assert _bounce(s)

    assert db.execute('SELECT project_month_spend(%s) AS n', (s['project'],)).fetchone()['n'] == 0


@needs_db
def test_a_refund_takes_the_address_back_from_the_list_row(db, told):
    """#747 owner decision (2026-10-16): the refund takes the data back, so a report the user plants in their own
    mailbox to win the credit gains nothing: the row loses the address, which stays excluded."""
    s = _setup(db)
    before = _balance(db, s['uid'])

    assert _bounce(s)

    (row,) = db.execute('SELECT cells, cell_meta FROM list_rows').fetchall()
    assert not {'email', 'email_status', 'email_source'} & (row['cells'].keys() | row['cell_meta'].keys())
    assert row['cells']['full_name'] == 'Ada'  # nothing else is taken
    assert [e['origin'] for e in db.execute('SELECT origin FROM exclusions WHERE value = %s', (ADA,))] == ['bounced']
    assert _balance(db, s['uid']) == before + 1

    assert not _bounce(s)  # the same report again changes nothing
    assert _balance(db, s['uid']) == before + 1 and _refunds(db, s) == [('email_bounce', 1)]


@needs_db
def test_an_address_typed_by_hand_over_the_bought_one_is_not_refunded(db, told):
    s = _setup(db)
    db.execute("""UPDATE list_rows SET cell_meta = cell_meta || '{"email": {"status": "manual"}}'""")

    assert _bounce(s)  # still a bounce: stop, exclusion

    assert _refunds(db, s) == [] and db.execute('SELECT cells FROM list_rows').fetchone()['cells']['email'] == ADA


@needs_db
def test_a_row_whose_email_changed_since_the_send_keeps_it_and_refunds_nothing(db, told):
    """The take-back clears only the bounced address, on its row, while the row still holds it: an email found again
    or typed since is the user's now."""
    s = _setup(db)
    db.execute('UPDATE list_rows SET cells = cells || \'{"email": "ada.new@acme.test"}\' WHERE id = %s', (s['row'],))

    assert _bounce(s)  # still stops and excludes the bounced address

    row = _row(db, s['row'])
    assert row['cells']['email'] == 'ada.new@acme.test' and row['cells']['email_status'] == 'verified'
    assert row['charge'] == 'charged' and _refunds(db, s) == []
    assert db.execute('SELECT origin FROM exclusions WHERE value = %s', (ADA,)).fetchone()['origin'] == 'bounced'


@needs_db
def test_the_same_address_in_another_list_or_client_is_never_touched(db, told):
    """Only the campaign's own list row loses the address; the exclusion covers the others."""
    s = _setup(db)
    _, other_row = _charged_row(db, s['uid'])  # same client, another list
    stranger = _user(db, credits=10)
    _, foreign_row = _charged_row(db, stranger)  # another organization

    assert _bounce(s)

    assert 'email' not in _row(db, s['row'])['cells'] and _row(db, s['row'])['charge'] == 'refunded'
    for kept in (other_row, foreign_row):
        assert _row(db, kept)['cells']['email'] == ADA and _row(db, kept)['charge'] == 'charged'
    assert _refunds(db, s) == [('email_bounce', 1)]


@needs_db
def test_a_campaign_whose_list_is_gone_refunds_nothing(db, told):
    s = _setup(db)
    db.execute('UPDATE outreach_campaigns SET list_id = NULL')

    assert _bounce(s)

    assert _refunds(db, s) == [] and _row(db, s['row'])['cells']['email'] == ADA


@needs_db
def test_without_an_invalid_reverification_the_bounce_stands_and_refunds_nothing(db, told):
    """Owner decision on #919: valid, catch-all, no answer or the verifier switched off refund nothing, and the row
    keeps its address; the bounce still stops, excludes and counts toward the pause."""
    s = _setup(db, people=20)

    assert _bounce(s, reverified_invalid=False)
    assert _bounce(s, recipient='p1@prospect.test', message_id='<m1@acme.test>', reverified_invalid=False)

    assert _refunds(db, s) == [] and _row(db, s['row'])['cells']['email'] == ADA
    assert _row(db, s['row'])['charge'] == 'charged'
    assert db.execute('SELECT origin FROM exclusions WHERE value = %s', (ADA,)).fetchone()['origin'] == 'bounced'
    assert db.execute('SELECT status FROM outreach_campaigns').fetchone()['status'] == 'paused'


@needs_db
def test_only_a_hard_bounce_of_a_send_in_the_window_is_worth_a_reverification(db, told):
    s = _setup(db)

    def may(**kw):
        bounce = Bounce(kw.get('to', ADA), kw.get('kind', 'hard'), '5.1.1', kw.get('mid', '<m0@acme.test>'))
        with acting_for(s['org'], 'mailbox push'):
            return bounces.may_refund(s['mailbox'], bounce)

    assert may()
    assert not may(kind='soft') and not may(mid='<guessed@acme.test>') and not may(to='stranger@else.test')
    db.execute("UPDATE outreach_campaign_sends SET delivered_at = now() - interval '8 days'")
    assert not may()
