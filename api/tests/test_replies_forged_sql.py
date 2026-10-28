import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.outreach import inbox_reply
from tests.conftest import act_for, member, scoped_conn, sender, sign_in
from tests.test_replies_sql import DMARC_FAIL, DMARC_GRAY, QUEUED, _campaign, _post, _target
from tests.test_replies_sql import db as db
from tests.test_reply_pause_sql import _label
from tests.test_tools_sql import _user


def _fake_labels(monkeypatch):
    class Provider:
        async def chat(self, **kw):
            return type(
                'R', (), {'text': json.dumps({'label': 'meeting_request', 'body': 'Giovedì alle 11 va bene.'})}
            )()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Provider())


async def test_a_failed_dmarc_reply_is_flagged_and_announces_nothing(db, http, webhook_events, monkeypatch):
    monkeypatch.setattr(inbox_reply, 'get_conn', scoped_conn(db))
    uid = _user(db)
    forged = _campaign(db, uid, email='zed@client.test')
    _campaign(db, uid)
    client = TestClient(app)
    _post(client, uid, sender='Zed <zed@client.test>', email_id='bad', authentication=DMARC_FAIL)
    _post(client, uid, email_id='good')
    _fake_labels(monkeypatch)
    http.handler = lambda r: httpx.Response(200, json={'id': 'mail_1'})
    for message_id in QUEUED:
        await _label(db, message_id)

    rows = {r['provider_message_id']: r for r in db.execute('SELECT * FROM outreach_inbound_messages')}
    assert rows['bad']['sender_authenticated'] is False and rows['good']['sender_authenticated'] is True
    assert rows['bad']['label'] == 'meeting_request'
    assert _target(db, forged['target'])['status'] == 'replied'
    announced = [(p['type'], p['data'].get('from') or p['data'].get('email')) for _, p in webhook_events]
    assert ('contact.replied', 'zed@client.test') not in announced
    assert ('sequence.stopped', 'zed@client.test') in announced
    assert ('contact.replied', 'anna@client.test') in announced
    notes = {r['href']: r for r in db.execute('SELECT href, kind, data, emailed_at, email_pending FROM notifications')}
    bad, good = notes[f'/inbox?m={rows["bad"]["id"]}'], notes[f'/inbox?m={rows["good"]["id"]}']
    assert len(notes) == 2
    assert bad['kind'] == 'reply' and bad['data']['unverified'] is True and not bad['emailed_at']
    assert good['kind'] == 'reply_urgent' and 'unverified' not in good['data'] and good['emailed_at']
    assert len(http.calls) == 1
    assert rows['bad']['draft_body'] is None
    act_for(member(db, uid).org_id, 'draft reply')  # the draft task, an organization task
    assert await inbox_reply.draft(str(rows['bad']['id'])) is False
    assert await inbox_reply.draft(str(rows['good']['id'])) is True
    drafts = {r['provider_message_id']: r['draft_body'] for r in db.execute('SELECT * FROM outreach_inbound_messages')}
    assert drafts == {'bad': None, 'good': 'Giovedì alle 11 va bene.'}

    sign_in(app, member(db, uid))
    listed = {m['from_address']: m['sender_authenticated'] for m in client.get('/v1/inbox').json()['messages']}
    assert listed == {'zed@client.test': False, 'anna@client.test': True}
    thread = client.get(f'/v1/inbox/{rows["bad"]["id"]}').json()
    assert thread['message']['sender_authenticated'] is False


def test_no_verdict_and_linkedin_replies_store_none(db):
    uid = _user(db)
    _campaign(db, uid)
    _campaign(db, uid, email=None, linkedin_url='https://linkedin.com/in/anna-v', provider_id='ACoAA1')
    sender(db, uid, 'unipile', provider_account_id='acc_1')
    client = TestClient(app)
    _post(client, uid, email_id='plain', authentication=DMARC_GRAY)
    payload = {
        'event': 'message_received',
        'account_id': 'acc_1',
        'account_info': {'user_id': 'ME'},
        'message_id': 'l1',
        'message': 'Ciao',
        'sender': {'attendee_provider_id': 'ACoAA1', 'attendee_profile_url': '', 'attendee_name': 'Anna'},
    }
    res = client.post('/v1/integrations/linkedin/webhook', json=payload, headers={'Unipile-Auth': 'uni-secret'})
    assert res.json()['targets'] == 1
    rows = db.execute('SELECT channel, sender_authenticated FROM outreach_inbound_messages').fetchall()
    assert {(r['channel'], r['sender_authenticated']) for r in rows} == {('email', None), ('linkedin', None)}
