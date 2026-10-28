"""#1173: internal mail quoting a campaign send is neither a lead reply nor mailbox content we may read."""

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from app.core.reply_address import build_campaign_reply_address
from app.main import app
from tests.conftest import member
from tests.test_agent import _user, needs_db
from tests.test_bounces_sql import _setup
from tests.test_mailbox_replies_sql import Gmail, Graph, _mail, _open_send, _open_sends, _stored, _target
from tests.test_replies_sql import QUEUED, _post
from tests.test_replies_sql import db as db

pytestmark = needs_db


def _owner(db, s, email='owner@company.test'):
    db.execute('UPDATE "user" SET email = %s WHERE id = %s', (email, s['uid']))


def _colleague(db, org, email):
    uid = _user(db)
    db.execute('UPDATE "user" SET email = %s WHERE id = %s', (email, uid))
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, uid, 'member'),
    )


def _resend(s, sender, *, subject='Re: Hi', headers=None, legacy=False):
    to = (
        None if legacy else build_campaign_reply_address(s['campaign'], domain='in.zenbree.test', secret='reply-secret')
    )
    response = _post(
        TestClient(app),
        s['uid'],
        sender=sender,
        subject=subject,
        headers={'In-Reply-To': '<m0@acme.test>'} | (headers or {}),
        email_id='resend-' + s['campaign'],
        to=to,
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize('path', ['resend', 'legacy', 'gmail', 'graph'])
@pytest.mark.parametrize('identity', ['owner', 'member', 'domain'])
@pytest.mark.parametrize('kind', ['reply', 'unsubscribe', 'auto_reply'])
def test_internal_mail_cannot_stop_or_be_filed_as_the_quoted_lead(
    db, monkeypatch, http, webhook_events, path, identity, kind
):
    s = _setup(db)
    _open_send(db, s)
    _owner(db, s)
    address = {'owner': 'owner@company.test', 'member': 'colleague@other.test', 'domain': 'colleague@company.test'}[
        identity
    ]
    if identity == 'member':
        _colleague(db, s['org'], address)
    subject = 'unsubscribe' if kind == 'unsubscribe' else 'Re: Hi'
    headers = {'Auto-Submitted': 'auto-replied'} if kind == 'auto_reply' else {}
    mail = _mail(address, subject=subject)
    for key, value in headers.items():
        mail[key] = value

    for _ in range(2):
        if path in ('resend', 'legacy'):
            assert _resend(s, address, subject=subject, headers=headers, legacy=path == 'legacy') == {
                'status': 'ignored'
            }
        else:
            provider = (
                Gmail(monkeypatch, http, s, {'internal': mail})
                if path == 'gmail'
                else Graph(monkeypatch, http, db, s, {'internal': mail})
            )
            assert (provider.push(db) if path == 'gmail' else provider.push(db, ['internal'])) == 0
            assert provider.fetched == []
        assert _target(db, s) == 'active' and _open_sends(db, s) == 1
        assert _stored(db, s) == []
        assert db.execute('SELECT count(*) AS n FROM exclusions').fetchone()['n'] == 0
        assert QUEUED == [] and webhook_events == []


@pytest.mark.parametrize('path', ['resend', 'gmail'])
@pytest.mark.parametrize('identity', ['public_domain', 'lookalike_domain', 'other_org', 'lead_colleague'])
def test_external_replies_still_stop_and_file_under_the_quoted_lead(
    db, monkeypatch, http, webhook_events, path, identity
):
    s = _setup(db)
    _open_send(db, s)
    _owner(db, s, 'owner@gmail.com' if identity == 'public_domain' else 'owner@company.test')
    address = {
        'public_domain': 'lead@gmail.com',
        'lookalike_domain': 'lead@company.test.other.test',
        'other_org': 'colleague@other.test',
        'lead_colleague': 'colleague@client.test',
    }[identity]
    if identity == 'other_org':
        other_org = member(db, _user(db)).org_id
        db.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (other_org, s['uid'], 'member'),
        )
        _colleague(db, other_org, address)
    if path == 'resend':
        assert _resend(s, address)['status'] == 'accepted'
    else:
        provider = Gmail(monkeypatch, http, s, {'reply': _mail(address)})
        assert provider.push(db) == 1 and provider.fetched == ['reply']
    assert _target(db, s) == 'replied' and _open_sends(db, s) == 0
    (stored,) = _stored(db, s)
    assert stored['from_address'] == address and stored['kind'] == 'reply'
    row = db.execute('SELECT target_id, campaign_id FROM outreach_inbound_messages').fetchone()
    assert row['target_id'] is not None and str(row['campaign_id']) == s['campaign']


@settings(
    max_examples=8,
    database=None,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(deliveries=st.integers(1, 4))
@example(deliveries=2)
def test_any_number_of_internal_deliveries_leaves_the_sequence_and_inbox_unchanged(
    db, monkeypatch, http, webhook_events, deliveries
):
    s = _setup(db)
    _owner(db, s, f'{s["uid"]}@company.test')
    provider = Gmail(monkeypatch, http, s, {'internal': _mail('colleague@company.test')})
    for _ in range(deliveries):
        assert provider.push(db) == 0
    assert provider.fetched == [] and _stored(db, s) == []
    assert _target(db, s) == 'active' and _open_sends(db, s) == 0
