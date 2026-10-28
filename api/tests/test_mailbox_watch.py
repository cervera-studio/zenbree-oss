"""#747 review: what a send leaves for its bounces (the provider's own Message-ID) and stopping a watch on disconnect.
Provider APIs mocked, no database."""

import httpx
import pytest

from app.core.errors import ok
from app.integrations import mailbox_watch
from app.integrations.oauth_client import TokenGrant
from tests.test_mailbox_push import ORG, Conn, _mailbox, _use
from tests.test_mailbox_push import configured as configured

SEND = {
    'id': 'send-1',
    'campaign_id': 'camp-1',
    'organization_id': ORG,
    'recipient': ' Anna@Client.test ',
    'subject': 'Ciao Anna',
    'message_id': '<ours@acme.test>',
}


@pytest.fixture
def rechecked(monkeypatch):
    out: list = []
    monkeypatch.setattr(mailbox_watch.bounces, 'recheck', lambda campaign, org: out.append((campaign, org)))
    return out


async def test_gmail_stores_the_message_id_it_gave_the_send(monkeypatch, http, rechecked):
    """Gmail may replace the Message-ID we wrote: the one its bounces quote is read back from the sent message."""
    conn = _use(monkeypatch, Conn())
    headers = [{'name': 'Message-Id', 'value': '<CAgmail123@mail.gmail.com>'}]
    http.handler = lambda r: httpx.Response(200, json={'id': 'g1', 'payload': {'headers': headers}})

    await mailbox_watch.delivered(_mailbox(), 'tok', SEND, 'g1')

    (call,) = http.calls
    assert call.url.path.endswith('/messages/g1') and call.url.params['format'] == 'metadata'
    assert conn.updates == [('<CAgmail123@mail.gmail.com>', 'send-1')]
    assert rechecked == [('camp-1', ORG)]


def _anna(message_id: str, subject: str = 'Ciao Anna') -> dict:
    return {
        'internetMessageId': message_id,
        'subject': subject,
        'toRecipients': [{'emailAddress': {'address': 'anna@client.test'}}],
    }


async def test_graph_confirms_the_message_id_we_wrote_when_exchange_kept_it(monkeypatch, http, rechecked):
    conn = _use(monkeypatch, Conn())
    http.handler = lambda r: httpx.Response(200, json={'value': [{'internetMessageId': '<ours@acme.test>'}]})

    await mailbox_watch.delivered(_mailbox('microsoft'), 'tok', SEND, None)

    (call,) = http.calls
    assert call.url.params['$filter'] == "internetMessageId eq '<ours@acme.test>'" and call.url.params['$top'] == '1'
    assert conn.updates == [('<ours@acme.test>', 'send-1')]


async def test_graph_takes_the_one_sent_copy_of_this_send_when_exchange_replaced_the_id(monkeypatch, http, rechecked):
    """The newest copy to Anna can be an email written by hand (another subject); an earlier step is outside the
    window Graph is asked for. Only this send's copy is taken."""
    conn = _use(monkeypatch, Conn())
    window = [_anna('<hand@contoso.test>', 'Re: pranzo'), _anna('<exchange@contoso.test>')]

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.params['$filter'].startswith('internetMessageId'):
            return httpx.Response(200, json={'value': []})  # Exchange replaced ours
        assert r.url.params['$filter'].startswith('sentDateTime ge ') and r.url.params['$top'] == '5'
        return httpx.Response(200, json={'value': window})

    http.handler = handler
    await mailbox_watch.delivered(_mailbox('microsoft'), 'tok', SEND, None)
    assert conn.updates == [('<exchange@contoso.test>', 'send-1')]

    window.append(_anna('<twin@contoso.test>'))  # two copies alike: ambiguous, none taken
    await mailbox_watch.delivered(_mailbox('microsoft'), 'tok', SEND, None)
    assert conn.updates == [('<exchange@contoso.test>', 'send-1')]


async def test_no_sent_copy_yet_or_an_error_stores_nothing_and_the_send_still_counts(monkeypatch, http, rechecked):
    conn = _use(monkeypatch, Conn())
    http.handler = lambda r: httpx.Response(200, json={'value': []})
    await mailbox_watch.delivered(_mailbox('microsoft'), 'tok', SEND, None)

    def down(r):
        raise httpx.ConnectError('down', request=r)

    http.handler = down
    await mailbox_watch.delivered(_mailbox(), 'tok', SEND, 'g1')

    assert conn.updates == [] and rechecked == [('camp-1', ORG), ('camp-1', ORG)]


async def test_a_send_only_grant_reads_nothing_back(monkeypatch, http, rechecked):
    _use(monkeypatch, Conn())
    await mailbox_watch.delivered(
        _mailbox(granted_scopes=['https://www.googleapis.com/auth/gmail.send']), 'tok', SEND, 'g1'
    )
    assert http.calls == [] and rechecked == [('camp-1', ORG)]


@pytest.fixture
def refresh(monkeypatch):
    class Client:
        def __init__(self, provider):
            pass

        async def refresh(self, token):
            return ok(TokenGrant(access_token='fresh', refresh_token=None, granted_scopes=()))

    monkeypatch.setattr('app.integrations.oauth_client.OAuthClient', Client)
    monkeypatch.setattr(mailbox_watch.token_crypto, 'decrypt', lambda value: ok('refresh-1'))


async def test_a_disconnect_stops_the_gmail_watch_and_deletes_the_graph_subscription(monkeypatch, http, refresh):
    monkeypatch.setattr('app.integrations.member_release.has_live_sibling', lambda row: False)
    http.handler = lambda r: httpx.Response(204)
    live = {'inbound_subscription_expires_at': 'soon', 'refresh_token_encrypted': 'ct', 'provider_account_id': 'g'}

    await mailbox_watch.unwatch(_mailbox(**live))
    await mailbox_watch.unwatch(_mailbox('microsoft', inbound_subscription_id='sub-1', **live))

    stop, delete = http.calls
    assert stop.method == 'POST' and stop.url.path.endswith('/users/me/stop')
    assert delete.method == 'DELETE' and delete.url.path.endswith('/subscriptions/sub-1')
    assert stop.headers['authorization'] == 'Bearer fresh'


async def test_a_google_account_another_live_row_shares_keeps_its_watch(monkeypatch, http, refresh):
    monkeypatch.setattr('app.integrations.member_release.has_live_sibling', lambda row: True)
    live = {'inbound_subscription_expires_at': 'soon', 'refresh_token_encrypted': 'ct', 'provider_account_id': 'g'}
    await mailbox_watch.unwatch(_mailbox(**live))
    await mailbox_watch.unwatch(_mailbox())  # never watched
    assert http.calls == []
