"""#747: the push channels that read a mailbox's bounces. Provider APIs mocked, no database."""

import base64
import contextlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.core.config import settings
from app.integrations import mailbox_push, mailbox_watch
from app.integrations.providers import GOOGLE, MICROSOFT
from app.main import app
from app.outreach import dispatcher, mailbox_replies
from app.worker import celery_app
from tests.conftest import body
from tests.test_dsn import dsn

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
ORG, CONN = '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, 'gmail_push_topic', 'projects/zb/topics/gmail')
    monkeypatch.setattr(settings, 'gmail_push_token', 'push-secret')
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.zenbree.test/')
    monkeypatch.setattr(settings, 'outreach_reply_secret', 's3cret')


@pytest.fixture
def queued(monkeypatch):
    out: list = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args: out.append((name, args)))
    return out


class Conn:
    """The mailbox row a read loads, and every UPDATE the module runs."""

    def __init__(self, row=None):
        self.row, self.updates = row, []

    def execute(self, sql, params=()):
        if sql.startswith('UPDATE'):
            self.updates.append(params)
        return self

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [self.row] if self.row else []


def _mailbox(provider='google', **cols) -> dict:
    scopes = (GOOGLE if provider == 'google' else MICROSOFT).scopes
    return {
        'id': CONN,
        'organization_id': ORG,
        'provider': provider,
        'granted_scopes': list(scopes),
        'inbound_subscription_id': None,
        'inbound_subscription_expires_at': None,
        'inbound_history_id': None,
        'account_label': 'seller@example.com',
        **cols,
    }


def _use(monkeypatch, conn: Conn) -> Conn:
    for module in (mailbox_push, mailbox_watch):
        monkeypatch.setattr(module, 'get_conn', lambda: contextlib.nullcontext(conn))
    return conn


# ── routes ─────────────────────────────────────────────────────────────────


def _pubsub(address: str) -> dict:
    data = base64.b64encode(json.dumps({'emailAddress': address, 'historyId': 9}).encode()).decode()
    return {'message': {'data': data, 'messageId': '1'}, 'subscription': 'projects/zb/subscriptions/push'}


def test_a_gmail_push_queues_a_read_for_each_connection_of_the_address(monkeypatch, queued):
    seen: list[str] = []
    rows = [{'id': CONN, 'organization_id': ORG}]
    monkeypatch.setattr(mailbox_push, '_gmail_connections', lambda address: seen.append(address) or rows)
    client = TestClient(app)

    assert client.post('/v1/integrations/mailboxes/google/push?token=nope', json=_pubsub('a@b.test')).status_code == 401
    res = client.post('/v1/integrations/mailboxes/google/push?token=push-secret', json=_pubsub('Me@Acme.test'))

    assert res.status_code == 204 and seen == ['Me@Acme.test']
    assert queued == [(mailbox_push.TASK, [CONN, []])]
    # a malformed push is acknowledged, or Pub/Sub redelivers it forever
    assert client.post('/v1/integrations/mailboxes/google/push?token=push-secret', json={}).status_code == 204


def test_graph_checks_the_url_then_queues_only_signed_notifications(queued):
    client = TestClient(app)
    url = f'/v1/integrations/mailboxes/microsoft/push?c={CONN}&o={ORG}'

    check = client.post(url + '&validationToken=abc%20123')
    assert (
        check.status_code == 200 and check.text == 'abc 123' and check.headers['content-type'].startswith('text/plain')
    )
    assert check.headers['x-content-type-options'] == 'nosniff'
    # only Graph's empty-body check is answered, escaped: the URL is open to anyone
    assert client.post(url + '&validationToken=<b>x</b>').text == '&lt;b&gt;x&lt;/b&gt;'
    assert client.post(url + '&validationToken=abc', json={'value': []}).status_code == 401
    assert client.post(url + '&validationToken=' + 'a' * 513).status_code == 422

    forged = {'value': [{'clientState': 'guess', 'resourceData': {'id': 'm1'}}]}
    assert client.post(url, json=forged).status_code == 401 and queued == []
    signed = {'value': [{'clientState': mailbox_push.client_state(CONN, ORG), 'resourceData': {'id': 'm1'}}]}
    assert client.post(url, json=signed).status_code == 202
    assert queued == [(mailbox_push.TASK, [CONN, ['m1']])]
    # signed for another organization: not this one's
    assert client.post(f'/v1/integrations/mailboxes/microsoft/push?c={CONN}&o=other', json=signed).status_code == 401


# ── watching ───────────────────────────────────────────────────────────────


async def test_a_gmail_watch_starts_before_a_send_and_is_left_alone_while_fresh(monkeypatch, http):
    conn = _use(monkeypatch, Conn())
    expiry = int((NOW + timedelta(days=7)).timestamp() * 1000)
    http.handler = lambda r: httpx.Response(200, json={'historyId': '77', 'expiration': str(expiry)})
    mb = _mailbox()

    await mailbox_watch.keep_watching(mb, 'tok', NOW)

    (call,) = http.calls
    assert str(call.url) == f'{mailbox_push.GMAIL_API}/watch' and call.headers['authorization'] == 'Bearer tok'
    assert body(call) == {'topicName': 'projects/zb/topics/gmail', 'labelIds': ['INBOX']}
    assert conn.updates == [(None, NOW + timedelta(days=7), '77', CONN)]

    await mailbox_watch.keep_watching(mb, 'tok', NOW + timedelta(hours=12))  # renewed less than a day ago
    assert len(http.calls) == 1


async def test_a_mailbox_without_the_read_scope_is_never_watched(monkeypatch, http):
    _use(monkeypatch, Conn())
    await mailbox_watch.keep_watching(_mailbox(granted_scopes=[GOOGLE.send_scope]), 'tok', NOW)
    assert http.calls == []


async def test_graph_renews_its_subscription_or_makes_a_signed_one(monkeypatch, http):
    conn = _use(monkeypatch, Conn())
    http.handler = lambda r: httpx.Response(404 if r.method == 'PATCH' else 201, json={'id': 'sub-2'})

    await mailbox_watch.keep_watching(_mailbox('microsoft', inbound_subscription_id='sub-1'), 'tok', NOW)

    patch, post = http.calls
    assert patch.method == 'PATCH' and str(patch.url).endswith('/subscriptions/sub-1')
    made = body(post)
    assert made['notificationUrl'] == (
        f'https://api.zenbree.test/v1/integrations/mailboxes/microsoft/push?c={CONN}&o={ORG}'
    )
    assert made['clientState'] == mailbox_push.client_state(CONN, ORG) and made['changeType'] == 'created'
    assert made['resource'] == "me/mailFolders('inbox')/messages"
    assert conn.updates == [('sub-2', NOW + mailbox_watch.GRAPH_WATCH, None, CONN)]


# ── reading ────────────────────────────────────────────────────────────────


def _gmail(monkeypatch, http, messages: dict, *, broken: dict | None = None) -> tuple[Conn, list]:
    """A mailbox whose cursor is 70 and a stand-in Gmail whose history adds ``messages`` (id → raw bytes, or None for
    an ordinary mail), one history record each, numbered 71, 72, …; ``broken`` maps an id to the response its
    metadata or raw fetch gets instead. Returns the mailbox's connection and the bounces recorded."""
    conn = _use(monkeypatch, Conn(_mailbox(inbound_history_id='70')))

    async def token(mb, provider):
        return 'tok'

    monkeypatch.setattr(dispatcher, '_access_token', token)
    recorded: list = []
    monkeypatch.setattr(mailbox_push.bounces, 'record', lambda mailbox, b, **kw: recorded.append((mailbox, b)) or True)
    monkeypatch.setattr(mailbox_replies, 'match', lambda mailbox, sender, refs: None)  # no reply to any send
    monkeypatch.setattr(mailbox_push.bounces, 'may_refund', lambda mailbox, b: False)  # no paid re-verification
    records = [{'id': str(71 + i), 'messagesAdded': [{'message': {'id': m}}]} for i, m in enumerate(messages)]

    def handler(r: httpx.Request) -> httpx.Response:
        path, query = r.url.path, dict(r.url.params)
        if path.endswith('/history'):  # like Gmail: only the records after the start
            after = [h for h in records if int(h['id']) > int(query['startHistoryId'])]
            return httpx.Response(200, json={'history': after, 'historyId': '80'})
        message_id = path.rsplit('/', 1)[-1]
        if (failure := (broken or {}).get((message_id, query.get('format')))) is not None:
            return failure(r)
        raw = messages[message_id]
        if query.get('format') == 'raw':
            assert raw is not None  # an ordinary message is never fetched whole
            return httpx.Response(200, json={'raw': base64.urlsafe_b64encode(raw).decode().rstrip('=')})
        sender = 'anna@client.test' if raw is None else 'mailer-daemon@googlemail.com'
        return httpx.Response(
            200, json={'payload': {'headers': [{'name': 'From', 'value': sender}]}, 'sizeEstimate': 900}
        )

    http.handler = handler
    return conn, recorded


def _bounced(recorded) -> list[str]:
    return [b.recipient for _, b in recorded]


def _to(address: str) -> bytes:
    return dsn().replace(b'Gone@Prospect.example', address.encode())


async def test_gmail_reads_only_the_dsns_since_its_cursor_then_moves_it(monkeypatch, http):
    # #747: anyone can mail a bounce-shaped message to the mailbox; it is fetched, refused and never recorded
    forged = dsn(sender='mailer-daemon@attacker.test')
    conn, recorded = _gmail(monkeypatch, http, {'dsn': dsn(), 'mail': None, 'forged': forged})

    assert await mailbox_push.read(CONN, []) == 1

    assert [(m, b.recipient, b.kind) for m, b in recorded] == [(CONN, 'gone@prospect.example', 'hard')]
    assert conn.updates == [('80', CONN)]


async def test_a_message_the_parser_chokes_on_is_skipped_and_the_cursor_still_moves(monkeypatch, http):
    """#747: one poison message must not stop bounce reading for the mailbox: it is logged, skipped, and never read
    again, and the next real report is still recorded."""
    conn, recorded = _gmail(monkeypatch, http, {'poison': b'poison', 'next': _to('a@b.test')})
    real = mailbox_push.dsn.read

    def choke(raw, **kw):
        if raw == b'poison':
            raise RecursionError('maximum recursion depth exceeded')
        return real(raw, **kw)

    monkeypatch.setattr(mailbox_push.dsn, 'read', choke)
    with capture_logs() as logs:
        assert await mailbox_push.read(CONN, []) == 1

    assert _bounced(recorded) == ['a@b.test'] and conn.updates == [('80', CONN)]
    (unparsable,) = [e for e in logs if e['event'] == 'mailbox_push.dsn_unparsable']
    assert unparsable['message_id'] == 'poison' and unparsable['error_type'] == 'RecursionError'


async def test_a_provider_error_on_one_message_drops_none_of_the_others(monkeypatch, http):
    def refuse(r):
        raise httpx.ReadTimeout('slow', request=r)

    broken = {('m1', 'metadata'): lambda r: httpx.Response(500), ('m2', 'raw'): refuse}
    conn, recorded = _gmail(
        monkeypatch, http, {'m1': _to('a@b.test'), 'm2': _to('c@d.test'), 'm3': _to('e@f.test')}, broken=broken
    )

    assert await mailbox_push.read(CONN, []) == 1

    assert _bounced(recorded) == ['e@f.test'] and conn.updates == [('80', CONN)]


async def test_a_message_over_the_size_limit_is_never_fetched_or_parsed(monkeypatch, http):
    big = {
        'payload': {'headers': [{'name': 'From', 'value': 'mailer-daemon@googlemail.com'}]},
        'sizeEstimate': 5_000_000,
    }
    huge = httpx.Response(200, json={'raw': 'A' * (mailbox_push.MAX_DSN_BYTES * 2 + 10)})
    broken = {('big', 'metadata'): lambda r: httpx.Response(200, json=big), ('huge', 'raw'): lambda r: huge}
    conn, recorded = _gmail(monkeypatch, http, {'big': dsn(), 'huge': dsn(), 'ok': _to('a@b.test')}, broken=broken)

    assert await mailbox_push.read(CONN, []) == 1

    assert _bounced(recorded) == ['a@b.test'] and conn.updates == [('80', CONN)]
    assert not [c for c in http.calls if c.url.path.endswith('/big') and c.url.params.get('format') == 'raw']


async def test_a_read_out_of_time_stores_the_last_record_it_handled(monkeypatch, http):
    """Past the deadline the rest waits for the next push. Gmail lists the records after the stored id, so the cursor
    is the last record handled, never the first one left (that one would be skipped for good)."""
    conn, recorded = _gmail(monkeypatch, http, {'m1': _to('a@b.test'), 'm2': _to('c@d.test')})
    clock = iter([0.0, 1.0, 99.0])  # the deadline is set at 0; m1 starts at 1, m2 would start at 99
    monkeypatch.setattr(mailbox_push, '_now', lambda: next(clock))

    assert await mailbox_push.read(CONN, []) == 1
    assert _bounced(recorded) == ['a@b.test'] and conn.updates == [('71', CONN)]

    conn.row['inbound_history_id'] = '71'  # the next push
    monkeypatch.setattr(mailbox_push, '_now', lambda: 0.0)
    assert await mailbox_push.read(CONN, []) == 1
    assert _bounced(recorded) == ['a@b.test', 'c@d.test'] and conn.updates[-1] == ('80', CONN)


def _failing_store(monkeypatch, recorded, bad='c@d.test'):
    def record(mailbox, bounce, **kw):
        if bounce.recipient == bad:
            raise psycopg.OperationalError('the database went away')
        recorded.append((mailbox, bounce))
        return True

    monkeypatch.setattr(mailbox_push.bounces, 'record', record)


async def test_a_bounce_that_cannot_be_stored_stops_the_read_before_its_record(monkeypatch, http):
    """A matched bounce is never lost: the store error stops the read, the cursor stays on the last record done, and
    the task retries from there."""
    conn, recorded = _gmail(monkeypatch, http, {'m1': _to('a@b.test'), 'm2': _to('c@d.test'), 'm3': _to('e@f.test')})
    _failing_store(monkeypatch, recorded)

    with pytest.raises(mailbox_push.StoreFailed):
        await mailbox_push.read(CONN, [], last_try=False)

    assert _bounced(recorded) == ['a@b.test'] and conn.updates == [('71', CONN)]


async def test_on_the_last_try_a_store_error_is_logged_and_passed(monkeypatch, http):
    """An error that recurs on every try (a CHECK violation, a bug) must not pin the cursor forever."""
    conn, recorded = _gmail(monkeypatch, http, {'m1': _to('a@b.test'), 'm2': _to('c@d.test'), 'm3': _to('e@f.test')})
    _failing_store(monkeypatch, recorded)

    with capture_logs() as logs:
        assert await mailbox_push.read(CONN, [], last_try=True) == 2

    assert _bounced(recorded) == ['a@b.test', 'e@f.test'] and conn.updates == [('80', CONN)]
    (failed,) = [e for e in logs if e['event'] == 'mailbox_push.store_failed']
    assert failed['log_level'] == 'error' and failed['message_id'] == 'm2'


def test_the_task_retries_a_failed_store_then_gives_up(monkeypatch):
    from app.worker import celery_app

    task = celery_app.tasks[mailbox_push.TASK]
    assert mailbox_push.StoreFailed in task.autoretry_for and task.max_retries == mailbox_push.STORE_RETRIES


async def test_the_task_time_limit_is_never_swallowed(monkeypatch, http):
    conn, _ = _gmail(monkeypatch, http, {'m1': _to('a@b.test')})

    def out_of_time(raw, **kw):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(mailbox_push.dsn, 'read', out_of_time)
    with pytest.raises(SoftTimeLimitExceeded):
        await mailbox_push.read(CONN, [])
    assert conn.updates == []  # nothing handled: the next read starts where this one did


async def test_graph_out_of_time_queues_the_rest_as_a_read_of_their_own(monkeypatch, http, queued):
    _use(monkeypatch, Conn(_mailbox('microsoft')))

    async def token(mb, provider):
        return 'tok'

    monkeypatch.setattr(dispatcher, '_access_token', token)
    http.handler = lambda r: httpx.Response(404)  # each message gone: nothing to read
    clock = iter([0.0, 1.0, 99.0])
    monkeypatch.setattr(mailbox_push, '_now', lambda: next(clock))

    assert await mailbox_push.read(CONN, ['a', 'b', 'c']) == 0

    assert queued == [(mailbox_push.TASK, [CONN, ['b', 'c']])]


async def test_more_messages_than_a_read_takes_leave_the_cursor_on_the_last_record_read(monkeypatch, http):
    monkeypatch.setattr(mailbox_push, 'MAX_MESSAGES', 2)
    conn, recorded = _gmail(monkeypatch, http, {f'm{i}': _to(f'p{i}@b.test') for i in range(3)})
    pages = iter([0, 1])

    real = http.handler

    def paged(r: httpx.Request) -> httpx.Response:
        resp = real(r)
        if r.url.path.endswith('/history'):  # two records on the first page, one more on the next
            body = resp.json()
            page = next(pages)
            more = {'nextPageToken': 'p2'} if page == 0 else {}
            return httpx.Response(200, json={**body, 'history': body['history'][page * 2 : page * 2 + 2], **more})
        return resp

    http.handler = paged
    assert await mailbox_push.read(CONN, []) == 2
    assert _bounced(recorded) == ['p0@b.test', 'p1@b.test']
    assert conn.updates == [('72', CONN)]  # m2 waits for the next push, not skipped


# ── the refund's re-verification (owner decision on #919) ─────────────────────


@pytest.fixture
def reverify(monkeypatch, http, vendor_db):
    """A Gmail read of one hard bounce of a@b.test that could refund, with MillionVerifier keyed: ``answer`` sets what
    MillionVerifier says (a JSON body, or an exception to raise); returns the read and the refund flags recorded."""
    monkeypatch.setattr(settings, 'millionverifier_api_key', 'mv-key')
    _gmail(monkeypatch, http, {'m1': _to('a@b.test')})
    monkeypatch.setattr(mailbox_push.bounces, 'may_refund', lambda mailbox, bounce: True)
    flags: list[bool] = []
    monkeypatch.setattr(
        mailbox_push.bounces, 'record', lambda mailbox, b, reverified_invalid: flags.append(reverified_invalid) or True
    )
    gmail = http.handler
    answer: dict = {'body': {'result': 'invalid'}}

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host != 'api.millionverifier.com':
            return gmail(r)
        if isinstance(answer['body'], Exception):
            raise answer['body']
        return httpx.Response(200, json=answer['body'])

    http.handler = handler

    async def read(body):
        answer['body'] = body
        assert await mailbox_push.read(CONN, []) == 1  # the bounce is kept whatever the verifier says
        return flags[-1]

    return read


async def test_an_address_millionverifier_calls_invalid_is_refunded(reverify, http):
    assert await reverify({'result': 'invalid'}) is True
    (asked,) = [c for c in http.calls if c.url.host == 'api.millionverifier.com']
    assert asked.url.params['email'] == 'a@b.test'


@pytest.mark.parametrize('result', ['ok', 'catch_all', 'unknown'])
async def test_an_address_millionverifier_does_not_call_invalid_is_not_refunded(reverify, result):
    assert await reverify({'result': result}) is False


async def test_a_verifier_error_refunds_nothing_and_keeps_the_bounce(reverify):
    assert await reverify({'result': '', 'error': 'Insufficient credits'}) is False
    assert await reverify(httpx.ConnectError('down')) is False


async def test_the_verifier_switched_off_refunds_nothing(reverify, http, kill_switch):
    kill_switch.add('vendor_disabled:millionverifier')
    assert await reverify({'result': 'invalid'}) is False
    assert not [c for c in http.calls if c.url.host == 'api.millionverifier.com']


async def test_a_bounce_that_cannot_refund_is_never_reverified(reverify, http, monkeypatch):
    monkeypatch.setattr(mailbox_push.bounces, 'may_refund', lambda mailbox, bounce: False)
    assert await reverify({'result': 'invalid'}) is False
    assert not [c for c in http.calls if c.url.host == 'api.millionverifier.com']
