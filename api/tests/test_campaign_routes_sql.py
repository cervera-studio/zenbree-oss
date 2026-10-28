"""Campaign builder routes (#84) against real SQL; provider faked. Needs TEST_DATABASE_URL."""

import email
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.core.config import settings
from app.core.errors import err, ok
from app.core.mail_threading import ProviderReceipt
from app.core.migrate import MIGRATIONS
from app.integrations import mailbox_sender, token_crypto
from app.integrations.oauth_client import TokenGrant
from app.main import app
from app.outreach import dispatcher, pool_routes, routes, sending, test_email_routes, variant_routes
from app.people.person_name import first_and_last
from tests.conftest import (
    conninfo_of,
    each_org,
    member,
    migrated_database,
    pool_on,
    project,
    scoped_conn,
    sender,
    sign_in,
    wait_for_a_lock,
)
from tests.conftest import (
    pool as fill_pool,
)
from tests.test_holds_sql import lead
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
BACKFILL = '20261006074107_backfill_target_names.sql'
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: campaign routes SQL test NOT run')

STEPS = [
    {'step': 1, 'channel': 'email', 'subject': 'Ciao {{firstName}}', 'body': 'Ciao {{firstName}} di {{companyName}}',
     'send_after_days': 0},
    {'step': 2, 'channel': 'email', 'subject': '', 'body': 'Ti riscrivo, {{firstName}}', 'send_after_days': 3,
     'threaded': True},
]  # fmt: skip


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (routes, dispatcher, pool_routes, test_email_routes, variant_routes):
                monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
            monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
            monkeypatch.setattr('app.notifications.service.get_conn', scoped_conn(c))
            monkeypatch.setattr(test_email_routes, 'is_disabled', lambda key: False)
            monkeypatch.setattr(sys.modules[__name__], '_DB', [c])
            yield c
        finally:
            app.dependency_overrides.clear()


_DB: list = []  # the test's own connection, for its setup


def _as(uid: str) -> TestClient:
    sign_in(app, member(_DB[0], uid))
    return TestClient(app)


def _list(c, uid, entity='person', rows=()) -> str:
    lid = c.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (default_organization(%s), %s, %s, 'L', %s) RETURNING id",
        (uid, project(c, uid), uid, entity),
    ).fetchone()['id']
    for cells in rows:
        c.execute(
            'INSERT INTO list_rows (list_id, cells, excluded) VALUES (%s, %s, %s)',
            (lid, json.dumps(cells), cells.get('_excluded', False)),
        )
    return str(lid)


def _mailbox(c, uid, *, scopes=('https://www.googleapis.com/auth/gmail.send',), display_name='') -> str:
    return sender(
        c,
        uid,
        refresh_token_encrypted=token_crypto.encrypt('refresh-1').value,
        granted_scopes=list(scopes),
        account_label='me@acme.test',
        sign_off='Marco',
        display_name=display_name,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())


def test_create_enrolls_reachable_people_as_drafts(db):
    uid, other = _user(db), _user(db)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'blocked.test')",
        (project(db, uid), uid),
    )
    lid = _list(
        db,
        uid,
        rows=[
            {'first_name': 'Anna', 'email': ' Anna@Client.test ', 'company_name': 'Client', 'title': 'CEO'},
            {'first_name': 'Bea', 'email': 'bad@client.test', 'email_status': 'invalid',
             'linkedin_url': 'https://linkedin.com/in/bea'},
            {'first_name': 'Carl', 'email': 'carl@blocked.test'},  # excluded by the user's rules
            {'first_name': 'Dan', 'email': 'dan@client.test', '_excluded': True},  # excluded row
            {'first_name': 'Eve'},  # nobody to reach
            {'first_name': 'Anna again', 'email': 'anna@client.test'},  # duplicate identity
        ],
    )  # fmt: skip
    client = _as(uid)

    created = client.post('/v1/campaigns', json={'name': ' Q4 ', 'list_id': lid})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['name'] == 'Q4' and body['status'] == 'draft' and body['steps'] == []
    assert body['target_count'] == 2 and body['linkedin_risk_notice']
    targets = db.execute('SELECT * FROM outreach_campaign_targets ORDER BY first_name').fetchall()
    assert [(t['first_name'], t['email'], t['status'], t['role']) for t in targets] == [
        ('Anna', 'anna@client.test', 'draft', 'CEO'),
        ('Bea', None, 'draft', ''),
    ]

    assert client.post('/v1/campaigns', json={'name': 'X', 'list_id': _list(db, uid, 'company')}).status_code == 422
    assert client.post('/v1/campaigns', json={'name': 'X', 'list_id': _list(db, other)}).status_code == 404
    assert _as(other).get(f'/v1/campaigns/{body["id"]}').status_code == 404
    assert _as(other).get('/v1/campaigns').json() == {'campaigns': []}


def test_list_filters_by_status(db):
    uid = _user(db)
    client = _as(uid)
    lid = _list(db, uid)
    ids = {
        n: client.post('/v1/campaigns', json={'name': n, 'list_id': lid}).json()['id'] for n in ('Old', 'Live', 'New')
    }
    for age, name in enumerate(('New', 'Live', 'Old')):
        db.execute(
            "UPDATE outreach_campaigns SET created_at = now() - %s * interval '1 hour' WHERE id = %s", (age, ids[name])
        )
    db.execute("UPDATE outreach_campaigns SET status = 'active' WHERE id = %s", (ids['Live'],))

    drafts = client.get('/v1/campaigns', params={'status': 'draft'})

    assert [c['name'] for c in drafts.json()['campaigns']] == ['New', 'Old']
    assert [c['name'] for c in client.get('/v1/campaigns').json()['campaigns']] == ['New', 'Live', 'Old']
    assert client.get('/v1/campaigns', params={'status': 'gone'}).status_code == 422


def test_list_carries_list_name_channels_and_progress(db):
    uid = _user(db)
    lid = _list(db, uid)
    steps = [*STEPS, {'step': 3, 'channel': 'linkedin_connect', 'body': '', 'send_after_days': 5}]
    cid = db.execute(
        "INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, list_id, steps) VALUES (default_organization(%s), %s, %s, 'C', 'active', %s, %s)"
        ' RETURNING id',
        (uid, project(db, uid), uid, lid, json.dumps(steps)),
    ).fetchone()['id']

    def target(name, status, next_step, *sends):
        tid = db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, next_step, paused_until)'
            " VALUES (%s, %s, %s, %s, %s, CASE WHEN %s = 'paused' THEN now() END) RETURNING id",
            (cid, uid, f'{name}@c.test', status, next_step, status),
        ).fetchone()['id']
        for step, send_status, code in sends:
            db.execute(
                'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,'
                ' error_code, idempotency_key, delivered_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (cid, uid, tid, step, 'email', send_status, f'{name}@c.test', code, f'{tid}:{step}',
                 datetime.now(UTC) if send_status == 'sent' else None),
            )  # fmt: skip

    # Mid-sequence: 1 of 3 done, 3 planned.
    target('mid', 'active', 2, (1, 'sent', None))
    # Replied after step 1: 1 of 1.
    target('rep', 'replied', 2, (1, 'sent', None))
    # Excluded before starting: 0 of 0.
    target('exc', 'excluded', 1)
    # Finished with a skipped step: 3 of 3, a full bar even though one step never went out.
    target('fin', 'active', 4, (1, 'sent', None), (2, 'skipped', 'NO_THREAD'), (3, 'sent', None))
    # Refused on step 1 stays there for good: 1 of 1.
    target('bad', 'active', 1, (1, 'refused', 'INVALID_EMAIL'))
    # Failed with no attempt left on step 2: as stuck as a refusal, 2 of 2.
    target('out', 'active', 2, (1, 'sent', None))
    out = db.execute("SELECT id FROM outreach_campaign_targets WHERE email = 'out@c.test'").fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, attempts,'
        " recipient, idempotency_key) VALUES (%s, %s, %s, 2, 'email', 'failed', %s, 'out@c.test', 'out:2')",
        (cid, uid, out, sending.MAX_ATTEMPTS),
    )
    # Paused on step 1: still in the sequence, 0 of 3.
    target('pau', 'paused', 1)
    # Marked replied by a reply that matched them before this campaign wrote to them: not in the rate.
    target('cold', 'replied', 1)

    [row] = _as(uid).get('/v1/campaigns').json()['campaigns']
    assert row['list_name'] == 'L'
    assert row['step_channels'] == ['email', 'email', 'linkedin_connect']
    assert (row['steps_done'], row['planned_sends']) == (1 + 1 + 0 + 3 + 1 + 2 + 0 + 0, 3 + 1 + 0 + 3 + 1 + 2 + 3 + 0)
    assert (row['target_count'], row['sent'], row['contacted'], row['failed']) == (8, 5, 4, 2)
    # rep and cold replied; only rep was contacted, so the rate is 1 of 4, never over 100%.
    assert (row['replied'], row['replied_contacted']) == (2, 1)

    db.execute('DELETE FROM lists WHERE id = %s', (lid,))
    assert _as(uid).get('/v1/campaigns').json()['campaigns'][0]['list_name'] is None


def test_list_counts_each_campaign_on_its_own_sends_and_organization(db):
    """#1128: the list reads the sends of all its campaigns at once; a count never crosses campaigns or clients."""
    uid, other = _user(db), _user(db)
    steps = [*STEPS, {'step': 3, 'channel': 'linkedin_connect', 'body': '', 'send_after_days': 5}]

    def campaign(owner, name, shape):
        lid = _list(db, owner)
        cid = db.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, list_id, steps, created_at)'
            " VALUES (default_organization(%s), %s, %s, %s, 'active', %s, %s, now() - %s * interval '1 hour')"
            ' RETURNING id',
            (owner, project(db, owner), owner, name, lid, json.dumps(steps), len(name)),
        ).fetchone()['id']
        for n, (status, next_step, sends) in enumerate(shape):
            tid = db.execute(
                'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, next_step)'
                ' VALUES (%s, %s, %s, %s, %s) RETURNING id',
                (cid, owner, f'{name}{n}@c.test', status, next_step),
            ).fetchone()['id']
            for step, send_status, attempts in sends:
                db.execute(
                    'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status,'
                    " attempts, recipient, idempotency_key, delivered_at) VALUES (%s, %s, %s, %s, 'email', %s, %s,"
                    " 'x@c.test', %s, CASE WHEN %s = 'sent' THEN now() END)",
                    (cid, owner, tid, step, send_status, attempts, f'{tid}:{step}', send_status),
                )  # fmt: skip
        return cid

    # Three sends went out to two people; one person is refused on the current step, one failed for good on an old one.
    big = campaign(
        uid,
        'big',
        [
            ('active', 3, [(1, 'sent', 0), (2, 'sent', 0)]),
            ('replied', 2, [(1, 'sent', 0)]),
            ('active', 1, [(1, 'refused', 0)]),
            # A terminal failure on step 1 that the person already left behind is not "stuck".
            ('active', 2, [(1, 'failed', sending.MAX_ATTEMPTS), (2, 'failed', sending.MAX_ATTEMPTS - 1)]),
        ],
    )
    # A delivered send whose person was deleted keeps its row with no target: it is a send, not a contacted person.
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,'
        " idempotency_key, delivered_at) VALUES (%s, %s, NULL, 1, 'email', 'sent', 'gone@c.test', 'gone:1', now())",
        (big, uid),
    )
    small = campaign(uid, 'sm', [('active', 2, [(1, 'sent', 0)]), ('active', 2, [(1, 'sent', 0)])])
    # A hard bounce is delivered, then failed: it counts in both.
    db.execute(
        "UPDATE outreach_campaign_sends SET bounce_kind = 'hard', bounced_at = now() WHERE campaign_id = %s"
        ' AND target_id = (SELECT min(id::text)::uuid FROM outreach_campaign_targets WHERE campaign_id = %s)',
        (small, small),
    )
    # Another client of another organization has sends of the same shape: none of them is ours.
    campaign(other, 'big', [('active', 1, [(1, 'sent', 0), (2, 'refused', 0)])] * 3)

    rows = {r['id']: r for r in _as(uid).get('/v1/campaigns').json()['campaigns']}
    assert set(rows) == {str(big), str(small)}
    b, s = rows[str(big)], rows[str(small)]
    assert (b['target_count'], b['sent'], b['contacted'], b['failed']) == (4, 4, 2, 2)
    assert (b['replied'], b['replied_contacted']) == (1, 1)
    # done 2 + 1 + 1 (refused: stuck) + 1; planned 3 + 1 (replied) + 1 (stuck) + 3 (step 2 still has attempts)
    assert (b['steps_done'], b['planned_sends']) == (2 + 1 + 1 + 1, 3 + 1 + 1 + 3)
    assert (s['target_count'], s['sent'], s['contacted'], s['failed']) == (2, 2, 2, 1)
    assert (s['steps_done'], s['planned_sends']) == (2, 6)


def test_create_skips_duplicates_prior_contacts_and_replies(db):
    uid, other = _user(db), _user(db)

    def campaign(owner, status, *targets):
        cid = db.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status) VALUES (default_organization(%s), %s, %s, %s, %s) RETURNING id',
            (owner, project(db, owner), owner, status, status),
        ).fetchone()['id']
        for t in targets:
            db.execute(
                'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, status, replied_at)'
                ' VALUES (%s, %s, %s, %s, %s, %s)',
                (cid, owner, t.get('email'), t.get('linkedin', ''), t.get('status', 'active'), t.get('replied_at')),
            )
        return cid

    campaign(uid, 'active', {'email': 'anna@client.test'})  # in another active campaign
    campaign(uid, 'paused', {'linkedin': 'https://www.linkedin.com/in/bea/'})  # paused one, matched by LinkedIn
    campaign(uid, 'stopped', {'email': 'cat@client.test', 'status': 'replied', 'replied_at': datetime.now(UTC)})
    done = campaign(uid, 'completed', {'email': 'dan@client.test'})
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, step, channel, status, recipient, idempotency_key,'
        " delivered_at) VALUES (%s, %s, 1, 'email', 'sent', 'dan@client.test', 'k1', now())",
        (done, uid),
    )  # prior contact
    campaign(other, 'active', {'email': 'eve@client.test'})  # another owner's campaign never blocks
    lid = _list(
        db,
        uid,
        rows=[
            {
                'first_name': n,
                'email': f'{n}@client.test',
                **({'linkedin_url': 'linkedin.com/in/bea'} if n == 'bea' else {}),
            }
            for n in ('anna', 'bea', 'cat', 'dan', 'eve')
        ],
    )
    created = _as(uid).post('/v1/campaigns', json={'name': 'New', 'list_id': lid}).json()
    enrolled = db.execute('SELECT email FROM outreach_campaign_targets WHERE campaign_id = %s', (created['id'],))
    assert [t['email'] for t in enrolled] == ['eve@client.test']


def test_activation_drops_people_another_campaign_took_meanwhile(db, key):
    """Two drafts from one list: whoever activates second loses the shared people, not the inboxes."""
    uid = _user(db)
    _mailbox(db, uid)
    client = _as(uid)
    shared = _list(db, uid, rows=[{'first_name': n, 'email': f'{n}@client.test'} for n in ('ann', 'bob')])
    first, second = (client.post('/v1/campaigns', json={'name': n, 'list_id': shared}).json()['id'] for n in 'AB')
    for cid in (first, second):
        client.patch(f'/v1/campaigns/{cid}', json={'steps': STEPS[:1]})
    assert client.post(f'/v1/campaigns/{first}/activate').json()['target_count'] == 2
    blocked = client.post(f'/v1/campaigns/{second}/activate')
    assert blocked.json()['detail']['code'] == 'CAMPAIGN.NO_TARGETS'  # everyone is taken: nothing changes
    assert (
        db.execute('SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id = %s', (second,)).fetchone()[
            'n'
        ]
        == 2
    )
    # With one free person, activation keeps them and drops the two the first campaign is writing to.
    db.execute(
        "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name) VALUES (%s, %s, 'cy@client.test', 'cy')",
        (second, uid),
    )
    active = client.post(f'/v1/campaigns/{second}/activate').json()
    assert active['status'] == 'active' and active['target_count'] == 1
    assert (
        db.execute('SELECT email FROM outreach_campaign_targets WHERE campaign_id = %s', (second,)).fetchone()['email']
        == 'cy@client.test'
    )


def test_steps_draft_only_and_explicit_activation(db, key, monkeypatch):
    uid = _user(db)
    client = _as(uid)
    cid = client.post(
        '/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=[{'first_name': 'A', 'email': 'a@c.test'}])}
    ).json()['id']
    url = f'/v1/campaigns/{cid}'

    assert client.post(f'{url}/activate').json()['detail']['code'] == 'CAMPAIGN.NO_STEPS'
    bad = client.patch(url, json={'steps': [{**STEPS[1], 'step': 1}]})
    assert bad.status_code == 422 and bad.json()['detail']['code'] == 'CAMPAIGN.INVALID_STEPS'
    long = client.patch(url, json={'steps': [{**STEPS[0], 'body': 'x' * 4001}]})
    assert long.status_code == 422 and long.json()['detail'] == {
        'code': 'CAMPAIGN.COPY_TOO_LONG',
        'message': 'step 1 body: 4001 characters, at most 4000',
        'step': 1,
    }
    for subject in ('Hi\r\nBcc: x@evil.test', 'two\nlines'):
        broken = client.patch(url, json={'steps': [{**STEPS[0], 'subject': subject}]})
        assert broken.json()['detail']['code'] == 'CAMPAIGN.SUBJECT_LINE_BREAK'
        assert broken.json()['detail']['step'] == 1
    too_many = client.patch(url, json={'steps': [{**STEPS[0], 'step': n} for n in range(1, 14)]})
    assert too_many.status_code == 422
    assert len(client.patch(url, json={'steps': STEPS}).json()['steps']) == 2

    assert client.post(f'{url}/activate').json()['detail']['code'] == 'CAMPAIGN.NO_SENDER'  # pool not connected
    linkedin = [{'step': 1, 'channel': 'linkedin_connect', 'body': '', 'send_after_days': 0}]
    client.patch(url, json={'steps': linkedin})
    # A channel the dispatcher cannot run is reported first: connecting LinkedIn would not help.
    with monkeypatch.context() as m:
        m.setattr(dispatcher, 'SENDABLE_CHANNELS', ('email',))
        assert client.post(f'{url}/activate').json()['detail']['code'] == 'CAMPAIGN.CHANNEL_UNAVAILABLE'
        m.setattr(dispatcher, 'SENDABLE_CHANNELS', ('email', 'linkedin_connect', 'linkedin_message'))
        assert client.post(f'{url}/activate').json()['detail']['code'] == 'CAMPAIGN.NO_LINKEDIN'
    # #123: a task step needs no LinkedIn, but a HubSpot grant that can write.
    task = [{'step': 1, 'channel': 'hubspot_task', 'subject': 'Idea', 'body': 'Scrivile tu', 'send_after_days': 0}]
    assert client.patch(url, json={'steps': task}).status_code == 200
    assert client.post(f'{url}/activate').json()['detail']['code'] == 'CAMPAIGN.NO_HUBSPOT'
    threaded = client.patch(url, json={'steps': [{**task[0], 'threaded': True}]})
    assert threaded.json()['detail']['code'] == 'CAMPAIGN.INVALID_STEPS'
    assert threaded.json()['detail']['step'] == 1
    client.patch(url, json={'steps': STEPS})
    client.put(f'{url}/senders', json={'sender_ids': [_mailbox(db, uid)]})
    active = client.post(f'{url}/activate')
    assert active.status_code == 200 and active.json()['status'] == 'active'
    assert db.execute('SELECT status FROM outreach_campaign_targets').fetchone()['status'] == 'active'

    assert client.patch(url, json={'steps': STEPS[:1]}).json()['detail']['code'] == 'CAMPAIGN.NOT_DRAFT'
    assert client.patch(url, json={'name': 'Renamed'}).json()['name'] == 'Renamed'
    assert client.post(f'{url}/activate').status_code == 409
    assert client.post(f'{url}/resume').status_code == 409
    assert client.post(f'{url}/pause').json()['status'] == 'paused'
    assert client.delete(url).status_code == 409
    assert client.post(f'{url}/resume').json()['status'] == 'active'


def test_preview_renders_like_the_dispatcher(db):
    uid = _user(db)
    client = _as(uid)
    lid = _list(
        db,
        uid,
        rows=[
            {'first_name': 'Anna', 'email': 'anna@client.test', 'company_name': 'Client'},
            {'first_name': 'Bo', 'email': 'bo@client.test'},  # no company: step 1 refused, so is the thread
            {'first_name': 'Cy', 'linkedin_url': 'linkedin.com/in/cy'},  # no email
            {'first_name': 'Di', 'email': 'di@client.test, x@client.test', 'company_name': 'C'},  # not one address
            {'full_name': 'Eva De Luca', 'email': 'eva@client.test', 'company_name': 'Client'},  # #235: one name column
        ],
    )
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': lid}).json()['id']
    client.patch(f'/v1/campaigns/{cid}', json={'steps': STEPS})
    items = client.get(f'/v1/campaigns/{cid}/preview?limit=10').json()['items']
    by_name = {i['target']['first_name']: i['steps'] for i in items}
    assert (by_name['Eva'][0]['subject'], by_name['Eva'][0]['error']) == ('Ciao Eva', None)
    assert [i['target']['last_name'] for i in items if i['target']['first_name'] == 'Eva'] == ['De Luca']
    assert by_name['Anna'][0] | {'step': 1} == {
        'step': 1,
        'channel': 'email',
        'subject': 'Ciao Anna',
        'body': 'Ciao Anna di Client',
        'error': None,
        'sign_off': '',
        'footer': sending.opt_out_footer(),
    }
    assert by_name['Anna'][1]['subject'] == 'Re: Ciao Anna'
    assert [s['error'] for s in by_name['Bo']] == ['OUTREACH.COPY_MISSING_FIELD', 'NO_THREAD']
    assert [s['error'] for s in by_name['Cy']] == ['NO_EMAIL', 'NO_EMAIL']
    assert [s['error'] for s in by_name['Di']] == ['INVALID_EMAIL', 'INVALID_EMAIL']


def test_live_preview_renders_unsaved_steps_and_stores_nothing(db):
    uid, other = _user(db), _user(db)
    client = _as(uid)
    rows = [
        {'first_name': 'Anna', 'email': 'anna@client.test', 'company_name': 'Client'},
        {'first_name': 'Cy', 'linkedin_url': 'linkedin.com/in/cy'},
    ]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    typed = [
        {'channel': 'email', 'subject': 'Per {{companyName}}', 'body': 'Ciao {{firstName}}'},
        {'channel': 'email', 'body': 'Ancora io', 'threaded': True},
        {'channel': 'linkedin_connect', 'body': ''},  # half-written steps preview too
    ]
    res = client.post(f'/v1/campaigns/{cid}/preview', json={'steps': typed})
    by_name = {i['target']['first_name']: i['steps'] for i in res.json()['items']}
    assert [s['step'] for s in by_name['Anna']] == [1, 2, 3]
    assert by_name['Anna'][0]['subject'] == 'Per Client' and by_name['Anna'][0]['body'] == 'Ciao Anna'
    assert by_name['Anna'][1]['subject'] == 'Re: Per Client'
    assert [s['error'] for s in by_name['Cy']][:2] == ['NO_EMAIL', 'NO_EMAIL']  # why the email steps are skipped
    assert client.get(f'/v1/campaigns/{cid}').json()['steps'] == []
    assert _as(other).post(f'/v1/campaigns/{cid}/preview', json={'steps': typed}).status_code == 404
    too_many = [{'channel': 'email', 'subject': 's', 'body': 'b'}] * 13
    assert _as(uid).post(f'/v1/campaigns/{cid}/preview', json={'steps': too_many}).status_code == 422


def test_linkedin_order_refusal_names_the_rule_and_step(db):
    uid = _user(db)
    client = _as(uid)
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid)}).json()['id']
    url = f'/v1/campaigns/{cid}'
    dm = {'step': 2, 'channel': 'linkedin_message', 'body': 'Ciao {{firstName}}', 'send_after_days': 3}
    invite = {'step': 1, 'channel': 'linkedin_connect', 'body': '', 'send_after_days': 0}

    no_invite = client.patch(url, json={'steps': [STEPS[0], dm]})
    assert no_invite.status_code == 422
    assert (no_invite.json()['detail']['code'], no_invite.json()['detail']['step']) == (
        'CAMPAIGN.LINKEDIN_NO_INVITE',
        2,
    )
    same_day = client.patch(url, json={'steps': [invite, dm, dm | {'step': 3}]})
    assert (same_day.json()['detail']['code'], same_day.json()['detail']['step']) == ('CAMPAIGN.LINKEDIN_DM_DELAY', 3)
    assert client.get(url).json()['steps'] == []
    assert [s['channel'] for s in client.patch(url, json={'steps': [invite, dm]}).json()['steps']] == [
        'linkedin_connect',
        'linkedin_message',
    ]


B = {'subject': '{{firstName}}, due minuti?', 'body': 'Ciao {{firstName}}, sono io'}


def test_copy_b_is_checked_like_a_and_previewed_beside_it(db):
    uid = _user(db)
    client = _as(uid)
    rows = [{'first_name': 'Anna', 'email': 'anna@client.test', 'company_name': 'Client'}]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    url = f'/v1/campaigns/{cid}'

    client.patch(url, json={'steps': STEPS})
    assert all('b' not in s for s in db.execute('SELECT steps FROM outreach_campaigns').fetchone()['steps'])
    (item,) = client.get(f'{url}/preview').json()['items']
    assert (item['steps_b'], item['variant']) == (None, None)
    for b, code in (
        ({'subject': 'two\nlines', 'body': 'x'}, 'CAMPAIGN.SUBJECT_LINE_BREAK'),
        ({'subject': 'x' * 301, 'body': 'x'}, 'CAMPAIGN.INVALID_STEPS'),
        ({'body': ''}, 'CAMPAIGN.INVALID_STEPS'),
    ):
        res = client.patch(url, json={'steps': [STEPS[0] | {'b': b}, STEPS[1]]})
        assert res.status_code == 422, f'copy B {b} was saved'
        refused = res.json()['detail']
        assert (refused['code'], refused['step'], refused['variant']) == (code, 1, 'b'), refused
    threaded = client.patch(url, json={'steps': [STEPS[0], STEPS[1] | {'b': {'subject': 'New', 'body': 'x'}}]})
    detail = threaded.json()['detail']
    assert (detail['code'], detail['step'], detail['variant']) == ('CAMPAIGN.INVALID_STEPS', 2, 'b')
    unknown = client.patch(url, json={'steps': [STEPS[0] | {'b': B | {'tone': 'warm'}}, STEPS[1]]})
    assert unknown.json()['detail']['code'] == 'CAMPAIGN.INVALID_STEPS'
    a_refused = client.patch(url, json={'steps': [STEPS[0] | {'subject': 'a\nb', 'b': B}, STEPS[1]]})
    assert 'variant' not in a_refused.json()['detail']

    saved = client.patch(url, json={'steps': [STEPS[0] | {'b': B}, STEPS[1]]}).json()['steps']
    assert saved[0]['b'] == B and 'b' not in saved[1]
    (item,) = client.get(f'{url}/preview').json()['items']
    assert [s['subject'] for s in item['steps']] == ['Ciao Anna', 'Re: Ciao Anna']
    assert [(s['subject'], s['body']) for s in item['steps_b']] == [
        ('Anna, due minuti?', 'Ciao Anna, sono io'),
        ('Re: Anna, due minuti?', 'Ti riscrivo, Anna'),  # step 2 has no B: A's body under B's thread
    ]
    assert item['variant'] is None
    live = client.post(f'{url}/preview', json={'steps': [{'channel': 'email', 'subject': 'A', 'body': 'a', 'b': B}]})
    assert live.json()['items'][0]['steps_b'][0]['subject'] == 'Anna, due minuti?'


def test_the_share_of_b_is_set_in_the_draft_and_only_paused_once_live(db):
    uid, other = _user(db), _user(db)
    client = _as(uid)
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid)}).json()['id']
    url = f'/v1/campaigns/{cid}/allocation'

    assert client.get(f'/v1/campaigns/{cid}').json()['b_percent'] == 50
    assert client.put(url, json={'b_percent': 30}).json()['b_percent'] == 30
    for bad in ({'b_percent': 35}, {'b_percent': 0}, {'b_percent': 30, 'paused': True}, {}):
        assert client.put(url, json=bad).status_code == 422, bad
    assert client.put(url, json={'paused': True}).json()['detail']['code'] == 'CAMPAIGN.INVALID_TRANSITION'
    assert _as(other).put(url, json={'b_percent': 40}).status_code == 404

    db.execute("UPDATE outreach_campaigns SET status = 'active', activated_at = now() WHERE id = %s", (cid,))
    client = _as(uid)
    late = client.put(url, json={'b_percent': 40})
    assert (late.status_code, late.json()['detail']['code']) == (409, 'CAMPAIGN.NOT_DRAFT')
    paused = client.put(url, json={'paused': True}).json()
    assert paused['allocation_paused_at'] is not None and paused['b_percent'] == 30
    assert client.put(url, json={'paused': True}).json()['allocation_paused_at'] == paused['allocation_paused_at']
    assert client.put(url, json={'paused': False}).json()['allocation_paused_at'] is None


def test_backfill_migration_fills_names_from_full_name(db):
    """#235: the backfill migration gives targets enrolled before the fix their names from the list's full_name."""
    uid = _user(db)
    people = [
        {'full_name': 'Dott.ssa Eva De Luca', 'email': 'Eva@Client.test'},
        {'full_name': 'dott. ing. Mario Rossi', 'email': 'mario@client.test'},
        {'full_name': 'Ing Chen', 'linkedin_url': 'https://www.linkedin.com/in/chen'},
        {'full_name': 'Anna', 'email': 'anna@client.test'},
        {'full_name': 'Dr.', 'email': 'dr@client.test'},
        {'full_name': 'Ugo Verdi', 'last_name': 'Kept', 'email': 'ugo@client.test'},
        {'email': 'noname@client.test'},  # nothing to take a name from
    ]
    lid = _list(db, uid, rows=people)
    cid = db.execute(
        "INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, list_id) VALUES (default_organization(%s), %s, %s, 'C', %s) RETURNING id",
        (uid, project(db, uid), uid, lid),
    ).fetchone()['id']

    def enroll(address=None, linkedin_url='', first_name='', last_name=''):
        db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, first_name, last_name)'
            ' VALUES (%s, %s, %s, %s, %s, %s)',
            (cid, uid, address, linkedin_url, first_name, last_name),
        )

    for cells in people:  # enrolled the old way: names copied from first_name/last_name only
        enroll(
            (cells.get('email') or '').lower() or None, cells.get('linkedin_url', ''), '', cells.get('last_name', '')
        )
    enroll('set@client.test', first_name='Already', last_name='Set')
    enroll('gone@client.test')  # not in the list any more

    db.execute((MIGRATIONS / BACKFILL).read_text())  # the fixture ran it on an empty schema: run it on this data
    names = {
        t['email'] or t['linkedin_url']: (t['first_name'], t['last_name'])
        for t in db.execute('SELECT * FROM outreach_campaign_targets').fetchall()
    }
    for cells in people:  # the SQL split is the one enrollment uses now
        key = (cells.get('email') or '').lower() or cells['linkedin_url']
        assert names[key] == first_and_last(cells), cells
    assert names['eva@client.test'] == ('Eva', 'De Luca')
    assert names['ugo@client.test'] == ('Ugo', 'Kept')
    assert names['set@client.test'] == ('Already', 'Set')
    assert names['gone@client.test'] == ('', '')


def test_approval_queue_approve_reject_edit(db, key):
    uid, other = _user(db), _user(db)
    _mailbox(db, uid)
    client = _as(uid)
    rows = [{'first_name': n, 'email': f'{n}@client.test', 'company_name': 'Client'} for n in ('ann', 'bob', 'cat')]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    url = f'/v1/campaigns/{cid}'
    client.patch(url, json={'steps': STEPS})
    client.post(f'{url}/activate')
    each_org(db, dispatcher.plan, datetime.now(UTC) + timedelta(minutes=1))

    queue = client.get(f'{url}/approvals').json()['items']
    assert len(queue) == 3 and client.get(url).json()['awaiting_approval'] == 3
    ids = {q['first_name']: q['id'] for q in queue}
    assert queue[0]['body'].startswith('Ciao ') and queue[0]['subject'].startswith('Ciao ')

    # Another owner can neither see nor act on this queue, not even through their own campaign id.
    victim = list(ids.values())
    assert _as(other).post(f'{url}/approvals/approve', json={'send_ids': victim}).status_code == 404
    attacker = _as(other)
    own = attacker.post('/v1/campaigns', json={'name': 'A', 'list_id': _list(db, other)}).json()['id']
    assert attacker.post(f'/v1/campaigns/{own}/approvals/approve', json={'send_ids': victim}).json() == {
        'approved': 0,
        'held': 0,
    }
    assert attacker.post(f'/v1/campaigns/{own}/approvals/reject', json={'send_ids': victim}).json() == {'rejected': 0}
    hijack = attacker.patch(f'/v1/campaigns/{own}/approvals/{victim[0]}', json={'subject': 'x', 'body': 'y'})
    assert hijack.status_code == 404
    assert {r['status'] for r in db.execute('SELECT status FROM outreach_campaign_sends')} == {'awaiting_approval'}
    client = _as(uid)

    held = client.patch(f'{url}/approvals/{ids["ann"]}', json={'subject': 'Hi [Name]', 'body': 'x'})
    assert held.status_code == 422 and held.json()['detail']['code'] == 'CAMPAIGN.PLACEHOLDER'
    header = client.patch(f'{url}/approvals/{ids["ann"]}', json={'subject': 'Hi\r\nBcc: x@evil.test', 'body': 'x'})
    assert header.json()['detail']['code'] == 'CAMPAIGN.SUBJECT_LINE_BREAK'
    too_long = client.patch(f'{url}/approvals/{ids["ann"]}', json={'subject': 's' * 301, 'body': 'x'})
    assert too_long.json()['detail']['code'] == 'CAMPAIGN.COPY_TOO_LONG'
    edited = client.patch(f'{url}/approvals/{ids["ann"]}', json={'subject': 'Hi Ann', 'body': 'Rewritten'})
    assert edited.json()['body'] == 'Rewritten'

    assert client.post(f'{url}/approvals/approve', json={'send_ids': [ids['ann'], ids['bob']]}).json() == {
        'approved': 2,
        'held': 0,
    }
    assert client.post(f'{url}/approvals/approve', json={'send_ids': [ids['ann']]}).json() == {
        'approved': 0,
        'held': 0,
    }
    assert client.post(f'{url}/approvals/reject', json={'send_ids': [ids['cat']]}).json() == {'rejected': 1}
    sends = {s['recipient']: s for s in db.execute('SELECT * FROM outreach_campaign_sends')}
    assert sends['ann@client.test']['status'] == 'approved' and sends['ann@client.test']['subject'] == 'Hi Ann'
    assert sends['cat@client.test']['status'] == 'skipped' and sends['cat@client.test']['error_code'] == 'REJECTED'
    steps = {t['email']: t['next_step'] for t in db.execute('SELECT email, next_step FROM outreach_campaign_targets')}
    assert steps == {'ann@client.test': 1, 'bob@client.test': 1, 'cat@client.test': 2}  # rejected moves on
    assert client.patch(f'{url}/approvals/{ids["ann"]}', json={'subject': 'a', 'body': 'b'}).status_code == 404
    assert client.get(f'{url}/approvals').json()['items'] == []

    # Only terminal failures count: a `failed` row with attempts left is still retried.
    db.execute("UPDATE outreach_campaign_sends SET status = 'failed', attempts = 1 WHERE recipient = 'ann@client.test'")
    db.execute("UPDATE outreach_campaign_sends SET status = 'refused' WHERE recipient = 'bob@client.test'")
    assert client.get(url).json()['failed'] == 1


def test_approval_queue_tags_each_message_with_its_persons_variant(db, key):
    uid = _user(db)
    _mailbox(db, uid)
    client = _as(uid)
    names = ('ann', 'bob', 'cat', 'dan')
    rows = [{'first_name': n, 'email': f'{n}@client.test', 'company_name': 'Client'} for n in names]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    url = f'/v1/campaigns/{cid}'
    client.patch(url, json={'steps': [STEPS[0] | {'b': {'subject': 'Due minuti?', 'body': 'Copia B'}}, STEPS[1]]})
    client.post(f'{url}/activate')
    each_org(db, dispatcher.plan, datetime.now(UTC) + timedelta(minutes=1))

    assigned = {t['first_name']: t['variant'] for t in client.get(f'{url}/targets').json()['items']}
    queue = client.get(f'{url}/approvals').json()['items']
    assert sorted(assigned.values()) == ['a', 'a', 'b', 'b']
    assert {q['first_name']: q['variant'] for q in queue} == assigned
    assert all((q['subject'] == 'Due minuti?') == (q['variant'] == 'b') for q in queue)


def test_reject_undo_restores_until_the_worker_moves(db, key):
    uid, other = _user(db), _user(db)
    _mailbox(db, uid)
    client = _as(uid)
    rows = [{'first_name': n, 'email': f'{n}@client.test', 'company_name': 'Client'} for n in ('ann', 'bob', 'cat')]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    url = f'/v1/campaigns/{cid}'
    client.patch(url, json={'steps': STEPS})
    client.post(f'{url}/activate')
    each_org(db, dispatcher.plan, datetime.now(UTC) + timedelta(minutes=1))
    ids = {q['first_name']: q['id'] for q in client.get(f'{url}/approvals').json()['items']}
    assert client.post(f'{url}/approvals/reject', json={'send_ids': list(ids.values())}).json() == {'rejected': 3}

    def state(name):
        return db.execute(
            'SELECT s.status, s.error_code, t.next_step FROM outreach_campaign_sends s'
            ' JOIN outreach_campaign_targets t ON t.id = s.target_id WHERE s.id = %s',
            (ids[name],),
        ).fetchone()

    # Another owner cannot undo, not even through their own campaign id.
    own = _as(other).post('/v1/campaigns', json={'name': 'A', 'list_id': _list(db, other)}).json()['id']
    assert _as(other).post(f'{url}/approvals/undo-reject', json={'send_ids': [ids['ann']]}).status_code == 404
    undo = _as(other).post(f'/v1/campaigns/{own}/approvals/undo-reject', json={'send_ids': [ids['ann']]})
    assert undo.json() == {'restored': 0}

    client = _as(uid)
    assert client.post(f'{url}/approvals/undo-reject', json={'send_ids': [ids['ann']]}).json() == {'restored': 1}
    assert state('ann') == {'status': 'awaiting_approval', 'error_code': None, 'next_step': 1}
    assert [q['id'] for q in client.get(f'{url}/approvals').json()['items']] == [ids['ann']]
    assert client.post(f'{url}/approvals/undo-reject', json={'send_ids': [ids['ann']]}).json() == {'restored': 0}

    # The worker moved on: bob's step 2 is claimed. A reply stopped cat. Neither comes back.
    each_org(db, dispatcher.plan, datetime.now(UTC) + timedelta(days=4))
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_sends WHERE step = 2').fetchone()['n'] == 2
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE email = 'cat@client.test'")
    body = {'send_ids': [ids['bob'], ids['cat']]}
    assert client.post(f'{url}/approvals/undo-reject', json=body).json() == {'restored': 0}
    # Step 2 replies in a thread step 1 never started, so the worker skipped it (NO_THREAD) and moved bob on.
    assert state('bob') == {'status': 'skipped', 'error_code': 'REJECTED', 'next_step': 3}

    # A planner that read ann one step on, before the undo committed, cannot claim that step any more.
    stale = db.execute(
        'SELECT t.*, c.project_id FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
        " WHERE t.email = 'ann@client.test'"
    ).fetchone()
    assert (
        dispatcher._claim(db, {**stale, 'next_step': 2}, 2, 'awaiting_approval', recipient='ann@client.test') is False
    )
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_sends WHERE step = 2').fetchone()['n'] == 2


def test_completion_waits_for_an_undo_in_flight(db):
    uid = _user(db)
    cid = db.execute(
        "INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps) VALUES (default_organization(%s), %s, %s, 'C', 'active', %s) RETURNING id",
        (uid, project(db, uid), uid, json.dumps(STEPS[:1])),
    ).fetchone()['id']
    tid = db.execute(  # rejected on the only step, so past it: the campaign looks finished
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, next_step)'
        " VALUES (%s, %s, 'a@c.test', 'active', 2) RETURNING id",
        (cid, uid),
    ).fetchone()['id']
    sid = db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, error_code,'
        " recipient, idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'skipped', 'REJECTED', 'a@c.test', 'k')"
        ' RETURNING id',
        (cid, uid, tid),
    ).fetchone()['id']

    # An undo mid-way, as undo_reject does it: the campaign lock first, then the person put back.
    with psycopg.connect(conninfo_of(db), row_factory=dict_row) as undo:
        undo.execute('SELECT 1 FROM outreach_campaigns WHERE id = %s FOR NO KEY UPDATE', (cid,))
        undo.execute('UPDATE outreach_campaign_targets SET next_step = 1 WHERE id = %s', (tid,))
        undo.execute(
            "UPDATE outreach_campaign_sends SET status = 'awaiting_approval', error_code = NULL WHERE id = %s", (sid,)
        )
        with ThreadPoolExecutor(1) as pool:
            completing = pool.submit(each_org, db, dispatcher.complete_finished)
            wait_for_a_lock(conninfo_of(db), 'the completion', db.info.backend_pid)
            assert not completing.done()  # waits on the campaign lock instead of completing on what it read
            undo.commit()
            assert completing.result(timeout=10) == 0
    assert db.execute('SELECT status FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()['status'] == 'active'


def test_test_email_goes_to_the_senders_own_mailbox(db, key, monkeypatch):
    uid = _user(db)
    _mailbox(db, uid)
    sent: list[email.message.Message] = []

    class Client:
        def __init__(self, provider):
            pass

        async def refresh(self, token):
            return ok(TokenGrant(access_token='fresh-token', refresh_token=None, granted_scopes=()))

    async def send(provider, *, access_token, raw, thread_id=''):
        sent.append(email.message_from_bytes(raw))
        return ok(ProviderReceipt(message_id='g1', thread_id='th1'))

    monkeypatch.setattr(dispatcher, 'OAuthClient', Client)
    monkeypatch.setattr(mailbox_sender, 'send', send)
    spent: list[str] = []
    monkeypatch.setattr(test_email_routes, '_rate_limit', spent.append)
    client = _as(uid)
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid)}).json()['id']
    url = f'/v1/campaigns/{cid}'
    client.patch(url, json={'steps': STEPS})

    (mailbox,) = client.get(f'{url}/senders').json()['senders']  # a new campaign starts with it
    client.put(f'{url}/senders', json={'sender_ids': []})
    assert client.post(f'{url}/test-email', json={'step': 1}).json()['detail']['code'] == 'CAMPAIGN.NO_SENDER'
    client.put(f'{url}/senders', json={'sender_ids': [mailbox['id']]})
    assert client.post(f'{url}/test-email', json={'step': 3}).status_code == 422
    assert client.post(f'{url}/test-email', json={'step': 1}).json() == {'to': 'me@acme.test'}
    (msg,) = sent
    assert msg['To'] == 'me@acme.test' and msg['Subject'] == '[Test] Ciao Sample'
    assert 'Ciao Sample di Esempio S.r.l.' in msg.get_payload() and 'Marco' in msg.get_payload()
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_sends').fetchone()['n'] == 0
    client.patch(url, json={'steps': [STEPS[0] | {'b': {'subject': 'B per {{firstName}}', 'body': 'Copia B'}}]})
    assert client.post(f'{url}/test-email', json={'step': 1, 'variant': 'b'}).json() == {'to': 'me@acme.test'}
    assert sent[-1]['Subject'] == '[Test] B per Sample' and 'Copia B' in sent[-1].get_payload()
    sent.pop()
    client.patch(url, json={'steps': STEPS})

    # A header the message cannot carry is a 422, not a 500.
    db.execute("UPDATE user_oauth_connections SET display_name = 'Marco\nBcc: x@evil.test'")
    assert client.post(f'{url}/test-email', json={'step': 1}).json()['detail']['code'] == 'CAMPAIGN.INVALID_CONTENT'
    # A grant without the send scope is refused before any token refresh or provider call.
    db.execute("UPDATE user_oauth_connections SET granted_scopes = '{openid}'")
    before = len(spent)
    assert client.post(f'{url}/test-email', json={'step': 1}).json()['detail']['code'] == 'CAMPAIGN.MAILBOX_UNAVAILABLE'
    assert len(sent) == 1 and len(spent) == before  # a refused grant spends no test-email quota

    # A provider outage asks to retry and leaves the mailbox connected; a revoked grant asks to reconnect.
    db.execute("UPDATE user_oauth_connections SET granted_scopes = '{https://www.googleapis.com/auth/gmail.send}'")
    refusal = {}

    class Refused:
        def __init__(self, provider):
            pass

        async def refresh(self, token):
            return err(refusal['code'])

    monkeypatch.setattr(dispatcher, 'OAuthClient', Refused)
    status = lambda: db.execute('SELECT status FROM user_oauth_connections').fetchone()['status']  # noqa: E731
    refusal['code'] = 'INTEGRATIONS.HTTP_ERROR'
    res = client.post(f'{url}/test-email', json={'step': 1})
    assert (res.status_code, res.json()['detail']['code']) == (503, 'MAILBOX.TEMPORARILY_UNAVAILABLE')
    assert status() == 'connected'
    refusal['code'] = 'INTEGRATIONS.AUTH'
    res = client.post(f'{url}/test-email', json={'step': 1})
    assert (res.status_code, res.json()['detail']['code']) == (409, 'CAMPAIGN.MAILBOX_UNAVAILABLE')
    assert status() == 'error'


def test_detail_stats_and_targets(db, key):
    uid, other = _user(db), _user(db)
    _mailbox(db, uid)
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, activated_at)'
        " VALUES (default_organization(%s), %s, %s, 'C', 'active', %s, now()) RETURNING id",
        (uid, project(db, uid), uid, json.dumps(STEPS)),
    ).fetchone()['id']
    fill_pool(db, cid, ('google',))

    def target(name, status='active', next_step=1):
        return db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, last_name, status,'
            ' next_step) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
            (cid, uid, f'{name}@c.test', name, 'Z', status, next_step),
        ).fetchone()['id']

    def send(tid, step, status, code=None, sent=False, channel='email'):
        db.execute(
            'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,'
            ' error_code, idempotency_key, attempted_at, delivered_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,'
            ' now(), %s)',
            (
                cid,
                uid,
                tid,
                step,
                channel,
                status,
                'x@c.test',
                code,
                f'{tid}:{step}',
                datetime.now(UTC) if sent else None,
            ),
        )

    def reply(tid, kind, label=None, ago=0):
        return db.execute(
            'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id,'
            " campaign_id, target_id, label, received_at) VALUES (%s, %s, 'email', %s, %s, %s, %s, %s,"
            " now() - %s * interval '1 hour') RETURNING id",
            (uid, project(db, uid), kind, uuid.uuid4().hex, cid, tid, label, ago),
        ).fetchone()['id']

    ann, bea, cat, dan = (
        target('Ann', 'replied', 2),
        target('Bea', next_step=2),
        target('Cat'),
        target('Dan', next_step=2),
    )
    send(ann, 1, 'sent', sent=True)
    send(bea, 1, 'sent', sent=True)
    send(cat, 1, 'awaiting_approval')
    send(dan, 1, 'refused', 'INVALID_EMAIL')
    send(bea, 2, 'sent', sent=True, channel='linkedin_connect')  # not held against the mailboxes' cap
    reply(ann, 'reply', 'interested', ago=2)
    newest = reply(ann, 'reply', 'meeting_request', ago=1)
    reply(bea, 'auto_reply')

    body = _as(uid).get(f'/v1/campaigns/{cid}').json()
    assert (body['sent_today'], body['interested'], body['auto_replies'], body['invalid_email']) == (2, 1, 1, 1)
    assert body['daily_cap'] == sending.daily_cap(None, datetime.now(UTC) - timedelta(days=30), datetime.now(UTC))
    assert (body['send_window_start_hour'], body['send_window_end_hour'], body['send_window_days']) == (
        9, 18, [0, 1, 2, 3, 4],
    )  # fmt: skip

    items = _as(uid).get(f'/v1/campaigns/{cid}/targets').json()['items']
    by_name = {i['first_name']: i for i in items}
    assert [i['first_name'] for i in items][:2] == ['Bea', 'Ann']  # newest activity first
    assert by_name['Ann']['message_id'] == str(newest) and by_name['Ann']['message_label'] == 'meeting_request'
    assert by_name['Ann']['last_sent_step'] == 1 and by_name['Ann']['status'] == 'replied'
    assert by_name['Cat']['awaiting_approval'] and not by_name['Cat']['invalid_email']
    assert by_name['Dan']['invalid_email'] and by_name['Dan']['last_sent_step'] is None
    assert _as(other).get(f'/v1/campaigns/{cid}/targets').status_code == 404


def test_a_patch_ignores_a_stale_sender_pool_and_keeps_the_pool(db, key):
    uid = _user(db)
    mailbox = _mailbox(db, uid)
    client = _as(uid)
    rows = [{'first_name': 'ann', 'email': 'ann@client.test'}]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']

    saved = client.patch(f'/v1/campaigns/{cid}', json={'name': 'D', 'sender_pool': []})

    assert saved.status_code == 200 and saved.json()['name'] == 'D' and 'sender_pool' not in saved.json()
    pool = db.execute('SELECT sender_id::text FROM outreach_campaign_senders WHERE campaign_id = %s', (cid,))
    assert [r['sender_id'] for r in pool] == [mailbox]


def _campaign_with_held(db, uid, steps, channels=('email',), name='ann'):
    """An active campaign with one person per channel, each holding an awaiting_approval send; returns
    ``(client, url, ids by channel)``."""
    client = _as(uid)
    rows = [{'first_name': name, 'email': f'{name}@client.test', 'company_name': 'Client'}]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    client.patch(f'/v1/campaigns/{cid}', json={'steps': steps})
    db.execute("UPDATE outreach_campaigns SET status = 'active' WHERE id = %s", (cid,))
    ids = {}
    for n, channel in enumerate(channels, 1):
        tid = db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, last_name, status, next_step)'
            " VALUES (%s, %s, %s, %s, 'Z', 'active', %s) RETURNING id",
            (cid, uid, f'{name}{n}@client.test', f'{name}{n}', n),
        ).fetchone()['id']
        ids[channel] = db.execute(
            'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,'
            " subject, body, idempotency_key) VALUES (%s, %s, %s, %s, %s, 'awaiting_approval', %s, %s, %s, %s)"
            ' RETURNING id',
            (
                cid,
                uid,
                tid,
                n,
                channel,
                f'{name}{n}@client.test',
                'Subj' if channel == 'email' else '',
                'Hello',
                f'k{n}',
            ),
        ).fetchone()['id']
    return client, f'/v1/campaigns/{cid}', {k: str(v) for k, v in ids.items()}


MIXED = [
    {'step': 1, 'channel': 'email', 'subject': 'S', 'body': 'Ciao {{firstName}}', 'send_after_days': 0},
    {'step': 2, 'channel': 'linkedin_connect', 'body': '', 'send_after_days': 1},
    {'step': 3, 'channel': 'linkedin_message', 'body': 'Hi', 'send_after_days': 2},
]


def test_preview_shows_the_sign_off_and_footer_the_send_appends(db, key):
    uid = _user(db)
    _mailbox(db, uid)
    client, url, _ = _campaign_with_held(db, uid, MIXED)
    for response in (
        client.get(f'{url}/preview').json(),
        client.post(
            f'{url}/preview', json={'steps': [{k: v for k, v in s.items() if k != 'step'} for s in MIXED]}
        ).json(),
    ):
        email_step, connect, message = response['items'][0]['steps']
        assert email_step['sign_off'] == 'Marco'
        assert (connect['sign_off'], connect['footer'], message['sign_off'], message['footer']) == ('', '', '', '')
        sent = sending.compose_body(email_step['body'], 'Marco')
        assert sent == f'{email_step["body"]}\n\n{email_step["sign_off"]}\n\n---\n{email_step["footer"]}'
    # The preview must not carry a live unsubscribe link.
    assert 'Oppure' not in email_step['footer']


def test_linkedin_edit_limits_and_email_unchanged(db):
    uid = _user(db)
    client, url, ids = _campaign_with_held(db, uid, MIXED, channels=('email', 'linkedin_connect', 'linkedin_message'))
    note, dm, mail = (f'{url}/approvals/{ids[k]}' for k in ('linkedin_connect', 'linkedin_message', 'email'))

    ok = client.patch(note, json={'body': 'x' * 300})
    assert ok.status_code == 200 and ok.json()['body'] == 'x' * 300 and ok.json()['subject'] == ''
    assert client.patch(note, json={'body': 'x' * 301}).json()['detail']['code'] == 'CAMPAIGN.COPY_TOO_LONG'
    assert client.patch(note, json={'subject': 'ignored', 'body': ''}).json()['subject'] == ''
    assert client.patch(dm, json={'body': 'y' * 4000}).status_code == 200
    assert client.patch(dm, json={'body': 'y' * 4001}).json()['detail']['code'] == 'CAMPAIGN.COPY_TOO_LONG'
    assert client.patch(dm, json={'body': '  '}).status_code == 422
    assert client.patch(dm, json={'body': 'Hi [Name]'}).json()['detail']['code'] == 'CAMPAIGN.PLACEHOLDER'
    assert client.patch(mail, json={'subject': '', 'body': 'x'}).status_code == 422
    edited = client.patch(mail, json={'subject': 'Hi', 'body': 'z'}).json()
    assert (edited['subject'], edited['body']) == ('Hi', 'z')


def test_approvals_say_when_and_from_where(db, key):
    uid = _user(db)
    _mailbox(db, uid, display_name='Marco R')
    sender(db, uid, 'unipile', account_label='li-acct', display_name='Marco Rossi', status='connected')
    client, url, ids = _campaign_with_held(db, uid, MIXED, channels=('email', 'linkedin_connect'))
    items = {i['channel']: i for i in client.get(f'{url}/approvals').json()['items']}
    assert items['email']['sender'] == {'address': 'me@acme.test', 'name': 'Marco R'}
    assert items['linkedin_connect']['sender'] == {'address': None, 'name': 'Marco Rossi'}
    assert 'target_id' not in items['email'] and 'target_sender' not in items['email']
    now = datetime.now(UTC)
    for item in items.values():
        send_at = datetime.fromisoformat(item['send_at'])
        assert send_at >= now - timedelta(seconds=5) and send_at <= now + timedelta(days=8)
    edited = client.patch(f'{url}/approvals/{ids["email"]}', json={'subject': 'a', 'body': 'b'}).json()
    assert edited['sender']['address'] == 'me@acme.test' and edited['send_at']
    db.execute('UPDATE user_oauth_connections SET send_window_start_hour = 17 WHERE provider = %s', ('google',))
    (email,) = [i for i in client.get(f'{url}/approvals').json()['items'] if i['channel'] == 'email']
    assert datetime.fromisoformat(email['send_at']).astimezone(ZoneInfo('Europe/Rome')).hour == 17


def test_inbox_filters_by_campaign_and_detail_counts_unread(db, monkeypatch):
    from app.outreach import inbox_routes

    monkeypatch.setattr(inbox_routes, 'get_conn', scoped_conn(db))
    uid = _user(db)
    client, url, _ = _campaign_with_held(db, uid, MIXED)
    other = client.post('/v1/campaigns', json={'name': 'D', 'list_id': _list(db, uid)}).json()['id']
    cid = url.rsplit('/', 1)[1]
    pid = project(db, uid)

    def reply(campaign, read):
        db.execute(
            'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id,'
            " campaign_id, received_at, read_at) VALUES (%s, %s, 'email', 'reply', %s, %s, now(), %s)",
            (uid, pid, uuid.uuid4().hex, campaign, datetime.now(UTC) if read else None),
        )

    reply(cid, False)
    reply(cid, False)
    reply(cid, True)
    reply(other, False)
    page = client.get(f'/v1/inbox?campaign_id={cid}').json()
    assert len(page['messages']) == 3 and {m['campaign_id'] for m in page['messages']} == {cid}
    assert (page['counts']['all'], page['counts']['unread']) == (3, 2)
    both = client.get(f'/v1/inbox?campaign_id={cid}&unread=true').json()
    assert len(both['messages']) == 2
    assert client.get('/v1/inbox').json()['counts']['all'] == 4
    assert client.get(url).json()['unread_replies'] == 2
    assert client.get(f'/v1/campaigns/{other}').json()['unread_replies'] == 1


def test_a_soft_deleted_list_leaves_no_list_behind_the_campaign(db):
    uid = _user(db)
    lid = _list(db, uid, rows=[{'first_name': 'A', 'email': 'a@c.test'}])
    client = _as(uid)
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': lid}).json()['id']
    assert client.get(f'/v1/campaigns/{cid}').json()['list_id'] == lid

    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (lid,))
    for body in (client.get(f'/v1/campaigns/{cid}').json(), client.get('/v1/campaigns').json()['campaigns'][0]):
        assert (body['list_id'], body['list_name'], body['chat_session_id']) == (None, None, None)


def test_activity_feed_is_newest_first_paged_and_scoped(db):
    uid, other = _user(db), _user(db)
    pid = project(db, uid)
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, activated_at)'
        " VALUES (default_organization(%s), %s, %s, 'C', 'active', %s, now()) RETURNING id",
        (uid, pid, uid, json.dumps(STEPS)),
    ).fetchone()['id']
    tid = db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, last_name, status)'
        " VALUES (%s, %s, 'a@c.test', 'Ann', 'Z', 'active') RETURNING id",
        (cid, uid),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,'
        " idempotency_key, created_at, approved_at, delivered_at) VALUES (%s, %s, %s, 1, 'email', 'sent', 'a@c.test', %s,"
        " now() - interval '4 hour', now() - interval '3 hour', now() - interval '2 hour')",
        (cid, uid, tid, uuid.uuid4().hex),
    )
    db.execute(
        'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id,'
        " campaign_id, target_id, label, received_at) VALUES (%s, %s, 'email', 'reply', %s, %s, %s, 'interested',"
        " now() - interval '1 hour')",
        (uid, pid, uuid.uuid4().hex, cid, tid),
    )
    url = f'/v1/campaigns/{cid}/activity'
    first = _as(uid).get(url, params={'limit': 2}).json()
    assert [(i['kind'], i['first_name']) for i in first['items']] == [('reply', 'Ann'), ('sent', 'Ann')]
    assert first['items'][0]['label'] == 'interested' and first['items'][1]['step'] == 1
    rest = _as(uid).get(url, params={'after': first['next_cursor']}).json()
    assert [i['kind'] for i in rest['items']] == ['approved'] and rest['next_cursor'] is None
    assert _as(other).get(url).status_code == 404  # another organization
    second = _as(uid).post('/v1/projects', json={'name': 'Other'}).json()['project']['id']
    assert _as(uid).get(url, headers={'X-Project-Id': second}).status_code == 404  # another project


def test_activity_feed_ties_task_channel_and_auto_approval(db):
    uid = _user(db)
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, activated_at)'
        " VALUES (default_organization(%s), %s, %s, 'C', 'active', %s, now()) RETURNING id",
        (uid, project(db, uid), uid, json.dumps(STEPS)),
    ).fetchone()['id']
    for i in range(2):  # auto-approved at insert (approved_at = created_at), and a CRM task
        tid = db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, last_name, status)'
            " VALUES (%s, %s, %s, 'P', 'Z', 'active') RETURNING id",
            (cid, uid, f'p{i}@c.test'),
        ).fetchone()['id']
        db.execute(
            'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,'
            " idempotency_key, created_at, approved_at, delivered_at) VALUES (%s, %s, %s, 1, %s, 'sent', 'x', %s,"
            ' now(), now(), now())',
            (cid, uid, tid, 'hubspot_task' if i else 'email', uuid.uuid4().hex),
        )
    url = f'/v1/campaigns/{cid}/activity'
    items = _as(uid).get(url).json()['items']
    assert sorted(i['kind'] for i in items) == ['sent', 'sent'] and 'hubspot_task' in {i['channel'] for i in items}

    # five events at one instant, two per page: none lost, none repeated
    t = db.execute('SELECT now() AS t').fetchone()['t']
    db.execute(
        "UPDATE outreach_campaign_sends SET created_at = %s - interval '1 second', approved_at = %s, delivered_at = %s",
        (t, t, t),
    )
    for _ in range(3):
        db.execute(
            'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id,'
            " campaign_id, received_at) VALUES (%s, %s, 'email', 'reply', %s, %s, %s)",
            (uid, project(db, uid), uuid.uuid4().hex, cid, t),
        )
    seen, cursor = [], None
    while True:
        page = _as(uid).get(url, params={'limit': 2, **({'after': cursor} if cursor else {})}).json()
        seen += [(i['id'], i['kind']) for i in page['items']]
        cursor = page['next_cursor']
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 7  # 2 sent + 2 approved + 3 replies
    assert _as(uid).get(url, params={'after': 'nope'}).status_code == 422


def test_approve_leaves_a_person_the_hold_keeps_waiting_and_counts_them(db, key):
    uid = _user(db)
    _mailbox(db, uid)
    client = _as(uid)
    rows = [{'first_name': n, 'email': f'{n}@client.test', 'company_name': 'Client'} for n in ('ann', 'bob')]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    url = f'/v1/campaigns/{cid}'
    client.patch(url, json={'steps': STEPS})
    client.post(f'{url}/activate')
    each_org(db, dispatcher.plan, datetime.now(UTC) + timedelta(minutes=1))
    ids = {q['first_name']: q['id'] for q in client.get(f'{url}/approvals').json()['items']}
    lead(db, project(db, uid), 'ann@client.test')

    out = client.post(f'{url}/approvals/approve', json={'send_ids': [ids['ann'], ids['bob']]}).json()
    assert out == {'approved': 1, 'held': 1}
    statuses = {
        r['recipient']: r['status'] for r in db.execute('SELECT recipient, status FROM outreach_campaign_sends')
    }
    assert statuses == {'ann@client.test': 'awaiting_approval', 'bob@client.test': 'approved'}


def test_a_held_message_shows_its_hold_and_counts_apart_from_the_approvals(db, key):
    from tests.test_holds_sql import stop

    uid = _user(db)
    client, url, ids = _campaign_with_held(db, uid, MIXED, channels=('email', 'linkedin_connect'))
    pid = project(db, uid)
    lead(db, pid, 'ann1@client.test')
    lead(db, pid, 'ann2@client.test')

    def seen():
        holds = {q['id']: q['hold'] for q in client.get(f'{url}/approvals').json()['items']}
        detail = client.get(url).json()
        waiting = {t['first_name']: t['awaiting_approval'] for t in client.get(f'{url}/targets').json()['items']}
        return holds, (detail['awaiting_approval'], detail['awaiting_approval_held']), waiting

    assert seen() == (
        {ids['email']: 'held_by_instantly', ids['linkedin_connect']: None},
        (1, 1),
        {'ann': False, 'ann1': False, 'ann2': True},
    )
    stop(db, pid, 'ann2@client.test')
    assert seen() == (
        {ids['email']: 'held_by_instantly', ids['linkedin_connect']: 'stop_pending'},
        (0, 2),
        {'ann': False, 'ann1': False, 'ann2': False},
    )


def test_activation_drops_a_draft_person_the_hold_keeps(db, key):
    uid = _user(db)
    _mailbox(db, uid)
    client = _as(uid)
    rows = [{'first_name': n, 'email': f'{n}@client.test', 'company_name': 'Client'} for n in ('ann', 'bob')]
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()['id']
    client.patch(f'/v1/campaigns/{cid}', json={'steps': STEPS})
    lead(db, project(db, uid), 'ann@client.test')
    assert client.post(f'/v1/campaigns/{cid}/activate').status_code == 200
    targets = db.execute('SELECT email, status FROM outreach_campaign_targets').fetchall()
    assert [(t['email'], t['status']) for t in targets] == [('bob@client.test', 'active')]


def test_the_parallel_campaigns_flag_is_set_at_create_and_edited_while_live(db):
    uid, stranger = _user(db), _user(db)
    rows = [{'first_name': 'anna', 'email': 'anna@client.test'}]
    client = _as(uid)
    first = client.post('/v1/campaigns', json={'name': 'A', 'list_id': _list(db, uid, rows=rows)}).json()
    assert first['block_active_duplicates'] is True
    db.execute("UPDATE outreach_campaigns SET status = 'active' WHERE id = %s", (first['id'],))

    body = {'name': 'B', 'list_id': _list(db, uid, rows=rows), 'block_active_duplicates': False}
    shared = client.post('/v1/campaigns', json=body).json()
    assert (shared['block_active_duplicates'], shared['target_count']) == (False, 1)
    blocked = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, rows=rows)}).json()
    assert blocked['target_count'] == 0

    url = f'/v1/campaigns/{first["id"]}'
    assert client.patch(url, json={'block_active_duplicates': False}).json()['block_active_duplicates'] is False
    listed = {c['id']: c['block_active_duplicates'] for c in client.get('/v1/campaigns').json()['campaigns']}
    assert listed[first['id']] is False
    assert client.patch(url, json={'name': 'A2'}).json()['block_active_duplicates'] is False

    assert _as(stranger).patch(url, json={'block_active_duplicates': True}).status_code == 404
    db.execute("UPDATE outreach_campaigns SET status = 'stopped' WHERE id = %s", (first['id'],))
    assert _as(uid).patch(url, json={'block_active_duplicates': True}).status_code == 409
    flag = db.execute('SELECT block_active_duplicates FROM outreach_campaigns WHERE id = %s', (first['id'],)).fetchone()
    assert flag['block_active_duplicates'] is False


def test_targets_say_what_holds_each_person_and_who_sends_their_email(db):
    from tests.test_holds_sql import second_project, stop

    uid = _user(db)
    pid = project(db, uid)

    def active(steps, *people):
        cid = db.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps)'
            " VALUES (default_organization(%s), %s, %s, 'C', 'active', %s) RETURNING id",
            (uid, pid, uid, json.dumps(steps)),
        ).fetchone()['id']
        for first_name, address in people:
            db.execute(
                'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, first_name, email, linkedin_url, status)'
                " VALUES (%s, %s, %s, %s, %s, 'active')",
                (cid, uid, first_name, address, f'https://www.linkedin.com/in/{first_name}/'),
            )
        items = _as(uid).get(f'/v1/campaigns/{cid}/targets').json()['items']
        return {t['first_name']: (t['hold'], t['email_sender']) for t in items}

    stop(db, pid, 'bob@client.test', 'pending')
    stop(db, pid, 'cat@client.test', 'confirmed')
    lead(db, pid, 'dan@client.test')
    stop(db, second_project(db, uid), 'fay@client.test', 'failed')
    people = [(n, f'{n}@client.test') for n in ('ann', 'bob', 'cat', 'dan', 'fay')] + [('eve', None)]
    assert active(STEPS, *people) == {
        'ann': (None, 'native'),
        'bob': ('stop_pending', 'instantly'),
        'cat': ('stopped', None),
        'dan': ('held_by_instantly', 'instantly'),
        'eve': (None, None),
        'fay': (None, 'native'),
    }
    linkedin_only = [{'step': 1, 'channel': 'linkedin_connect', 'body': '', 'send_after_days': 0}]
    assert active(linkedin_only, ('dan', 'dan@client.test'), ('ann', 'ann@client.test')) == {
        'dan': (None, 'instantly'),
        'ann': (None, None),
    }
