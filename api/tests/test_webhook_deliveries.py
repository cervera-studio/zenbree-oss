"""The delivery history of the outbound webhooks (#943): what is recorded, what is never recorded, who reads it."""

import json

import httpx
from celery.exceptions import SoftTimeLimitExceeded

from app.core.errors import ok
from app.core.security.ssrf_validator import SSRFBlockedError
from app.integrations import webhook_deliveries as deliveries
from app.integrations import webhooks
from app.worker import deliver_webhooks
from tests.conftest import call_for, member
from tests.test_agent import _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_webhooks import _endpoint, _fire, _payload
from tests.test_webhooks import fernet_key as fernet_key
from tests.test_webhooks import receiver as receiver
from tests.test_webhooks import retries as retries

URL = '/v1/integrations/webhooks'


def test_a_status_or_a_reason_is_a_failure_category():
    assert [deliveries.categorize(s) for s in (200, 204, 301, 400, 404, 410, 429, 500, 503)] == [
        None, None, 'rejected', 'rejected', 'rejected', 'gone', 'rate_limited', 'server_error', 'server_error',
    ]  # fmt: skip
    assert [deliveries.categorize(None, why) for why in ('timeout', 'blocked', 'unreachable', None)] == [
        'timeout', 'blocked', 'unreachable', 'unreachable',
    ]  # fmt: skip


def test_no_answer_says_why(receiver):
    secret = webhooks.new_secret()
    for failure, why in (
        (httpx.ConnectTimeout('slow'), 'timeout'),
        (SoftTimeLimitExceeded(), 'timeout'),
        (SSRFBlockedError('SSRF blocked', request=httpx.Request('POST', 'https://x.test')), 'blocked'),
        (httpx.ConnectError('dns'), 'unreachable'),
    ):
        receiver.status = failure
        made = webhooks.attempt_delivery(url='https://hooks.test/x', secret=secret, payload={'type': 'x'})
        assert (made.status, made.no_answer) == (None, why)


def _history(db, endpoint):
    return db.execute(
        'SELECT event_id, event_type, attempt, status_code, category, will_retry FROM webhook_deliveries'
        ' WHERE webhook_id = %s ORDER BY attempted_at, attempt',
        (endpoint,),
    ).fetchall()


@needs_db
def test_each_attempt_is_recorded_with_the_stable_event_id(db, receiver, retries):
    owner = _user(db)
    org = member(db, owner).org_id
    endpoint = _endpoint(db, owner)
    payload = _payload()

    receiver.status = 503
    _fire(org, endpoint, payload)
    _fire(org, endpoint, payload, attempt=1)
    _fire(org, endpoint, payload, attempt=len(webhooks.RETRY_DELAYS_S))  # the last one: nothing queued after it
    receiver.status = 200
    _fire(org, endpoint, payload, attempt=2)
    receiver.status = 400
    _fire(org, endpoint, _payload() | {'timestamp': 'u'})

    rows = _history(db, endpoint)
    assert [(r['attempt'], r['status_code'], r['category'], r['will_retry']) for r in rows] == [
        (1, 503, 'server_error', True),
        (2, 503, 'server_error', True),
        (4, 503, 'server_error', False),
        (3, 200, None, False),
        (1, 400, 'rejected', False),
    ]
    assert len({r['event_id'] for r in rows[:4]}) == 1  # one event, one id across its retries
    assert rows[4]['event_id'] != rows[0]['event_id'] and rows[4]['event_type'] == 'campaign.finished'
    assert rows[0]['event_id'] == webhooks.event_id_of(payload)


@needs_db
def test_no_answer_is_recorded_and_a_skipped_delivery_is_not(db, receiver, retries):
    owner = _user(db)
    org = member(db, owner).org_id
    endpoint = _endpoint(db, owner, events=('list.completed',))
    assert _fire(org, endpoint, _payload()) is None  # unsubscribed: no attempt was made
    assert _history(db, endpoint) == []

    other = _endpoint(db, owner)
    receiver.status = httpx.ConnectTimeout('slow')
    _fire(org, other, _payload())
    _fire(org, other, _payload(), attempt=1)
    assert [(r['status_code'], r['category'], r['will_retry']) for r in _history(db, other)] == [
        (None, 'timeout', True),
        (None, 'timeout', True),
    ]


@needs_db
def test_history_failing_never_stops_the_retry_or_the_revoke(db, receiver, retries, monkeypatch):
    owner = _user(db)
    org = member(db, owner).org_id
    endpoint = _endpoint(db, owner)

    def broken():
        raise ConnectionError('database down')

    monkeypatch.setattr(deliveries, 'get_conn', broken)
    receiver.status = 503
    _fire(org, endpoint, _payload())
    assert len(retries) == 1
    receiver.status = 410
    _fire(org, endpoint, _payload())
    assert db.execute('SELECT gone_at FROM user_webhooks WHERE id = %s', (endpoint,)).fetchone()['gone_at']


@needs_db
def test_only_the_newest_attempts_of_an_endpoint_are_kept(db, receiver, retries, monkeypatch):
    owner = _user(db)
    org = member(db, owner).org_id
    endpoint, other = _endpoint(db, owner), _endpoint(db, owner)
    monkeypatch.setattr(deliveries, 'KEPT_PER_ENDPOINT', 3)
    _fire(org, other, _payload())
    for i in range(5):
        _fire(org, endpoint, {**_payload(), 'timestamp': str(i)})
    kept = [r['event_id'] for r in _history(db, endpoint)]
    assert kept == [webhooks.event_id_of({**_payload(), 'timestamp': str(i)}) for i in (2, 3, 4)]
    assert len(_history(db, other)) == 1  # another endpoint's history is not pruned


@needs_db
def test_the_list_marks_a_test_and_holds_no_secret_url_or_payload(client, db, receiver, retries, monkeypatch):
    tc, uid = client
    org = member(db, uid).org_id
    secret = webhooks.new_secret()
    endpoint = _endpoint(db, uid, ('contact.replied',), url='https://hooks.test/token-in-path-9f8e', secret=secret)
    receiver.status = 503
    _fire(org, endpoint, {'type': 'contact.replied', 'timestamp': 't', 'data': {'from': 'ada@example.com'}})
    _fire(org, endpoint, {'type': webhooks.TEST_EVENT, 'timestamp': 't', 'data': {}})

    res = tc.get(f'{URL}/{endpoint}/deliveries')
    assert res.status_code == 200
    listed = res.json()['deliveries']
    assert [(d['event_type'], d['is_test'], d['will_retry']) for d in listed] == [
        ('webhook.test', True, False),  # a test is never retried
        ('contact.replied', False, True),
    ]
    assert set(listed[0]) == {
        'id', 'event_id', 'event_type', 'is_test', 'attempt', 'attempted_at', 'status_code', 'category', 'will_retry',
    }  # fmt: skip
    text = json.dumps(res.json())
    assert secret not in text and 'token-in-path' not in text and 'ada@example.com' not in text


@needs_db
def test_the_list_is_the_admins_of_the_endpoints_organization_only(client, db, receiver, retries):
    tc, uid = client
    mine = _endpoint(db, uid)
    other = _user(db)
    theirs = _endpoint(db, other)
    _fire(member(db, other).org_id, theirs, _payload())
    assert len(_history(db, theirs)) == 1  # the rows exist; another organization's admin must not read them
    assert tc.get(f'{URL}/{theirs}/deliveries').status_code == 404  # another organization's
    assert tc.get(f'{URL}/00000000-0000-0000-0000-000000000000/deliveries').status_code == 404
    assert tc.get(f'{URL}/{mine}/deliveries').json() == {'deliveries': []}
    tc.delete(f'{URL}/{mine}')
    assert tc.get(f'{URL}/{mine}/deliveries').status_code == 404  # deleted

    again = _endpoint(db, uid)
    db.execute('UPDATE member SET role = \'member\' WHERE "userId" = %s', (uid,))
    res = tc.get(f'{URL}/{again}/deliveries')
    assert res.status_code == 403 and res.json()['detail']['code'] == 'AUTH.ORG_ADMIN_ONLY'


@needs_db
def test_an_admins_delete_deletes_the_history(client, db, receiver, retries):
    tc, uid = client
    org = member(db, uid).org_id
    deleted, kept = _endpoint(db, uid), _endpoint(db, uid)
    for endpoint in (deleted, kept):
        _fire(org, endpoint, _payload())
    assert tc.delete(f'{URL}/{deleted}').status_code == 204
    assert _history(db, deleted) == [] and len(_history(db, kept)) == 1


@needs_db
def test_a_410_keeps_the_endpoint_and_its_history_and_stops_the_deliveries(client, db, receiver, retries, monkeypatch):
    monkeypatch.setattr(webhooks, 'validate_url', ok)  # no DNS in tests
    tc, uid = client
    org = member(db, uid).org_id
    gone = _endpoint(db, uid)
    receiver.status = 503
    _fire(org, gone, _payload())
    receiver.status = 410
    _fire(org, gone, _payload() | {'timestamp': 'u'})
    assert [(r['status_code'], r['category'], r['will_retry']) for r in _history(db, gone)] == [
        (503, 'server_error', True),
        (410, 'gone', False),
    ]
    listed = tc.get(URL).json()['webhooks']
    assert [(w['id'], w['gone_at'] is not None) for w in listed] == [(gone, True)]
    assert len(tc.get(f'{URL}/{gone}/deliveries').json()['deliveries']) == 2

    receiver.calls.clear()
    assert _fire(org, gone, _payload()) is None and receiver.calls == []  # a queued retry finds it stopped
    assert len(_history(db, gone)) == 2
    res = tc.post(f'{URL}/{gone}/test')
    assert res.status_code == 409 and res.json()['detail']['code'] == 'WEBHOOKS.REVOKED'
    for _ in range(webhooks.MAX_ENDPOINTS - 1):
        _endpoint(db, uid)
    assert (
        tc.post(URL, json={'url': 'https://h.test', 'label': 'L', 'event_names': ['list.completed']}).status_code == 409
    )
    assert tc.delete(f'{URL}/{gone}').status_code == 204  # the admin removes it, and its history goes
    assert _history(db, gone) == []


@needs_db
def test_a_delivery_that_ends_after_the_delete_writes_no_row(db, monkeypatch):
    owner = _user(db)
    org = member(db, owner).org_id
    endpoint = _endpoint(db, owner)

    def delete_while_posting(**_):
        webhooks._revoke(org, endpoint)  # the admin deletes while the POST is in flight
        return webhooks.Attempt(503, 'id', None)

    monkeypatch.setattr(webhooks, 'attempt_delivery', delete_while_posting)
    call_for(org, webhooks.fire, org, endpoint, _payload())
    assert _history(db, endpoint) == []


@needs_db
def test_a_retry_the_broker_refused_is_not_recorded_as_queued(db, receiver, monkeypatch):
    owner = _user(db)
    endpoint = _endpoint(db, owner)

    def refused(*_, **__):
        raise ConnectionError('broker down')

    monkeypatch.setattr(deliver_webhooks, 'apply_async', refused)
    receiver.status = 503
    _fire(member(db, owner).org_id, endpoint, _payload())
    assert [(r['category'], r['will_retry']) for r in _history(db, endpoint)] == [('server_error', False)]
