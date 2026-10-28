"""Signed outbound webhooks (#93): signature recipe, delivery outcomes, and the organization-scoped API (#323).

Ported from the earlier implementation tests/unit/test_webhooks.py, test_webhooks_retry_rotate.py and
tests/contract/test_integrations_webhooks_api.py.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import ClassVar

import httpx
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from cryptography.fernet import Fernet
from structlog.testing import capture_logs

from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.core.errors import ok
from app.core.security import ssrf_validator
from app.core.system_reasons import SystemReason
from app.integrations import token_crypto, webhooks
from app.integrations.webhooks import _subscribed as real_subscribed
from app.outreach.inbound import verify_svix
from app.worker import celery_app, deliver_webhooks
from tests.conftest import call_as, call_for, call_system, member, project
from tests.test_agent import _chat, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db


def _reference_verify(secret: str, headers: dict[str, str], body: bytes) -> bool:
    """Standard Webhooks' reference algorithm, as the spec writes it: the key is the base64 after ``whsec_``,
    the header a space-separated list of ``v1,<sig>``."""
    key = base64.b64decode(secret.split('_', 1)[1])
    signed = f'{headers["webhook-id"]}.{headers["webhook-timestamp"]}.{body.decode()}'.encode()
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        hmac.compare_digest(sig.split(',', 1)[1], expected)
        for sig in headers['webhook-signature'].split()
        if sig.startswith('v1,')
    )


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
def receiver(monkeypatch):
    """Stands in for safe_httpx_post: answers ``receiver.status`` (an exception is raised) and records requests."""

    class Receiver:
        status: int | BaseException = 200
        calls: ClassVar[list[tuple[str, bytes, dict]]] = []

    def post(url, *, content, headers, timeout):
        Receiver.calls.append((url, content, headers))
        if isinstance(Receiver.status, BaseException):
            raise Receiver.status
        return httpx.Response(Receiver.status)

    Receiver.calls = []
    monkeypatch.setattr(webhooks, 'safe_httpx_post', post)
    return Receiver


@pytest.fixture
def retries(monkeypatch):
    queued: list[tuple[tuple, dict, int]] = []
    monkeypatch.setattr(
        deliver_webhooks, 'apply_async', lambda args, kwargs, countdown: queued.append((args, kwargs, countdown))
    )
    return queued


def test_receivers_verify_our_signature_the_standard_way(receiver):
    secret = webhooks.new_secret()
    assert secret.startswith('whsec_') and len(base64.b64decode(secret[6:])) == 32
    payload = {'type': 'sequence.stopped', 'timestamp': 't', 'data': {'reason': 'replied'}}
    assert webhooks.attempt_delivery(url='https://hooks.test/x', secret=secret, payload=payload).status == 200
    _, body, headers = receiver.calls[0]
    assert json.loads(body) == payload
    assert _reference_verify(secret, headers, body)
    # and the Svix verifier the inbound route already trusts for Resend
    check = {'msg_id': headers['webhook-id'], 'timestamp': headers['webhook-timestamp'], 'body': body}
    assert verify_svix(secret=secret, signature=headers['webhook-signature'], now=time.time(), **check)
    assert not _reference_verify(secret, headers, body + b' ')
    assert not _reference_verify(webhooks.new_secret(), headers, body)
    webhooks.attempt_delivery(url='https://hooks.test/x', secret=secret, payload=payload)
    assert receiver.calls[1][2]['webhook-id'] == headers['webhook-id']  # a retry can be deduped


def test_no_answer_past_the_deadline_and_private_hosts_are_none(receiver, monkeypatch):
    for failure in (httpx.ConnectTimeout('slow'), SoftTimeLimitExceeded()):
        receiver.status = failure
        assert (
            webhooks.attempt_delivery(
                url='https://hooks.test/x', secret=webhooks.new_secret(), payload={'type': 'x'}
            ).status
            is None
        )
    monkeypatch.setattr(webhooks, 'safe_httpx_post', ssrf_validator.safe_httpx_post)  # the real guard
    for url in ('https://127.0.0.1/hook', 'https://169.254.169.254/latest', 'https://localhost/x'):
        assert webhooks.attempt_delivery(url=url, secret=webhooks.new_secret(), payload={'type': 'x'}).status is None


def test_only_recoverable_failures_are_retried():
    assert [webhooks.is_retryable(s) for s in (None, 429, 500, 503, 200, 400, 404, 410)] == [True] * 4 + [False] * 4


def test_deliveries_have_their_own_queue():
    assert celery_app.amqp.router.route({}, 'deliver_webhooks')['queue'].name == 'webhooks'
    assert celery_app.amqp.router.route({}, 'run_job')['queue'].name == 'celery'
    assert deliver_webhooks.ignore_result and deliver_webhooks.soft_time_limit == webhooks.DELIVERY_DEADLINE_S


def test_emit_never_raises(monkeypatch):
    def down(*_):
        raise ConnectionError('broker down')

    monkeypatch.setattr(webhooks, '_subscribed', lambda event, data: [('org', 'endpoint')])
    monkeypatch.setattr(deliver_webhooks, 'delay', down)
    webhooks.emit('campaign.finished', {'campaign_id': 'c'})
    monkeypatch.setattr(webhooks, '_subscribed', down)
    webhooks.emit('campaign.finished', {'campaign_id': 'c'})


# ── SQL ────────────────────────────────────────────────────────────────────


def _endpoint(c, uid, events=('campaign.finished',), url='https://hooks.test/a', secret=None, org=None) -> str:
    """An endpoint ``uid`` made in ``org`` (default: their own organization)."""
    encrypted = token_crypto.encrypt(secret or webhooks.new_secret())
    return str(
        c.execute(
            'INSERT INTO user_webhooks (organization_id, user_id, url, secret_encrypted, event_names, label)'
            ' VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
            (org or member(c, uid).org_id, uid, url, encrypted.value, list(events), 'L'),
        ).fetchone()['id']
    )


def _campaign(c, uid, org=None) -> str:
    """A campaign ``uid`` made in ``org`` (default: their own), in that organization's first project."""
    org = org or member(c, uid).org_id
    return str(
        c.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name)'
            " SELECT %s, id, %s, 'C' FROM projects WHERE organization_id = %s ORDER BY created_at LIMIT 1"
            ' RETURNING id',
            (org, uid, org),
        ).fetchone()['id']
    )


def _payload(event='campaign.finished'):
    return {'type': event, 'timestamp': 't', 'data': {}}


@needs_db
def test_api_creates_lists_and_revokes_owned_endpoints(client, db, monkeypatch):
    tc, _ = client
    monkeypatch.setattr(webhooks, 'validate_url', ok)  # no DNS in tests
    body = {'url': 'https://hooks.test/zap', 'label': 'Zap', 'event_names': ['sequence.stopped', 'contact.replied']}
    res = tc.post('/v1/integrations/webhooks', json=body)
    assert res.status_code == 201
    created = res.json()
    assert created['secret'].startswith('whsec_') and created['event_names'] == ['contact.replied', 'sequence.stopped']
    stored = db.execute('SELECT secret_encrypted FROM user_webhooks').fetchone()['secret_encrypted']
    assert created['secret'] not in stored and token_crypto.decrypt(stored).value == created['secret']  # at rest

    listed = tc.get('/v1/integrations/webhooks').json()
    assert listed['events'] == ['list.completed', 'contact.replied', 'campaign.finished', 'sequence.stopped']
    assert [w['id'] for w in listed['webhooks']] == [created['id']]
    assert 'secret' not in listed['webhooks'][0] and 'secret_encrypted' not in listed['webhooks'][0]

    other = _endpoint(db, _user(db))
    assert tc.delete(f'/v1/integrations/webhooks/{other}').status_code == 404  # another organization's
    assert tc.post(f'/v1/integrations/webhooks/{other}/test').status_code == 404
    assert tc.delete(f'/v1/integrations/webhooks/{created["id"]}').status_code == 204
    assert tc.delete(f'/v1/integrations/webhooks/{created["id"]}').status_code == 404
    assert tc.get('/v1/integrations/webhooks').json()['webhooks'] == []

    monkeypatch.setattr(settings, 'oauth_token_encryption_key', '')
    res = tc.post('/v1/integrations/webhooks', json=body)
    assert res.status_code == 503 and res.json()['detail']['code'] == 'WEBHOOKS.NOT_CONFIGURED'


@needs_db
def test_api_rejects_bad_urls_unknown_events_and_past_the_cap(client, db, monkeypatch):
    tc, uid = client
    base = {'label': 'L', 'event_names': ['list.completed']}
    for url in ('http://hooks.test/x', 'https://127.0.0.1/x', 'https://169.254.169.254/latest/meta-data'):
        res = tc.post('/v1/integrations/webhooks', json={**base, 'url': url})
        assert res.status_code == 422 and res.json()['detail']['code'] == 'WEBHOOKS.INVALID_URL', url
    for events in (['credits.low'], []):
        res = tc.post('/v1/integrations/webhooks', json={**base, 'url': 'https://h.test', 'event_names': events})
        assert res.status_code == 422
    monkeypatch.setattr(webhooks, 'validate_url', ok)
    for _ in range(webhooks.MAX_ENDPOINTS):
        _endpoint(db, uid)
    res = tc.post('/v1/integrations/webhooks', json={**base, 'url': 'https://hooks.test/x'})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'WEBHOOKS.CAP_REACHED'


def _emit_with(fn, *args) -> None:
    """An emit as ``System``, as an inbound reply emits (``replies.record``, after its filing ends). No row-level
    security narrows what ``_subscribed`` reads, so its own SQL must keep every other organization's endpoints out."""
    call_system(SystemReason.INBOUND_MAIL, fn, *args)


def _emit(event, data) -> None:
    _emit_with(webhooks.emit, event, data)


def _fire(org, *args, **kwargs):
    """The delivery task (app/worker.py ``deliver_webhooks``), an organization task of the endpoint's organization."""
    return call_for(org, webhooks.fire, org, *args, **kwargs)


@needs_db
def test_emit_queues_one_task_per_subscribed_live_endpoint_of_the_events_organization(db, monkeypatch):
    monkeypatch.setattr(webhooks, '_subscribed', real_subscribed)
    queued: list[tuple] = []
    monkeypatch.setattr(deliver_webhooks, 'delay', lambda *args: queued.append(args))
    uid, other, nobody = _user(db), _user(db), _user(db)
    for u in (uid, other, nobody):
        project(db, u)
    org = member(db, uid).org_id
    first, second = _endpoint(db, uid), _endpoint(db, uid, events=('campaign.finished', 'list.completed'))
    _endpoint(db, uid, events=('list.completed',))
    revoked = _endpoint(db, uid)
    db.execute('UPDATE user_webhooks SET revoked_at = now() WHERE id = %s', (revoked,))
    _endpoint(db, other)

    campaign = _campaign(db, uid)
    call_for(org, webhooks.emit, 'campaign.finished', {'campaign_id': campaign})  # the campaign beat's scope
    assert sorted(endpoint for _, endpoint, _ in queued) == sorted([first, second])
    assert {(o, p['type'], p['data']['campaign_id']) for o, _, p in queued} == {(org, 'campaign.finished', campaign)}
    _emit('campaign.finished', {'campaign_id': _campaign(db, nobody)})
    _emit('campaign.finished', {'campaign_id': str(uuid.uuid4())})  # gone: no organization
    assert len(queued) == 2  # no endpoint, no task


@needs_db
def test_a_members_events_reach_the_team_admins_endpoints_and_no_other_organizations(db, monkeypatch):
    """#323: Ada (admin) set up the team's endpoint; Bo (member) runs a campaign, grows a list and gets a reply.
    Each event reaches Ada's endpoint; Bo's own organization's endpoint, and a stranger's, never fire."""
    monkeypatch.setattr(webhooks, '_subscribed', real_subscribed)
    queued: list[tuple] = []
    monkeypatch.setattr(deliver_webhooks, 'delay', lambda *args: queued.append(args))
    ada, bo, eve = _user(db), _user(db), _user(db)
    team = member(db, ada).org_id
    project(db, ada)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (team, bo),
    )
    events = list(webhooks.EVENTS)
    teams = _endpoint(db, ada, events=events)
    _endpoint(db, bo, events=events)  # in Bo's own organization
    _endpoint(db, eve, events=events)

    campaign = _campaign(db, bo, org=team)
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity)'
        " SELECT organization_id, project_id, %s, id, 'L', 'person' FROM chat_sessions WHERE id = %s RETURNING id",
        (bo, _chat(db, ada)),
    ).fetchone()['id']
    message = db.execute(
        'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id, received_at)'
        " SELECT %s, project_id, 'email', 'reply', 'm1', now() FROM outreach_campaigns WHERE id = %s RETURNING id",
        (bo, campaign),
    ).fetchone()['id']
    call_for(team, webhooks.emit, 'campaign.finished', {'campaign_id': campaign})  # the campaign beat's scope
    call_as(  # the turn that grew the list runs as its member
        CurrentUser(id=str(bo), org_id=str(team), org_role='member'),
        webhooks.emit,
        'list.completed',
        {'list_id': str(lid)},
    )
    _emit('contact.replied', {'message_id': str(message), 'campaign_id': None})  # matched no campaign
    _emit_with(
        webhooks.sequence_stopped, [{'id': 't', 'campaign_id': campaign, 'email': 'a@x', 'linkedin_url': ''}], 'replied'
    )
    assert [(o, endpoint, p['type']) for o, endpoint, p in queued] == [
        (team, teams, 'campaign.finished'),
        (team, teams, 'list.completed'),
        (team, teams, 'contact.replied'),
        (team, teams, 'sequence.stopped'),
    ]


@needs_db
def test_fire_signs_with_the_stored_secret_and_skips_what_it_should(db, receiver, retries):
    uid, other = _user(db), _user(db)
    org = member(db, uid).org_id
    secret = webhooks.new_secret()
    endpoint = _endpoint(db, uid, url='https://hooks.test/wanted', secret=secret)
    unsubscribed = _endpoint(db, uid, events=('list.completed',))

    assert _fire(org, endpoint, _payload()) == 200
    url, body, headers = receiver.calls[0]
    assert url == 'https://hooks.test/wanted' and _reference_verify(secret, headers, body)
    assert _fire(org, unsubscribed, _payload()) is None  # unsubscribed since it was queued
    assert _fire(member(db, other).org_id, endpoint, _payload()) is None  # another organization's
    assert len(receiver.calls) == 1 and retries == []


@needs_db
def test_failures_retry_with_backoff_and_gone_revokes(db, receiver, retries):
    owner = _user(db)
    uid = member(db, owner).org_id  # deliveries are keyed by the organization
    endpoint = _endpoint(db, owner)
    receiver.status = 503
    _fire(uid, endpoint, _payload())
    assert retries == [((uid, endpoint, _payload()), {'attempt': 1}, webhooks.RETRY_DELAYS_S[0])]
    _fire(uid, endpoint, _payload(), attempt=len(webhooks.RETRY_DELAYS_S))
    assert len(retries) == 1  # exhausted
    receiver.status = 400
    _fire(uid, endpoint, _payload())
    assert len(retries) == 1  # the request is wrong: resending cannot fix it
    receiver.status = 503
    _fire(uid, endpoint, {**_payload(), 'type': webhooks.TEST_EVENT})
    assert len(retries) == 1  # the test event is never retried

    receiver.status = 410
    _fire(uid, endpoint, _payload())
    assert db.execute('SELECT gone_at FROM user_webhooks WHERE id = %s', (endpoint,)).fetchone()['gone_at']
    assert _fire(uid, endpoint, _payload()) is None


@needs_db
def test_test_route_goes_through_the_webhooks_worker_and_ignores_subscriptions(client, db, receiver, monkeypatch):
    tc, uid = client
    endpoint = _endpoint(db, uid, events=('list.completed',))
    org = member(db, uid).org_id
    sent: list[dict] = []

    class Result:
        def __init__(self, value):
            self.value = value

        def get(self, timeout, propagate):
            return self.value

    def apply_async(args, ignore_result, expires):
        sent.append({'args': args, 'ignore_result': ignore_result, 'expires': expires})
        return Result(webhooks.fire(*args))

    monkeypatch.setattr(deliver_webhooks, 'apply_async', apply_async)
    receiver.status = 500
    res = tc.post(f'/v1/integrations/webhooks/{endpoint}/test')
    assert res.status_code == 200 and res.json() == {'status': 500}
    assert sent[0]['ignore_result'] is False and sent[0]['args'][:2] == (org, endpoint)
    assert sent[0]['expires'] == webhooks.DELIVERY_DEADLINE_S + 15  # moot once the caller stopped waiting
    assert json.loads(receiver.calls[0][1])['type'] == 'webhook.test'


@needs_db
def test_test_route_is_limited_per_user_and_a_refused_call_enqueues_nothing(client, db, monkeypatch):
    from app.core import rate_limit
    from app.worker import deliver_webhooks

    counts: dict = {}

    class Counter:
        def eval(self, script, n, key, seconds):
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        def set(self, *args, **kw):
            return True

        def delete(self, *args):
            return 1

    monkeypatch.setattr(rate_limit, '_redis', Counter)
    tc, uid = client
    endpoint = _endpoint(db, uid)
    sent: list = []

    class Result:
        def get(self, timeout, propagate):
            return 200

    monkeypatch.setattr(deliver_webhooks, 'apply_async', lambda *a, **kw: sent.append(a) or Result())
    for _ in range(10):
        assert tc.post(f'/v1/integrations/webhooks/{endpoint}/test').status_code == 200
    res = tc.post(f'/v1/integrations/webhooks/{endpoint}/test')
    assert res.status_code == 429 and res.json()['detail']['code'] == 'WEBHOOKS.RATE_LIMITED'
    assert res.headers['retry-after'] == '60' and len(sent) == 10


@needs_db
def test_tests_in_flight_are_capped_in_the_process_and_one_for_an_organization(client, db, monkeypatch):
    import threading

    from app.core import rate_limit
    from app.worker import deliver_webhooks

    held: set = set()

    class Redis:
        def eval(self, *args):
            return 1

        def set(self, key, value, nx, ex):
            if key in held:
                return None
            held.add(key)
            return True

        def delete(self, key):
            held.discard(key)

    monkeypatch.setattr(rate_limit, '_redis', Redis)
    tc, uid = client
    endpoint = _endpoint(db, uid)
    sent: list = []

    class Result:
        def get(self, timeout, propagate):
            return 200

    monkeypatch.setattr(deliver_webhooks, 'apply_async', lambda *a, **kw: sent.append(a) or Result())
    url = f'/v1/integrations/webhooks/{endpoint}/test'

    full = threading.BoundedSemaphore(1)
    full.acquire()  # the process already runs its share
    monkeypatch.setattr(webhooks, '_IN_FLIGHT', full)
    with capture_logs() as logs:
        busy = tc.post(url)
    assert busy.status_code == 429 and busy.json()['detail']['code'] == 'WEBHOOKS.TEST_BUSY' and sent == []
    assert [(e['event'], e['cap']) for e in logs if e['event'] == 'webhooks.test_busy'] == [
        ('webhooks.test_busy', 'process')
    ]

    free = threading.BoundedSemaphore(1)
    monkeypatch.setattr(webhooks, '_IN_FLIGHT', free)
    held.add(f'webhook_test:{member(db, uid).org_id}')  # a test of this organization is running
    assert tc.post(url).json()['detail']['code'] == 'WEBHOOKS.TEST_BUSY' and sent == []
    assert free.acquire(blocking=False)  # the refused call gave its place back
    free.release()

    held.clear()
    assert tc.post(url).status_code == 200 and len(sent) == 1
    assert held == set() and free.acquire(blocking=False)  # both released after the test
