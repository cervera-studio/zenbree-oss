"""The inbound webhook trigger of automations (#1031) against real SQL: the signed route, the ledger of calls, the beat
that starts one run for each accepted call, and the run that works on that one person."""

import json
import os
import re
import shutil
import threading
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from structlog.testing import capture_logs

from app.automations import approval, deliveries, person_match, runner
from app.core import rate_limit
from app.core.config import settings
from app.core.migrate import MIGRATIONS, migrate
from app.core.scope import acting_as, acting_for
from app.core.system_reasons import SystemReason
from app.integrations.webhooks import sign
from app.lists.rows import dedupe_key
from app.main import app
from tests.automations_support import client, database, make_campaign, make_list, teammate, user
from tests.conftest import call_system, empty_database, member, migrated_database, pool_on, project, wait_for_a_lock

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')
db = pytest.fixture(database)
MIGRATION = '20261018194807_automation_webhooks.sql'


@pytest.fixture(autouse=True)
def settings_for_hooks(monkeypatch):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.test')
    monkeypatch.setattr(rate_limit, 'over_limit', lambda *_: False)


def _person(i: int, **cells) -> dict:
    return {'full_name': f'Person {i}', 'email': f'p{i}@acme.it', 'email_status': 'verified', **cells}


class Hooked:
    """A user, their person list (rows keyed as the list keys them), an active campaign, and an enabled webhook
    automation that adds the called person to it. ``approved``: its version needs no review."""

    def __init__(self, db, monkeypatch, people, *, approved=True, steps=None, credits=200):
        self.db = db
        self.uid = user(db, credits=credits)
        self.org = member(db, self.uid).org_id
        self.list_id, self.rows = make_list(db, self.uid, 'person', people)
        for row_id, cells in zip(self.rows, people, strict=True):
            db.execute('UPDATE list_rows SET dedupe_key = %s WHERE id = %s', (dedupe_key('person', cells), row_id))
        self.campaign = make_campaign(db, self.uid, status='active')
        self.api = client(db, monkeypatch, self.uid)
        self.public = TestClient(app)
        made = self.api.post(
            '/v1/automations',
            json={
                'name': 'Inbound',
                'credit_cap': 10,
                'trigger': {'kind': 'webhook'},
                'definition': {
                    'list_id': self.list_id,
                    'steps': steps or [{'type': 'add_to_campaign', 'campaign_id': self.campaign}],
                },
            },
        )
        assert made.status_code == 201, made.text
        self.id = made.json()['id']
        if approved:
            db.execute('UPDATE automations SET approved_version = version WHERE id = %s', (self.id,))
        self.secret, self.url = self.rotate()

    def rotate(self) -> tuple[str, str]:
        got = self.api.post(f'/v1/automations/{self.id}/webhook/secret')
        assert got.status_code == 200, got.text
        assert got.headers['cache-control'] == 'no-store'
        return got.json()['secret'], got.json()['url']

    @property
    def path(self) -> str:
        return self.url.removeprefix('https://api.test')

    def call(self, body, webhook_id='msg_1', *, secret=None, path=None, timestamp=None, headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        signed_at = int(time.time()) if timestamp is None else timestamp
        signature = sign(secret=secret or self.secret, webhook_id=webhook_id, timestamp=signed_at, body=raw)
        sent = {'webhook-id': webhook_id, 'webhook-timestamp': str(signed_at), 'webhook-signature': signature}
        return self.public.post(path or self.path, content=raw, headers=headers if headers is not None else sent)

    def deliveries(self) -> list[dict]:
        return self.db.execute(
            'SELECT * FROM automation_deliveries WHERE automation_id = %s ORDER BY received_at', (self.id,)
        ).fetchall()

    def delivery(self, webhook_id) -> dict:
        return self.db.execute(
            'SELECT * FROM automation_deliveries WHERE automation_id = %s AND webhook_id = %s', (self.id, webhook_id)
        ).fetchone()

    def pickup(self) -> int:
        return call_system(SystemReason.DUE_WORK, deliveries.pickup)

    def runs(self) -> list[dict]:
        return self.db.execute(
            'SELECT * FROM automation_runs WHERE automation_id = %s ORDER BY created_at', (self.id,)
        ).fetchall()

    async def work(self, run_id) -> dict:
        with acting_as(self.org, self.uid):
            await runner.run_automation(run_id)
        return self.db.execute('SELECT * FROM automation_runs WHERE id = %s', (run_id,)).fetchone()

    def worked(self) -> set[str]:
        rows = self.db.execute('SELECT row_id::text FROM automation_rows WHERE automation_id = %s', (self.id,))
        return {r['row_id'] for r in rows}

    def patch(self, **body):
        return self.api.patch(f'/v1/automations/{self.id}', json=body)

    def shown(self) -> dict:
        return next(a for a in self.api.get('/v1/automations').json()['automations'] if a['id'] == self.id)


def _code(response) -> str:
    return response.json()['detail']['code']


@pytest.fixture
def before(tmp_path):
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name < MIGRATION:
            shutil.copy(path, tmp_path)
    with empty_database() as c:
        migrate(c, tmp_path)
        yield c, tmp_path


def _check_kinds(c, constraint: str) -> set[str]:
    definition = c.execute(
        'SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = %s', (constraint,)
    ).fetchone()['d']
    return set(re.findall(r"'(\w+)'::text", definition))


def test_the_migration_widens_both_trigger_checks_over_the_existing_rows(before):
    c, directory = before
    uid = user(c)
    org = member(c, uid).org_id
    list_id, _ = make_list(c, uid, 'person')
    definition = Jsonb({'list_id': list_id, 'steps': [{'type': 'find_emails'}]})
    for kind, cadence in (('manual', None), ('schedule', 'daily')):
        automation = c.execute(
            'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, cadence, credit_cap,'
            " definition) VALUES (%s, %s, %s, 'A', %s, %s, 5, %s) RETURNING id",
            (org, project(c, uid), uid, kind, cadence, definition),
        ).fetchone()['id']
        c.execute(
            'INSERT INTO automation_runs (organization_id, automation_id, version, definition, credit_cap, trigger_kind,'
            ' scheduled_for) VALUES (%s, %s, 1, %s, 5, %s, now())',
            (org, automation, definition, kind),
        )

    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]

    for constraint in ('automations_trigger_kind_check', 'automation_runs_trigger_kind_check'):
        assert _check_kinds(c, constraint) == {'manual', 'schedule', 'webhook'}
    assert c.execute('SELECT count(*) AS n FROM automation_runs WHERE target_row_id IS NULL').fetchone()['n'] == 2
    with pytest.raises(Exception, match='automation_runs_webhook_has_target'):
        c.execute(
            "UPDATE automation_runs SET trigger_kind = 'webhook' WHERE automation_id = %s",
            (automation,),
        )


def test_the_secret_is_shown_once_and_a_new_one_voids_the_old_at_once(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1)])
    assert hooked.url.startswith('https://api.test/v1/automation-hooks/')
    assert hooked.id not in hooked.url  # the URL names a random hook id
    listed = hooked.api.get('/v1/automations')
    assert hooked.secret not in listed.text
    assert hooked.shown()['webhook'] | {'rotated_at': None} == {
        'url': hooked.url,
        'rotated_at': None,
        'pending': 0,
        'waiting_for_approval': False,
    }

    old = hooked.secret
    sealed = db.execute('SELECT secret_ciphertext AS s FROM automation_webhooks').fetchone()['s']
    hooked.secret, url = hooked.rotate()
    with acting_for(hooked.org, 'test'):  # a call checked against the old secret just before the rotation
        assert deliveries.record(hooked.id, 'raced', sealed, b'{"email": "p1@acme.it"}') == deliveries.UNAUTHORIZED
    assert hooked.deliveries() == []
    assert (url, hooked.secret != old) == (hooked.url, True)
    assert _code(hooked.call({'email': 'p1@acme.it'}, secret=old)) == 'AUTOMATION.WEBHOOK_UNAUTHORIZED'
    assert hooked.call({'email': 'p1@acme.it'}).status_code == 202

    manual = hooked.api.post(
        '/v1/automations',
        json={
            'name': 'Manual',
            'credit_cap': 5,
            'definition': {'list_id': hooked.list_id, 'steps': [{'type': 'find_emails'}]},
        },
    ).json()
    assert _code(hooked.api.post(f'/v1/automations/{manual["id"]}/webhook/secret')) == 'AUTOMATION.NOT_WEBHOOK'
    mate = client(db, monkeypatch, teammate(db, hooked.org), org=hooked.org)
    assert mate.post(f'/v1/automations/{hooked.id}/webhook/secret').status_code == 403
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', '')
    assert _code(hooked.api.post(f'/v1/automations/{hooked.id}/webhook/secret')) == 'AUTOMATION.WEBHOOK_UNAVAILABLE'


def test_a_call_that_is_not_signed_by_the_hook_writes_nothing(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1)])
    body = {'email': 'p1@acme.it'}
    unauthorized = [
        hooked.call(body, headers={}),
        hooked.call(body, headers={'webhook-id': 'msg_1', 'webhook-timestamp': str(int(time.time()))}),
        hooked.call(body, secret='whsec_' + 'A' * 43 + '='),
        hooked.call(body, path=f'/v1/automation-hooks/{uuid.uuid4()}'),
        hooked.call(body, path='/v1/automation-hooks/not-a-uuid'),
        hooked.call(body, webhook_id='m' * 201),
    ]
    assert [(r.status_code, _code(r)) for r in unauthorized] == [(401, 'AUTOMATION.WEBHOOK_UNAUTHORIZED')] * 6
    stale = hooked.call(body, timestamp=int(time.time()) - 301)
    assert (stale.status_code, _code(stale)) == (401, 'AUTOMATION.WEBHOOK_STALE')
    huge = hooked.call(b'{"email": "p1@acme.it", "pad": "' + b'x' * 17_000 + b'"}')
    assert (huge.status_code, _code(huge)) == (413, 'REQUEST.TOO_LARGE')
    assert hooked.deliveries() == []


def test_a_repeated_webhook_id_gets_the_stored_answer_and_changes_nothing(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1)])
    first = hooked.call({'email': 'p1@acme.it'})
    again = hooked.call({'email': 'someone@else.it'})  # the same webhook-id: its first answer stands
    assert (first.status_code, first.content) == (202, again.content)
    refused = hooked.call({}, webhook_id='msg_2')
    assert (refused.status_code, refused.content) == (422, hooked.call({}, webhook_id='msg_2').content)
    assert [(d['webhook_id'], d['state']) for d in hooked.deliveries()] == [('msg_1', 'pending'), ('msg_2', 'refused')]


def test_two_calls_with_one_webhook_id_at_once_store_one_delivery(monkeypatch):
    """Two requests on two connections: the second waits on the automation's lock, then reads the first answer."""
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        uid = user(c)
        org = member(c, uid).org_id
        cells = _person(1)
        list_id, (row_id,) = make_list(c, uid, 'person', [cells])
        c.execute('UPDATE list_rows SET dedupe_key = %s WHERE id = %s', (dedupe_key('person', cells), row_id))
        automation = c.execute(
            'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, credit_cap, definition)'
            " VALUES (%s, %s, %s, 'A', 'webhook', 5, %s) RETURNING id::text",
            (org, project(c, uid), uid, Jsonb({'list_id': list_id, 'steps': [{'type': 'find_emails'}]})),
        ).fetchone()['id']
        c.execute(
            "INSERT INTO automation_webhooks (automation_id, organization_id, secret_ciphertext) VALUES (%s, %s, 'x')",
            (automation, org),
        )
        inside = threading.Event()
        found = person_match.find

        def slow_find(*args):
            if not inside.is_set():  # the first call, which holds the lock until the other call waits on it
                inside.set()
                wait_for_a_lock(conninfo, 'the second call')
            return found(*args)

        monkeypatch.setattr(person_match, 'find', slow_find)
        answers = []

        def call():
            with acting_for(org, 'test'):
                answers.append(deliveries.record(automation, 'msg_1', 'x', b'{"email": "p1@acme.it"}'))

        threads = [threading.Thread(target=call)]
        threads[0].start()
        inside.wait(5)
        threads.append(threading.Thread(target=call))
        threads[1].start()
        for t in threads:
            t.join(10)
        assert len(answers) == 2
        assert answers[0] == answers[1]
        assert isinstance(answers[0], deliveries.Accepted)
        assert c.execute('SELECT count(*) AS n FROM automation_deliveries').fetchone()['n'] == 1


async def test_each_refusal_is_stored_and_its_replay_gets_the_same_code(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(i) for i in range(4)])
    cases = {
        'no_identifier': ({'name': 'Ada'}, 'AUTOMATION.WEBHOOK_NO_IDENTIFIER'),
        'blank': ({'email': '  ', 'linkedin_url': ''}, 'AUTOMATION.WEBHOOK_NO_IDENTIFIER'),
        'array': (b'[1]', 'AUTOMATION.WEBHOOK_INVALID_BODY'),
        'number': ({'email': 5}, 'AUTOMATION.WEBHOOK_INVALID_BODY'),
        'garbage': (b'not json', 'AUTOMATION.WEBHOOK_INVALID_BODY'),
        'no_match': ({'email': 'nobody@acme.it'}, 'AUTOMATION.WEBHOOK_NO_MATCH'),
    }
    for webhook_id, (body, code) in cases.items():
        assert _code(hooked.call(body, webhook_id)) == code, webhook_id

    db.execute(
        'INSERT INTO automation_rows (automation_id, row_id, organization_id) VALUES (%s, %s, %s)',
        (hooked.id, hooked.rows[0], hooked.org),
    )
    assert _code(hooked.call({'email': 'p0@acme.it'}, 'worked')) == 'AUTOMATION.WEBHOOK_ALREADY_WORKED'
    assert hooked.call({'email': 'p1@acme.it'}, 'first').status_code == 202
    assert _code(hooked.call({'email': 'p1@acme.it'}, 'pending')) == 'AUTOMATION.WEBHOOK_ALREADY_WORKED'
    assert hooked.pickup() == 1  # p1's run is queued: live
    assert _code(hooked.call({'email': 'p1@acme.it'}, 'live')) == 'AUTOMATION.WEBHOOK_ALREADY_WORKED'

    assert hooked.patch(enabled=False).status_code == 200
    assert _code(hooked.call({'email': 'p2@acme.it'}, 'paused')) == 'AUTOMATION.WEBHOOK_PAUSED'
    assert hooked.patch(enabled=True).status_code == 200
    db.execute('DELETE FROM member WHERE "userId" = %s', (hooked.uid,))
    assert _code(hooked.call({'email': 'p3@acme.it'}, 'gone')) == 'AUTOMATION.WEBHOOK_NOT_ACCEPTING'

    stored = {d['webhook_id']: (d['state'], d['code']) for d in hooked.deliveries()}
    assert stored == {
        'no_identifier': ('refused', 'no_identifier'),
        'blank': ('refused', 'no_identifier'),
        'array': ('refused', 'invalid_body'),
        'number': ('refused', 'invalid_body'),
        'garbage': ('refused', 'invalid_body'),
        'no_match': ('refused', 'no_match'),
        'worked': ('refused', 'already_worked'),
        'first': ('started', None),
        'pending': ('refused', 'already_worked'),
        'live': ('refused', 'already_worked'),
        'paused': ('refused', 'automation_paused'),
        'gone': ('refused', 'creator_gone'),
    }
    replays = {
        webhook_id: _code(hooked.call({'email': 'p3@acme.it'}, webhook_id)) for webhook_id in ('no_match', 'paused')
    }
    assert replays == {'no_match': 'AUTOMATION.WEBHOOK_NO_MATCH', 'paused': 'AUTOMATION.WEBHOOK_PAUSED'}


def test_the_person_is_found_by_linkedin_first_then_by_email(db, monkeypatch):
    ada = _person(1, linkedin_url='https://www.linkedin.com/in/ada', email='ada@acme.it')
    bob = _person(2, email='bob@acme.it')
    hooked = Hooked(db, monkeypatch, [ada, bob])
    db.execute('SELECT audience_link_row(%s)', (hooked.rows[0],))  # ada's row is keyed by LinkedIn

    for i, linkedin in enumerate(
        ('http://linkedin.com/in/ada/', 'https://www.linkedin.com/in/ada', 'LINKEDIN.COM/in/ada?x=1')
    ):
        assert hooked.call({'linkedin_url': linkedin}, f'url-{i}').status_code in (202, 409)
    assert hooked.delivery('url-0')['row_id'] == uuid.UUID(hooked.rows[0])
    assert [hooked.delivery(f'url-{i}')['code'] for i in (1, 2)] == ['already_worked', 'already_worked']

    db.execute('DELETE FROM automation_deliveries WHERE automation_id = %s', (hooked.id,))
    assert hooked.call({'email': 'ADA@acme.it '}, 'by-email').status_code == 202
    found = hooked.delivery('by-email')
    assert (found['row_id'], found['matched_by']) == (uuid.UUID(hooked.rows[0]), 'email')

    db.execute('DELETE FROM automation_deliveries WHERE automation_id = %s', (hooked.id,))
    assert hooked.call({'email': 'bob@acme.it', 'linkedin_url': 'linkedin.com/in/ada'}, 'both').status_code == 202
    both = hooked.delivery('both')
    assert (both['row_id'], both['matched_by']) == (uuid.UUID(hooked.rows[0]), 'linkedin_url')


def test_a_limited_call_records_nothing_and_its_retry_is_taken(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1), _person(2)])
    monkeypatch.setattr(rate_limit, 'over_limit', lambda *_: True)
    limited = hooked.call({'email': 'p1@acme.it'})
    assert (limited.status_code, _code(limited), limited.headers['retry-after']) == (
        429,
        'AUTOMATION.WEBHOOK_RATE_LIMITED',
        '60',
    )
    monkeypatch.setattr(rate_limit, 'over_limit', lambda *_: False)
    assert hooked.deliveries() == []
    assert hooked.call({'email': 'p1@acme.it'}).status_code == 202

    db.execute(
        'INSERT INTO automation_deliveries (organization_id, automation_id, webhook_id, state)'
        " SELECT %s, %s, 'filler-' || i, 'pending' FROM generate_series(1, 99) i",
        (hooked.org, hooked.id),
    )
    full = hooked.call({'email': 'p2@acme.it'}, 'msg_2')
    assert (full.status_code, _code(full), full.headers['retry-after']) == (429, 'AUTOMATION.WEBHOOK_BACKLOG', '60')
    assert hooked.delivery('msg_2') is None


async def test_the_beat_starts_one_run_that_works_on_the_called_person_only(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(i) for i in range(30)])
    assert hooked.call({'email': 'p7@acme.it'}).status_code == 202
    assert hooked.pickup() == 1
    delivery = hooked.delivery('msg_1')
    (run,) = hooked.runs()
    assert (delivery['state'], delivery['run_id']) == ('started', run['id'])
    assert (run['trigger_kind'], str(run['target_row_id'])) == ('webhook', hooked.rows[7])

    done = await hooked.work(run['id'])
    assert (done['status'], done['summary']['taken']['people']) == ('succeeded', 1)
    assert hooked.worked() == {hooked.rows[7]}

    assert hooked.call({'email': 'p8@acme.it'}, 'deleted-later').status_code == 202
    assert hooked.pickup() == 1
    db.execute('DELETE FROM list_rows WHERE id = %s', (hooked.rows[8],))
    gone = await hooked.work(hooked.runs()[-1]['id'])
    assert (gone['status'], gone['summary']['taken']['people']) == ('succeeded', 0)
    assert hooked.worked() == {hooked.rows[7]}  # never the best of the list instead

    assert hooked.call({'email': 'p9@acme.it'}, 'deleted-before').status_code == 202
    db.execute('DELETE FROM list_rows WHERE id = %s', (hooked.rows[9],))
    assert hooked.call({'email': 'p10@acme.it'}, 'worked-since').status_code == 202
    db.execute(
        'INSERT INTO automation_rows (automation_id, row_id, organization_id) VALUES (%s, %s, %s)',
        (hooked.id, hooked.rows[10], hooked.org),
    )
    assert hooked.call({'email': 'p11@acme.it'}, 'next').status_code == 202
    assert hooked.pickup() == 1
    dropped = {w: (hooked.delivery(w)['state'], hooked.delivery(w)['code']) for w in ('deleted-before', 'worked-since')}
    assert dropped == {'deleted-before': ('dropped', 'person_gone'), 'worked-since': ('dropped', 'already_worked')}
    assert str(hooked.runs()[-1]['target_row_id']) == hooked.rows[11]


async def test_a_call_waits_while_a_run_of_the_automation_is_live(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1), _person(2)])
    assert hooked.call({'email': 'p1@acme.it'}, 'a').status_code == 202
    assert hooked.pickup() == 1
    assert hooked.call({'email': 'p2@acme.it'}, 'b').status_code == 202
    assert hooked.pickup() == 0
    assert hooked.delivery('b')['state'] == 'pending'
    assert hooked.shown()['webhook']['pending'] == 1

    await hooked.work(hooked.runs()[0]['id'])
    assert hooked.pickup() == 1
    assert hooked.delivery('b')['state'] == 'started'
    assert _code(hooked.api.post(f'/v1/automations/{hooked.id}/run')) == 'AUTOMATION.WEBHOOK_TRIGGERED'


async def test_drafts_of_an_unapproved_version_hold_the_queue_until_the_approval(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1), _person(2)], approved=False)
    assert hooked.call({'email': 'p1@acme.it'}, 'a').status_code == 202
    assert hooked.pickup() == 1
    first = await hooked.work(hooked.runs()[0]['id'])
    assert (first['status'], first['review']) == ('succeeded', True)

    assert hooked.call({'email': 'p2@acme.it'}, 'b').status_code == 202
    assert hooked.pickup() == 0
    assert hooked.shown()['webhook'] | {'rotated_at': None, 'url': None} == {
        'url': None,
        'rotated_at': None,
        'pending': 1,
        'waiting_for_approval': True,
    }
    with acting_as(hooked.org, hooked.uid):
        approval.approve(member(db, hooked.uid), project(db, hooked.uid), hooked.id, first['id'])
    assert hooked.shown()['webhook']['waiting_for_approval'] is False
    assert hooked.pickup() == 1


def test_a_review_run_that_no_approval_accepts_does_not_hold_the_queue(db, monkeypatch):
    """A HubSpot-only review run that the reaper closed: it left no drafts, so ``approve`` answers 409."""
    hooked = Hooked(db, monkeypatch, [_person(1)], approved=False)
    db.execute(
        'INSERT INTO automation_runs (organization_id, automation_id, version, definition, credit_cap, trigger_kind,'
        ' scheduled_for, status, review, hubspot_row_ids) SELECT organization_id, id, version, definition, credit_cap,'
        " 'manual', now() - interval '1 hour', 'unknown', true, %s::uuid[] FROM automations WHERE id = %s",
        ([hooked.rows[0]], hooked.id),
    )
    assert hooked.shown()['webhook']['waiting_for_approval'] is False
    assert hooked.call({'email': 'p1@acme.it'}).status_code == 202
    assert hooked.pickup() == 1


async def test_a_started_call_keeps_its_run_whatever_becomes_of_it(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1), _person(2)])
    assert hooked.call({'email': 'p1@acme.it'}, 'cancelled').status_code == 202
    hooked.pickup()
    run = hooked.runs()[-1]
    assert hooked.api.post(f'/v1/automations/{hooked.id}/runs/{run["id"]}/cancel').json()['status'] == 'cancelled'
    assert hooked.call({'email': 'p1@acme.it'}, 'again').status_code == 202  # nobody worked on p1

    hooked.pickup()
    reaped = hooked.runs()[-1]
    db.execute(
        "UPDATE automation_runs SET status = 'running', started_at = now() - interval '1 hour' WHERE id = %s",
        (reaped['id'],),
    )
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert db.execute('SELECT status FROM automation_runs WHERE id = %s', (reaped['id'],)).fetchone()['status'] == (
        'unknown'
    )
    assert [(d['webhook_id'], d['state']) for d in hooked.deliveries()] == [
        ('cancelled', 'started'),
        ('again', 'started'),
    ]
    assert hooked.call({'email': 'p1@acme.it'}, 'third').status_code == 202


async def test_a_run_refused_its_credits_keeps_its_call_started(db, monkeypatch):
    steps = [{'type': 'find_emails'}]
    hooked = Hooked(db, monkeypatch, [_person(1, email=None, email_status=None)], steps=steps, credits=0)
    db.execute("UPDATE list_rows SET dedupe_key = 'linkedin.com/in/one' WHERE id = %s", (hooked.rows[0],))
    assert hooked.call({'linkedin_url': 'https://linkedin.com/in/one'}).status_code == 202
    hooked.pickup()
    failed = await hooked.work(hooked.runs()[-1]['id'])
    assert (failed['status'], failed['error_code']) == ('failed', 'insufficient_credits')
    assert hooked.delivery('msg_1')['state'] == 'started'
    assert hooked.call({'linkedin_url': 'https://linkedin.com/in/one'}, 'msg_2').status_code == 202


def test_a_pause_or_another_trigger_drops_the_waiting_calls(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1), _person(2), _person(3)])
    assert hooked.call({'email': 'p1@acme.it'}, 'a').status_code == 202
    assert hooked.patch(enabled=False).status_code == 200
    assert (hooked.delivery('a')['state'], hooked.delivery('a')['code']) == ('dropped', 'automation_paused')

    assert hooked.patch(enabled=True).status_code == 200
    assert hooked.call({'email': 'p2@acme.it'}, 'b').status_code == 202
    assert hooked.patch(trigger={'kind': 'manual'}).status_code == 200
    assert (hooked.delivery('b')['state'], hooked.delivery('b')['code']) == ('dropped', 'trigger_changed')
    assert _code(hooked.call({'email': 'p3@acme.it'}, 'c')) == 'AUTOMATION.WEBHOOK_UNAUTHORIZED'
    assert hooked.shown()['webhook'] is None

    assert hooked.patch(trigger={'kind': 'webhook'}).status_code == 200
    hooked.secret, url = hooked.rotate()
    assert url != hooked.url  # a switch back makes a new URL
    hooked.url = url
    assert hooked.call({'email': 'p3@acme.it'}, 'old').status_code == 202
    assert hooked.call({'email': 'nobody@acme.it'}, 'refused-long-ago').status_code == 422
    assert hooked.call({'email': 'nobody@acme.it'}, 'refused-lately').status_code == 422
    db.execute(
        "UPDATE automation_deliveries SET received_at = now() - interval '8 days' WHERE webhook_id = 'old';"
        "UPDATE automation_deliveries SET decided_at = now() - interval '31 days' WHERE webhook_id = 'refused-long-ago';"
        "UPDATE automation_deliveries SET decided_at = now() - interval '29 days' WHERE webhook_id = 'refused-lately'"
    )
    assert hooked.pickup() == 0
    assert (hooked.delivery('old')['state'], hooked.delivery('old')['code']) == ('dropped', 'expired')
    assert hooked.delivery('refused-long-ago') is None
    assert hooked.delivery('refused-lately')['state'] == 'refused'


def test_held_automations_never_take_the_beat_from_one_that_can_start(db, monkeypatch):
    held = Hooked(db, monkeypatch, [_person(1), _person(2)])
    assert held.call({'email': 'p1@acme.it'}, 'a').status_code == 202
    assert held.pickup() == 1  # its run stays live: its next call waits
    assert held.call({'email': 'p2@acme.it'}, 'b').status_code == 202
    ready = Hooked(db, monkeypatch, [_person(3)])
    assert ready.call({'email': 'p3@acme.it'}, 'c').status_code == 202
    monkeypatch.setattr(deliveries, 'BATCH', 1)

    assert ready.pickup() == 1
    assert ready.delivery('c')['state'] == 'started'
    assert held.delivery('b')['state'] == 'pending'


def test_the_beat_tidies_the_old_calls_of_an_automation_that_cannot_start(db, monkeypatch):
    held = Hooked(db, monkeypatch, [_person(1), _person(2)])
    assert held.call({'email': 'p1@acme.it'}, 'a').status_code == 202
    assert held.pickup() == 1  # its run stays live
    assert held.call({'email': 'p2@acme.it'}, 'waited').status_code == 202
    idle = Hooked(db, monkeypatch, [_person(3)])
    assert idle.call({'email': 'nobody@acme.it'}, 'refused').status_code == 422
    db.execute(
        "UPDATE automation_deliveries SET received_at = now() - interval '8 days' WHERE webhook_id = 'waited';"
        "UPDATE automation_deliveries SET decided_at = now() - interval '31 days' WHERE webhook_id = 'refused'"
    )

    assert held.pickup() == 0
    assert (held.delivery('waited')['state'], held.delivery('waited')['code']) == ('dropped', 'expired')
    assert idle.delivery('refused') is None


async def test_the_run_shows_the_call_and_no_log_holds_a_secret_or_a_person(db, monkeypatch, app_logging):
    hooked = Hooked(db, monkeypatch, [_person(1, linkedin_url='https://linkedin.com/in/ada')])
    db.execute('SELECT audience_link_row(%s)', (hooked.rows[0],))
    hook_id = hooked.url.rsplit('/', 1)[1]
    with capture_logs() as logs:
        assert hooked.call({'email': 'p1@acme.it', 'note': 'call me'}).status_code == 202
        hooked.call({'email': 'p1@acme.it'}, secret='whsec_' + 'B' * 43 + '=')
        assert hooked.call({'email': 'p1@acme.it'}, 'twice').status_code == 409
    hooked.pickup()
    run_id = hooked.runs()[-1]['id']
    shown = hooked.api.get(f'/v1/automations/{hooked.id}/runs/{run_id}').json()
    assert shown['trigger_kind'] == 'webhook'
    assert shown['delivery'] | {'received_at': None} == {
        'id': str(hooked.delivery('msg_1')['id']),
        'webhook_id': 'msg_1',
        'received_at': None,
        'matched_by': 'email',
        'row_id': hooked.rows[0],
    }
    assert 'p1@acme.it' not in json.dumps(shown)
    logged = json.dumps(logs, default=str)
    for secret_or_person in (hooked.secret, hook_id, 'p1@acme.it', 'call me', 'linkedin.com/in/ada'):
        assert secret_or_person not in logged
    assert {e.get('answer') for e in logs if e['event'] == 'automation.webhook'} == {'accepted', 'already_worked'}


async def test_a_webhook_definition_never_finds_people(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(1)])
    accounts, _ = make_list(db, hooked.uid, 'company')
    find = {'type': 'find_people', 'accounts_list_id': accounts, 'titles': ['CEO']}
    steps = [find, {'type': 'add_to_campaign', 'campaign_id': hooked.campaign}]
    body = {'name': 'Finder', 'credit_cap': 5, 'definition': {'list_id': hooked.list_id, 'steps': steps}}
    made = hooked.api.post('/v1/automations', json={**body, 'trigger': {'kind': 'webhook'}})
    assert _code(made) == 'AUTOMATION.WEBHOOK_FIND_PEOPLE'

    assert hooked.patch(enabled=False).status_code == 200
    with_find = hooked.patch(definition={'list_id': hooked.list_id, 'steps': steps})
    assert _code(with_find) == 'AUTOMATION.WEBHOOK_FIND_PEOPLE'  # a paused automation is checked too
    finder = hooked.api.post('/v1/automations', json=body).json()
    to_webhook = hooked.api.patch(f'/v1/automations/{finder["id"]}', json={'trigger': {'kind': 'webhook'}})
    assert _code(to_webhook) == 'AUTOMATION.WEBHOOK_FIND_PEOPLE'

    assert hooked.patch(enabled=True).status_code == 200
    assert hooked.call({'email': 'p1@acme.it'}).status_code == 202
    hooked.pickup()
    run = hooked.runs()[-1]
    definition = Jsonb({'list_id': hooked.list_id, 'steps': steps})
    db.execute('UPDATE automation_runs SET definition = %s WHERE id = %s', (definition, run['id']))
    failed = await hooked.work(run['id'])
    assert (failed['status'], failed['error_code']) == ('failed', 'invalid_configuration')


def test_a_webhook_preview_counts_one_person_for_each_run(db, monkeypatch):
    hooked = Hooked(db, monkeypatch, [_person(i) for i in range(30)], steps=[{'type': 'find_emails'}])
    definition = {'list_id': hooked.list_id, 'steps': [{'type': 'find_emails'}]}

    def preview(**extra):
        got = hooked.api.post('/v1/automations/preview', json={'definition': definition, 'credit_cap': 10, **extra})
        assert got.status_code == 200, got.text
        return got.json()['people_per_run'], got.json()['max_credits']

    assert preview(automation_id=hooked.id) == (1, 1)
    assert preview(trigger_kind='webhook') == (1, 1)  # a draft not saved yet
    assert preview(automation_id=hooked.id, trigger_kind='manual') == (25, 10)  # a draft that leaves the webhook
    assert preview() == (25, 10)

    accounts, _ = make_list(db, hooked.uid, 'company')
    find = {'type': 'find_people', 'accounts_list_id': accounts, 'titles': ['CEO']}
    finder = {'list_id': hooked.list_id, 'steps': [find]}
    got = hooked.api.post(
        '/v1/automations/preview', json={'definition': finder, 'credit_cap': 10, 'trigger_kind': 'webhook'}
    ).json()
    assert [p['code'] for p in got['problems']] == ['AUTOMATION.WEBHOOK_FIND_PEOPLE']
