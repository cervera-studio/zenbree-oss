"""An inbox answer goes from the account of the person's conversation, or from none (#935).

Needs TEST_DATABASE_URL. The mailbox is faked.
"""

import pytest

from tests import test_inbox_reply_sql
from tests.test_dispatcher_sql import _mailbox
from tests.test_replies_sql import DB_URL

db = test_inbox_reply_sql.db
provider = test_inbox_reply_sql.provider
owner = test_inbox_reply_sql.owner

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: inbox reply pin SQL test NOT run')


def _refused(owner) -> None:
    client, mid = owner['client'], owner['id']
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Giovedì alle 11 va bene.'})
    assert res.json()['detail']['code'] == 'INBOX.NO_MAILBOX'
    assert client.get(f'/v1/inbox/{mid}').json()['reply_blocked'] == 'INBOX.NO_MAILBOX'
    assert owner['sent'] == []


def test_an_answer_goes_from_the_conversations_account_among_several(owner, db):
    microsoft = _mailbox(
        db, owner['uid'], provider='microsoft', granted_scopes=['User.Read', 'Mail.Send'], account_label='ms@acme.test'
    )
    db.execute(
        "UPDATE outreach_campaign_targets SET sender_id = %s, sender_pinned_at = now() WHERE email = 'anna@client.test'",
        (microsoft,),
    )
    client, mid = owner['client'], owner['id']

    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Giovedì alle 11 va bene.'})

    assert res.status_code == 200 and res.json()['status'] == 'sent'
    assert [msg['From'] for msg, _ in owner['sent']] == ['ms@acme.test']


def test_no_answer_from_the_account_that_replaced_the_conversations_account(owner, db):
    first = db.execute('SELECT id FROM user_oauth_connections').fetchone()['id']
    db.execute(
        "UPDATE outreach_campaign_targets SET sender_id = %s, sender_pinned_at = now() WHERE email = 'anna@client.test'",
        (first,),
    )
    second = db.execute(
        """INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,
               granted_scopes, account_label, sign_off, created_at)
           SELECT organization_id, user_id, provider, refresh_token_encrypted, granted_scopes, 'second@acme.test',
                  sign_off, created_at FROM user_oauth_connections WHERE id = %s RETURNING id""",
        (first,),
    ).fetchone()['id']
    db.execute('UPDATE project_senders SET sender_id = %s WHERE sender_id = %s', (second, first))  # as attach does

    _refused(owner)


def test_no_answer_on_a_conversation_whose_account_is_unknown(owner, db):
    db.execute("UPDATE outreach_campaign_targets SET sender_pinned_at = now() WHERE email = 'anna@client.test'")

    _refused(owner)  # the project's only mailbox may not be the one that wrote


@pytest.mark.parametrize('reason', ['mailbox_unknown', 'disconnected', 'needs_reconnect', 'unlinked'])
def test_the_reply_box_says_why_the_conversations_account_cannot_answer(owner, db, reason):
    status = {'disconnected': 'disconnected', 'needs_reconnect': 'error'}.get(reason, 'connected')
    account = _mailbox(db, owner['uid'], account_label='theirs@acme.test', status=status)
    if reason == 'unlinked':
        db.execute('DELETE FROM project_senders WHERE sender_id = %s', (account,))
    db.execute(
        "UPDATE outreach_campaign_targets SET sender_id = %s, sender_pinned_at = now() WHERE email = 'anna@client.test'",
        (None if reason == 'mailbox_unknown' else account,),
    )

    thread = owner['client'].get(f'/v1/inbox/{owner["id"]}').json()

    assert (thread['reply_blocked'], thread['reply_hold']) == ('INBOX.NO_MAILBOX', reason)


def test_a_reply_box_that_can_answer_shows_no_hold(owner):
    thread = owner['client'].get(f'/v1/inbox/{owner["id"]}').json()

    assert (thread['reply_blocked'], thread['reply_hold']) == (None, None)
