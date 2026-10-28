"""Web Push (#343) against real SQL: subscriptions, the SSRF gate, deliver() and the test route.

Needs TEST_DATABASE_URL; copies a throwaway database from the migrated api/db template. The push service is faked at
``push.webpush``: no test posts to a real one.
"""

import base64
import contextlib
import http.server
import json
import os
import threading
import uuid
from typing import ClassVar

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from pywebpush import WebPushException

from app.core.config import settings
from app.main import app
from app.notifications import push, routes
from app.notifications import service as n
from tests.conftest import italian_user, member, migrated_database, pool_on, project, scoped_conn, sign_in
from tests.test_tools_sql import _user as _new_user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: push SQL test NOT run')

FCM = 'https://fcm.googleapis.com/fcm/send/abc'
MOZILLA = 'https://updates.push.services.mozilla.com/wpush/v2/abc'
APPLE = 'https://web.push.apple.com/QZ'
KEYS = {'p256dh': 'BKey', 'auth': 'aSecret'}


class FakePushService:
    """``push.webpush``: records each request, answers the status set for its endpoint (default 201)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status: dict[str, int] = {}
        self.boom: Exception | None = None
        self.errors: dict[str, Exception] = {}
        self.open_conns = 0
        self.conns_during_send: list[int] = []

    def __call__(self, subscription_info, data=None, **kwargs):
        endpoint = subscription_info['endpoint']
        self.calls.append((endpoint, json.loads(data)))
        self.conns_during_send.append(self.open_conns)
        if self.boom:
            raise self.boom
        if endpoint in self.errors:
            raise self.errors[endpoint]
        code = self.status.get(endpoint, 201)
        resp = requests.Response()
        resp.status_code = code
        if code >= 400:
            raise WebPushException(f'push failed: {code}', response=resp)
        return resp


def _user(c) -> str:
    return italian_user(c, _new_user)


@pytest.fixture
def service(monkeypatch):
    fake = FakePushService()
    monkeypatch.setattr(push, 'webpush', fake)
    monkeypatch.setattr(settings, 'vapid_public_key', 'BPublic')
    monkeypatch.setattr(settings, 'vapid_private_key', 'private')
    monkeypatch.setattr(settings, 'vapid_subject', 'mailto:ops@zenbree.test')
    return fake


@pytest.fixture
def db(monkeypatch, service):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            scoped = scoped_conn(c)

            @contextlib.contextmanager
            def get_conn(timeout=None):
                service.open_conns += 1
                try:
                    with scoped(timeout) as conn:
                        yield conn
                finally:
                    service.open_conns -= 1

            for module in (routes, n, push):
                monkeypatch.setattr(module, 'get_conn', get_conn, raising=False)
            yield c
        finally:
            app.dependency_overrides.clear()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def test_a_redirecting_push_service_is_never_followed(monkeypatch):
    """The real ``_send_one`` and pywebpush against a local server that answers 307: only the first hop is hit."""

    class Redirect(http.server.BaseHTTPRequestHandler):
        hits: ClassVar[list[str]] = []

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            Redirect.hits.append(self.path)
            self.send_response(307 if self.path == '/a' else 201)
            self.send_header('Location', '/b')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *args):
            pass

    browser = ec.generate_private_key(ec.SECP256R1()).public_key()
    vapid = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, 'big')
    monkeypatch.setattr(settings, 'vapid_private_key', _b64(vapid))
    monkeypatch.setattr(settings, 'vapid_subject', 'mailto:ops@zenbree.test')
    server = http.server.HTTPServer(('127.0.0.1', 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sub = {
            'endpoint': f'http://127.0.0.1:{server.server_port}/a',
            'p256dh': _b64(browser.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)),
            'auth': _b64(os.urandom(16)),
        }
        assert push._send_one(sub, push.check_payload('it')) is None
    finally:
        server.shutdown()
    assert Redirect.hits == ['/a']


def _as(db, uid) -> TestClient:
    sign_in(app, member(db, uid))
    return TestClient(app)


def _put(client, endpoint=FCM):
    return client.put('/v1/notifications/push', json={'endpoint': endpoint, **KEYS})


def _owner(db, endpoint=FCM):
    row = db.execute('SELECT user_id::text FROM push_subscriptions WHERE endpoint = %s', (endpoint,)).fetchone()
    return row and row['user_id']


def _count(db) -> int:
    return db.execute('SELECT count(*) AS n FROM push_subscriptions').fetchone()['n']


def test_subscribe_upserts_by_endpoint_and_a_second_user_on_the_same_browser_takes_it_over(db):
    first, second = _user(db), _user(db)
    assert _put(_as(db, first)).status_code == 200
    assert _put(_as(db, first)).status_code == 200
    assert _count(db) == 1 and _owner(db) == first
    assert _put(_as(db, second)).status_code == 200
    assert _count(db) == 1 and _owner(db) == second
    assert _as(db, first).get('/v1/notifications/push').json()['subscribed'] == []
    assert _as(db, second).get('/v1/notifications/push').json()['subscribed'] == [FCM]


def test_another_user_who_only_knows_the_endpoint_cannot_take_it_over(db):
    owner, intruder = _user(db), _user(db)
    _put(_as(db, owner))
    res = _as(db, intruder).put('/v1/notifications/push', json={'endpoint': FCM, 'p256dh': 'BOther', 'auth': 'other'})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'NOTIFICATIONS.PUSH_TAKEN'
    row = db.execute('SELECT user_id::text, p256dh, auth FROM push_subscriptions').fetchone()
    assert row == {'user_id': owner, **KEYS}


@pytest.mark.parametrize(
    'endpoint',
    [
        'http://fcm.googleapis.com/fcm/send/abc',
        'https://internal.zenbree.test/hook',
        'https://169.254.169.254/latest/meta-data',
        'https://fcm.googleapis.com.evil.test/x',
        'https://evil.test/fcm.googleapis.com',
        'https://user:pw@fcm.googleapis.com/x',
        'https://fcm.googleapis.com:8443/x',
        'https://notify.windows.com/x',
        'ftp://fcm.googleapis.com/x',
        'not a url',
    ],
)
def test_endpoint_outside_the_push_services_is_a_422_and_never_stored(db, service, endpoint):
    res = _put(_as(db, _user(db)), endpoint)
    assert res.status_code == 422
    assert _count(db) == 0
    assert service.calls == []


@pytest.mark.parametrize(
    'endpoint', [FCM, MOZILLA, 'https://db5p.notify.windows.com/w/?token=x', 'https://web.push.apple.com/QZ']
)
def test_the_four_push_services_are_accepted(db, endpoint):
    assert _put(_as(db, _user(db)), endpoint).status_code == 200


def test_delete_removes_only_the_callers_subscription(db):
    mine, other = _user(db), _user(db)
    _put(_as(db, mine), FCM)
    _put(_as(db, other), MOZILLA)
    client = _as(db, mine)
    assert client.request('DELETE', '/v1/notifications/push', json={'endpoint': MOZILLA}).json() == {'deleted': 0}
    assert _owner(db, MOZILLA) == other
    assert client.request('DELETE', '/v1/notifications/push', json={'endpoint': FCM}).json() == {'deleted': 1}
    assert _owner(db) is None


def _deliver(db, uid, kind='low_credits', href='/billing', data=None):
    n.deliver(n.notify(db, uid, kind, href=href, data=data or {'credits': 90}, org_id=member(db, uid).org_id))


def test_deliver_pushes_only_what_the_browser_setting_allows(db, service):
    uid = _user(db)
    _put(_as(db, uid))
    _deliver(db, uid, 'low_credits')
    assert [(e, p['title'], p['href']) for e, p in service.calls] == [
        (FCM, 'Crediti in esaurimento', n.switch_href(member(db, uid).org_id, '/billing'))
    ]
    _deliver(db, uid, 'reply', data={'name': 'Ada', 'campaign': 'C'})
    _deliver(db, uid, 'payment_failed')
    assert len(service.calls) == 1
    saved = n.prefs(db, uid)
    n.save_prefs(db, uid, saved['events'], saved['digest'], True)
    _deliver(db, uid, 'low_credits')
    assert len(service.calls) == 1  # do-not-disturb holds it


def _english(db, uid):
    db.execute('UPDATE "user" SET locale = %s WHERE id = %s', ('en', uid))


def test_the_push_follows_the_users_locale(db, service):
    uid = _user(db)
    _english(db, uid)
    _put(_as(db, uid))
    _deliver(db, uid, 'low_credits')
    _deliver(db, uid, 'search_failed')
    assert [(p['title'], p['body']) for _, p in service.calls] == [
        ('Credits running low', 'Top up so searches and campaigns keep running.'),
        ('Search stopped', "A provider is not responding. You don't pay for what's missing."),
    ]


def test_the_push_names_the_event_and_never_carries_reply_text(db, service):
    uid = _user(db)
    _put(_as(db, uid))
    mid = db.execute(
        """INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id, body)
           VALUES (%s, %s, 'email', 'reply', %s, 'SECRET TEXT of the reply') RETURNING id::text""",
        (uid, project(db, uid), uuid.uuid4().hex),
    ).fetchone()['id']
    data = {'message_id': mid, 'name': 'Ada', 'label': 'interested', 'campaign': 'Q4'}
    notified = n.notify(db, uid, 'reply_urgent', href=f'/inbox?m={mid}', data=data, org_id=member(db, uid).org_id)
    assert notified.item['quote'] == 'SECRET TEXT of the reply'  # the panel item has it, the push must not
    n.deliver(notified)
    ((_, sent),) = service.calls
    assert set(sent) == {'id', 'title', 'body', 'href'}
    assert 'SECRET' not in json.dumps(sent) and 'Ada' in sent['body']


@pytest.mark.parametrize('code', [404, 410])
def test_a_gone_subscription_is_deleted_and_the_others_still_get_it(db, service, code):
    uid = _user(db)
    _put(_as(db, uid), FCM)
    _put(_as(db, uid), MOZILLA)
    service.status[FCM] = code
    _deliver(db, uid)
    assert {e for e, _ in service.calls} == {FCM, MOZILLA}
    assert _owner(db, FCM) is None and _owner(db, MOZILLA) == uid


@pytest.mark.parametrize('broken', [FCM, MOZILLA])
def test_an_unexpected_error_on_one_browser_still_sends_to_the_other(db, service, broken):
    uid = _user(db)
    _put(_as(db, uid), FCM)
    _put(_as(db, uid), MOZILLA)
    service.errors[broken] = KeyError('a library bug')
    _deliver(db, uid)
    assert {e for e, _ in service.calls} == {FCM, MOZILLA}
    assert _owner(db, FCM) == uid and _owner(db, MOZILLA) == uid


def test_no_pooled_connection_is_held_while_the_push_service_answers(db, service):
    uid = _user(db)
    client = _as(db, uid)
    _put(client, FCM)
    _put(client, MOZILLA)
    service.status[FCM] = 410
    _deliver(db, uid)
    assert client.post('/v1/notifications/push/test', json={'endpoint': MOZILLA}).json() == {'sent': True}
    assert service.conns_during_send == [0, 0, 0]
    assert _owner(db, FCM) is None


def test_a_server_error_keeps_the_subscription_and_deliver_never_raises(db, service):
    uid = _user(db)
    _put(_as(db, uid))
    service.status[FCM] = 500
    _deliver(db, uid)
    assert _owner(db) == uid
    service.boom = RuntimeError('network down')
    _deliver(db, uid)
    assert _owner(db) == uid


def test_test_route_has_nothing_to_send_without_a_subscription(db, service):
    uid, other = _user(db), _user(db)
    client = _as(db, uid)
    res = client.post('/v1/notifications/push/test', json={'endpoint': FCM})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'NOTIFICATIONS.PUSH_NO_SUBSCRIPTION'
    assert client.post('/v1/notifications/push/test', json={}).status_code == 422
    _put(_as(db, other), MOZILLA)
    assert _as(db, uid).post('/v1/notifications/push/test', json={'endpoint': MOZILLA}).status_code == 409
    assert service.calls == []


def test_test_route_selects_only_the_named_browser_and_deliver_selects_all(db, service):
    """#983: an empty endpoint is a browser the caller does not have, never "every browser"."""
    uid, other = _user(db), _user(db)
    _put(_as(db, other), APPLE)
    client = _as(db, uid)
    _put(client, FCM)
    _put(client, MOZILLA)
    res = client.post('/v1/notifications/push/test', json={'endpoint': ''})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'NOTIFICATIONS.PUSH_NO_SUBSCRIPTION'
    assert client.post('/v1/notifications/push/test', json={'endpoint': APPLE}).status_code == 409
    assert service.calls == []
    assert client.post('/v1/notifications/push/test', json={'endpoint': FCM}).json() == {'sent': True}
    assert [e for e, _ in service.calls] == [FCM]
    _deliver(db, uid)
    assert sorted(e for e, _ in service.calls[1:]) == sorted([FCM, MOZILLA])


def test_test_route_sends_to_this_browser_and_reports_a_refusal(db, service):
    uid = _user(db)
    client = _as(db, uid)
    _put(client, FCM)
    _put(client, MOZILLA)
    assert client.post('/v1/notifications/push/test', json={'endpoint': MOZILLA}).json() == {'sent': True}
    assert [e for e, _ in service.calls] == [MOZILLA]
    service.status[MOZILLA] = 410
    assert client.post('/v1/notifications/push/test', json={'endpoint': MOZILLA}).status_code == 409
    assert _owner(db, MOZILLA) is None
    service.status[FCM] = 500
    assert client.post('/v1/notifications/push/test', json={'endpoint': FCM}).status_code == 502
    assert _owner(db) == uid


def test_test_route_sends_in_the_users_locale(db, service):
    italian, english = _user(db), _user(db)
    _english(db, english)
    _put(_as(db, english), MOZILLA)
    _put(_as(db, italian), FCM)
    assert _as(db, english).post('/v1/notifications/push/test', json={'endpoint': MOZILLA}).json() == {'sent': True}
    assert _as(db, italian).post('/v1/notifications/push/test', json={'endpoint': FCM}).json() == {'sent': True}
    assert [(e, p['title'], p['body']) for e, p in service.calls] == [
        (MOZILLA, 'Notifications on', 'This is a test alert from Zenbree.'),
        (FCM, 'Notifiche attive', 'Questo è un avviso di prova da Zenbree.'),
    ]


def test_without_keys_push_is_inert(db, service, monkeypatch):
    uid = _user(db)
    client = _as(db, uid)
    _put(client)
    monkeypatch.setattr(settings, 'vapid_private_key', '')
    assert client.get('/v1/notifications/push').json()['public_key'] is None
    _deliver(db, uid)
    assert service.calls == []
    assert _put(client, MOZILLA).status_code == 503
    assert client.post('/v1/notifications/push/test', json={'endpoint': FCM}).status_code == 503
