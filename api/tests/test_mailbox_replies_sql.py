"""#1165: a reply to a campaign send, read from the sending mailbox, against real SQL and row-level security.

A stand-in Gmail serves the history, the headers and (only when asked) the whole message. ``fetched`` lists every
message read whole: an unrelated one must never be in it, and nothing of it is stored."""

import asyncio
import base64
from email import message_from_bytes
from email.message import EmailMessage

import httpx
import pytest

from app import worker
from app.core.scope import acting_for
from app.integrations import mailbox_push
from app.integrations.providers import GOOGLE, MICROSOFT
from app.outreach import dispatcher
from tests.conftest import member, project, sender
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_bounces_sql import ADA, _setup
from tests.test_dsn import dsn

pytestmark = needs_db

AUTH_PASS = (
    'mx.google.com; dkim=pass header.i=@acme.test; spf=pass; dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=acme.test'
)
AUTH_FAIL = 'mx.google.com; dkim=none; spf=fail; dmarc=fail (p=REJECT) header.from=acme.test'


def _mail(
    sender=ADA, *, refs='<m0@acme.test>', mid='<r1@acme.test>', subject='Re: Hi', text='Sounds good', auth=AUTH_PASS
):
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'], msg['Message-ID'] = sender, 'me@acme.test', subject, mid
    if refs:
        msg['In-Reply-To'] = refs
        msg['References'] = refs
    if auth:
        msg['Authentication-Results'] = auth
    msg.set_content(text)
    return msg


@pytest.fixture(autouse=True)
def quiet(monkeypatch, webhook_events):
    monkeypatch.setattr(worker.label_reply, 'delay', lambda message_id: None)


class Gmail:
    """The mailbox's stand-in: ``messages`` in arrival order, one history record each, numbered from 71."""

    def __init__(self, monkeypatch, http, s: dict, messages: dict[str, EmailMessage]):
        self.s, self.fetched, self.org = s, [], s['org']
        self.messages = {k: v if isinstance(v, bytes) else bytes(v) for k, v in messages.items()}

        async def token(mb, provider):
            return 'tok'

        monkeypatch.setattr(dispatcher, '_access_token', token)
        http.handler = self._serve

    def _serve(self, r: httpx.Request) -> httpx.Response:
        path, query = r.url.path, dict(r.url.params)
        if path.endswith('/history'):
            history = [
                {'id': str(71 + i), 'messagesAdded': [{'message': {'id': m}}]} for i, m in enumerate(self.messages)
            ]
            return httpx.Response(200, json={'history': history, 'historyId': '99'})
        mid = path.rsplit('/', 1)[-1]
        raw = self.messages[mid]
        if query.get('format') == 'raw':
            self.fetched.append(mid)
            return httpx.Response(200, json={'raw': base64.urlsafe_b64encode(raw).decode().rstrip('=')})
        headers = [{'name': k, 'value': str(v)} for k, v in message_from_bytes(raw).items()]
        return httpx.Response(200, json={'payload': {'headers': headers}, 'sizeEstimate': len(raw)})

    def push(self, db) -> int:
        """One delivery of the provider's push: the read starts again from history id 70."""
        db.execute(
            'UPDATE user_oauth_connections SET inbound_history_id = %s, granted_scopes = %s WHERE id = %s',
            ('70', list(GOOGLE.scopes), self.s['mailbox']),
        )
        with acting_for(self.org, 'mailbox push'):
            return _run(self.s['mailbox'])


def _run(connection_id: str) -> int:
    return asyncio.run(mailbox_push.read(connection_id, []))


def _stored(db, s) -> list[dict]:
    return db.execute(
        'SELECT kind, from_address, provider_message_id, sender_authenticated FROM outreach_inbound_messages'
        ' WHERE project_id = %s ORDER BY received_at',
        (s['project'],),
    ).fetchall()


def _target(db, s) -> str:
    return db.execute(
        'SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s LIMIT 1', (s['campaign'],)
    ).fetchone()['status']


def _open_send(db, s) -> None:
    t = db.execute(
        'SELECT id, owner_id FROM outreach_campaign_targets WHERE campaign_id = %s LIMIT 1', (s['campaign'],)
    ).fetchone()
    db.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               idempotency_key, message_id) VALUES (%s, %s, %s, 2, 'email', 'approved', %s, 'k2', '<m2@acme.test>')""",
        (s['campaign'], t['owner_id'], t['id'], ADA),
    )


def _open_sends(db, s) -> int:
    return db.execute(
        "SELECT count(*) AS n FROM outreach_campaign_sends WHERE campaign_id = %s AND status = 'approved'",
        (s['campaign'],),
    ).fetchone()['n']


def test_a_reply_to_a_send_stops_the_person_and_an_unrelated_message_is_never_read_whole(db, monkeypatch, http):
    s = _setup(db)
    _open_send(db, s)
    mail = {'reply': _mail(), 'other': _mail('anna@client.test', refs='', mid='<o1@client.test>', text='Newsletter')}
    gmail = Gmail(monkeypatch, http, s, mail)

    assert gmail.push(db) == 1

    assert gmail.fetched == ['reply']  # the unrelated message's body was never asked for
    (stored,) = _stored(db, s)
    assert stored['kind'] == 'reply' and stored['from_address'] == ADA and stored['sender_authenticated'] is True
    assert _target(db, s) == 'replied' and _open_sends(db, s) == 0
    assert db.execute('SELECT count(*) AS n FROM outreach_inbound_messages').fetchone()['n'] == 1


def test_a_reply_from_another_address_is_matched_by_the_message_it_quotes(db, monkeypatch, http):
    s = _setup(db)
    gmail = Gmail(monkeypatch, http, s, {'alias': _mail('ada.personal@home.test')})

    assert gmail.push(db) == 1

    assert _target(db, s) == 'replied' and [m['kind'] for m in _stored(db, s)] == ['reply']


def test_a_message_from_a_target_that_quotes_nothing_is_matched_by_its_sender(db, monkeypatch, http):
    s = _setup(db)
    gmail = Gmail(monkeypatch, http, s, {'fresh': _mail(refs='')})

    assert gmail.push(db) == 1 and gmail.fetched == ['fresh'] and _target(db, s) == 'replied'


def test_a_message_quoting_a_send_of_another_mailbox_is_not_ours(db, monkeypatch, http):
    s = _setup(db)
    gmail = Gmail(monkeypatch, http, s, {'x': _mail('stranger@else.test', refs='<m0@other.test>')})

    assert gmail.push(db) == 0

    assert gmail.fetched == [] and _stored(db, s) == [] and _target(db, s) == 'active'


def test_an_opt_out_reply_excludes_only_on_the_providers_dmarc_pass(db, monkeypatch, http):
    s = _setup(db)
    gmail = Gmail(monkeypatch, http, s, {'forged': _mail(text='no grazie, non scrivetemi piu', auth=AUTH_FAIL)})
    assert gmail.push(db) == 1
    assert (
        db.execute('SELECT count(*) AS n FROM exclusions WHERE project_id = %s', (s['project'],)).fetchone()['n'] == 0
    )

    s = _setup(db)  # a second, own world: the genuine opt-out
    gmail = Gmail(monkeypatch, http, s, {'real': _mail(text='no grazie, non scrivetemi piu', mid='<r2@acme.test>')})
    assert gmail.push(db) == 1
    rules = db.execute('SELECT kind, value FROM exclusions WHERE project_id = %s', (s['project'],)).fetchall()
    assert [(r['kind'], r['value']) for r in rules] == [('email', ADA)]
    assert [m['kind'] for m in _stored(db, s)] == ['unsubscribe']


def test_a_report_is_never_also_a_reply(db, monkeypatch, http):
    """A real delivery report that also quotes our send in ``In-Reply-To``: it would match as a reply."""
    s = _setup(db)
    report = dsn(headers='In-Reply-To: <m0@acme.test>\r\nReferences: <m0@acme.test>\r\n')
    gmail = Gmail(monkeypatch, http, s, {'dsn': report})

    gmail.push(db)

    assert gmail.fetched == ['dsn']  # read as a report, once
    assert _stored(db, s) == [] and _target(db, s) == 'active'


def test_mail_from_the_mailbox_itself_is_no_reply(db, monkeypatch, http):
    s = _setup(db)  # the mailbox is me@acme.test
    gmail = Gmail(monkeypatch, http, s, {'own': _mail('me@acme.test')})

    assert gmail.push(db) == 0
    assert gmail.fetched == [] and _stored(db, s) == [] and _target(db, s) == 'active'


def test_a_reply_quoting_the_id_the_provider_gave_the_send_is_matched(db, monkeypatch, http):
    """Gmail and Exchange may replace our Message-ID: the recipient quotes theirs, from an address of their own."""
    s = _setup(db)
    db.execute(
        "UPDATE outreach_campaign_sends SET message_id = '<ours@acme.test>', provider_message_id = '<given@mail.gmail.com>'"
    )
    gmail = Gmail(monkeypatch, http, s, {'alias': _mail('ada.home@else.test', refs='<given@mail.gmail.com>')})

    assert gmail.push(db) == 1
    assert _target(db, s) == 'replied' and [m['kind'] for m in _stored(db, s)] == ['reply']


def _second_organization(db, s) -> dict:
    """The owner of ``s`` also works in another organization, whose campaign writes to Ada too."""
    boss = _user(db)
    org, pid = member(db, boss).org_id, project(db, boss)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, s['uid'], 'member'),
    )
    campaign = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " VALUES (%s, %s, %s, 'Other', 'active') RETURNING id",
        (org, pid, s['uid']),
    ).fetchone()['id']
    target = db.execute(
        """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, status, next_step)
           VALUES (%s, %s, %s, 'Ada', 'active', 2) RETURNING id""",
        (campaign, s['uid'], ADA),
    ).fetchone()['id']
    return {'org': org, 'project': pid, 'target': target}


def test_an_opt_out_read_from_the_mailbox_stops_and_excludes_the_person_in_every_organization(db, monkeypatch, http):
    s = _setup(db)
    other = _second_organization(db, s)
    gmail = Gmail(monkeypatch, http, s, {'optout': _mail(text='no grazie, non scrivetemi piu')})

    assert gmail.push(db) == 1

    assert _target(db, s) == 'replied'
    assert (
        db.execute('SELECT status FROM outreach_campaign_targets WHERE id = %s', (other['target'],)).fetchone()[
            'status'
        ]
        == 'replied'
    )
    excluded = {
        r['project_id']
        for r in db.execute("SELECT project_id::text FROM exclusions WHERE kind = 'email' AND value = %s", (ADA,))
    }
    assert excluded == {s['project'], other['project']}


class Graph:
    """The mailbox's stand-in Graph: ``messages`` id to raw bytes; ``fetched`` lists the ids read through ``$value``."""

    def __init__(self, monkeypatch, http, db, s: dict, messages: dict[str, EmailMessage]):
        self.s, self.fetched = s, []
        self.messages = {k: bytes(v) for k, v in messages.items()}
        mailbox = sender(db, s['uid'], 'microsoft', account_label='me@acme.test', granted_scopes=list(MICROSOFT.scopes))
        db.execute('UPDATE outreach_campaign_sends SET sender_id = %s', (mailbox,))
        self.mailbox = mailbox

        async def token(mb, provider):
            return 'tok'

        monkeypatch.setattr(dispatcher, '_access_token', token)
        http.handler = self._serve

    def _serve(self, r: httpx.Request) -> httpx.Response:
        path = r.url.path
        if path.endswith('/$value'):
            mid = path.rsplit('/', 2)[-2]
            self.fetched.append(mid)
            return httpx.Response(200, content=self.messages[mid])
        msg = message_from_bytes(self.messages[path.rsplit('/', 1)[-1]])
        headers = [{'name': k, 'value': str(v)} for k, v in msg.items()]
        return httpx.Response(
            200,
            json={
                'from': {'emailAddress': {'address': ADA}},
                'subject': msg['Subject'],
                'internetMessageHeaders': headers,
            },
        )

    def push(self, db, ids: list[str]) -> int:
        with acting_for(self.s['org'], 'mailbox push'):
            return asyncio.run(mailbox_push.read(self.mailbox, ids))


def test_a_reply_in_a_microsoft_mailbox_is_read_from_the_headers_and_an_unrelated_one_is_not_fetched(
    db, monkeypatch, http
):
    s = _setup(db)
    graph = Graph(
        monkeypatch,
        http,
        db,
        s,
        {
            'reply': _mail(
                auth='eur05.protection.outlook.com; dkim=pass header.d=acme.test; dmarc=pass action=none header.from=acme.test'
            ),
            'other': _mail('anna@client.test', refs='', mid='<o1@client.test>'),
        },
    )

    assert graph.push(db, ['reply', 'other']) == 1

    assert graph.fetched == ['reply']
    (stored,) = _stored(db, s)
    assert stored['kind'] == 'reply' and stored['sender_authenticated'] is True and _target(db, s) == 'replied'


def test_the_same_reply_arriving_through_resend_is_recorded_once(db, monkeypatch, http):
    from app.outreach import replies

    s = _setup(db)
    gmail = Gmail(monkeypatch, http, s, {'reply': _mail()})
    assert gmail.push(db) == 1
    with acting_for(s['org'], 'inbound reply'):
        again = replies.record(
            None,
            campaign_id=s['campaign'],
            channel='email',
            provider_message_id='resend-email-id',
            sender=ADA,
            subject='Re: Hi',
            text='Sounds good',
            authentication={'dmarc': 'pass'},
            rfc_message_id='<R1@acme.test>',
        )
    assert again['status'] == 'duplicate' and len(_stored(db, s)) == 1
