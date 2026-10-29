"""HubSpot webhook route (#120): v3 signature, event parsing, queueing. No DB, no HubSpot."""

import base64
import hmac
import json
import time
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.crm import webhooks
from app.main import app

SECRET = 'hs-secret'
URL = 'https://api.zenbree.test/v1/webhooks/hubspot'
EVENTS = [
    {'subscriptionType': 'deal.propertyChange', 'portalId': 4242, 'objectId': 55, 'propertyName': 'dealstage'},
    {'subscriptionType': 'contact.propertyChange', 'portalId': 4242, 'objectId': 7},
    {'subscriptionType': 'contact.propertyChange', 'portalId': 4242, 'objectId': 7},  # same record twice
    {'subscriptionType': 'ticket.creation', 'portalId': 4242, 'objectId': 9},  # not ours
    {'subscriptionType': 'company.propertyChange', 'portalId': 4242},  # no record
    'junk',
]
PARSED = [
    {'portal': '4242', 'kind': 'deal', 'id': '55'},
    {'portal': '4242', 'kind': 'contact', 'id': '7'},
]


def _sign(body: bytes, timestamp: str, *, secret=SECRET, method='POST', uri=URL) -> str:
    signed = method.encode() + uri.encode() + body + timestamp.encode()
    return base64.b64encode(hmac.new(secret.encode(), signed, sha256).digest()).decode()


def _now() -> str:
    return str(int(time.time() * 1000))


def test_signature_accepts_only_a_fresh_untampered_delivery():
    body, ts = b'[{"objectId":1}]', '1790000000000'

    def ok(**over):
        args = {
            'secret': SECRET,
            'method': 'POST',
            'uri': URL,
            'body': body,
            'timestamp': ts,
            'signature': _sign(body, ts),
            'now_ms': 1790000000000 + 1000,
        }
        return webhooks.verify_signature(**{**args, **over})

    assert ok()
    assert not ok(body=b'[{"objectId":2}]')  # tampered
    assert not ok(secret='other')
    assert not ok(uri=URL + '?x=1')  # signed for another URL
    assert not ok(now_ms=1790000000000 + webhooks.MAX_AGE_MS + 1)  # replayed after five minutes
    assert not ok(signature='é') and not ok(timestamp='²')
    assert not ok(timestamp='soon') and not ok(signature='') and not ok(secret='')


def test_parse_events_keeps_contact_company_and_deal_records_once():
    assert webhooks.parse_events(EVENTS) == PARSED
    assert webhooks.parse_events({'not': 'a list'}) == []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, 'hubspot_oauth_client_secret', SECRET)
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.zenbree.test')
    queued: list[list[dict]] = []
    monkeypatch.setattr(webhooks, '_enqueue', queued.append)
    return TestClient(app), queued


def _post(tc, body: bytes, *, signature=None, timestamp=None):
    timestamp = timestamp or _now()
    headers = {
        'X-HubSpot-Signature-v3': signature if signature is not None else _sign(body, timestamp),
        'X-HubSpot-Request-Timestamp': timestamp,
        'Content-Type': 'application/json',
    }
    return tc.post('/v1/webhooks/hubspot', content=body, headers=headers)


def test_signed_delivery_is_queued(client):
    tc, queued = client
    res = _post(tc, json.dumps(EVENTS).encode())
    assert res.status_code == 200 and res.json() == {'received': 2}
    assert queued == [PARSED]
    # Nothing of ours in the delivery: acknowledged, nothing queued.
    assert _post(tc, b'[]').json() == {'received': 0} and len(queued) == 1


def test_unsigned_forged_or_stale_delivery_is_refused(client):
    tc, queued = client
    body = json.dumps(EVENTS).encode()
    assert _post(tc, body, signature='forged').status_code == 401
    assert _post(tc, body, signature=_sign(body, _now(), secret='attacker')).status_code == 401
    old = str(int(time.time() * 1000) - webhooks.MAX_AGE_MS - 1000)
    assert _post(tc, body, timestamp=old).status_code == 401
    assert queued == []


def test_bad_body_unconfigured_and_queue_down(client, monkeypatch):
    tc, queued = client
    assert _post(tc, b'not json').status_code == 400
    big = b'[' + b' ' * webhooks.MAX_BODY_BYTES + b']'
    assert _post(tc, big).status_code == 413

    def down(events):
        raise ConnectionError('redis')

    monkeypatch.setattr(webhooks, '_enqueue', down)
    assert _post(tc, json.dumps(EVENTS).encode()).status_code == 503  # HubSpot retries
    monkeypatch.setattr(settings, 'hubspot_oauth_client_secret', '')
    assert _post(tc, b'[]').status_code == 503
    assert queued == []
