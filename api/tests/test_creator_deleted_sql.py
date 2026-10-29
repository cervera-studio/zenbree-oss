"""#333: deleting a member's account keeps everything the organization owns that they created; their personal
rows go, and the senders they connected are disconnected and unlinked.

Real SQL (the migrations, the FK actions and the trigger); the account is deleted the way Better Auth's
``admin/remove-user`` does it in the end: ``DELETE FROM "user"``.
"""

import email
import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.middleware import CurrentUser
from app.core.reply_address import ReplyKey, parse_reply_address
from app.core.system_reasons import SystemReason
from app.integrations import token_crypto
from app.main import app
from app.outreach import dispatcher, replies, unsubscribe
from app.outreach.domain.campaign import validate_steps
from tests.conftest import call_as, call_system, each_org, each_org_async, member, pool, sign_in
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_dispatcher_sql import GMAIL_SEND, NOW, ONE_STEP
from tests.test_dispatcher_sql import provider as provider

pytestmark = needs_db


def _org(db, uid) -> str:
    return str(db.execute('SELECT default_organization(%s) AS id', (uid,)).fetchone()['id'])


def _team(db):
    """Ada's organization with Bo as a member, and a client Bo created in it."""
    ada, bo = _user(db), _user(db)
    org, _ = _org(db, ada), _org(db, bo)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, bo),
    )
    pid = str(
        db.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Cliente di Bo') RETURNING id",
            (org, bo),
        ).fetchone()['id']
    )
    return ada, bo, org, pid


def _mailbox(db, uid, org, pid, label, provider='google') -> str:
    sid = db.execute(
        'INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,'
        ' granted_scopes, account_label, sign_off, created_at, provider_account_id) VALUES (%s, %s, %s, %s, %s,'
        " %s, 'Firma', %s, %s) RETURNING id",
        (
            org,
            uid,
            provider,
            token_crypto.encrypt('refresh-1').value,
            ['openid', GMAIL_SEND],
            label,
            NOW - timedelta(days=30),
            label,
        ),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO project_senders (project_id, sender_id, provider, organization_id) VALUES (%s, %s, %s, %s)',
        (pid, sid, provider, org),
    )
    return str(sid)


def _campaign(db, owner, org, pid, emails=('anna@client.test',)) -> str:
    cid = db.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps,
               activated_at, approve_before_sending)
           VALUES (%s, %s, %s, 'Camp di Bo', 'active', %s, %s, false) RETURNING id""",
        (org, pid, owner, json.dumps(validate_steps(ONE_STEP)), NOW - timedelta(hours=1)),
    ).fetchone()['id']
    pool(db, cid, ('google',))
    for addr in emails:
        db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status)'
            " VALUES (%s, %s, %s, 'Anna', 'Client', 'active')",
            (cid, owner, addr),
        )
    return str(cid)


def test_the_team_keeps_what_a_deleted_member_created_and_their_personal_rows_go(db, provider):
    ada, bo, org, pid = _team(db)
    bo_box, ada_box = (
        _mailbox(db, bo, org, pid, 'bo@agency.test', 'microsoft'),
        _mailbox(db, ada, org, pid, 'ada@agency.test'),
    )
    cid = _campaign(db, bo, org, pid)
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id, title) VALUES (%s, %s, %s, %s) RETURNING id',
        (org, pid, bo, 'Chat di Bo'),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO chat_messages (session_id, user_id, role, content) VALUES (%s, %s, 'user', 'ciao')", (sid, bo)
    )
    db.execute(
        'INSERT INTO jobs (organization_id, user_id, chat_session_id, status)'
        " VALUES ((SELECT organization_id FROM chat_sessions WHERE id = %s), %s, %s, 'succeeded')",
        (sid, bo, sid),
    )
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity)'
        " VALUES (%s, %s, %s, %s, 'Lista di Bo', 'person') RETURNING id",
        (org, pid, bo, sid),
    ).fetchone()['id']
    db.execute("INSERT INTO list_rows (list_id, dedupe_key, cells) VALUES (%s, 'anna@client.test', '{}')", (lid,))
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'rival.test')", (pid, bo)
    )
    db.execute(
        'INSERT INTO saved_searches (user_id, name, tool_name, tool_input, list_id, campaign_id, cadence, next_run_at)'
        " VALUES (%s, 'Refill', 'search_people', '{}', %s, %s, 'daily', now())",
        (bo, lid, cid),
    )
    db.execute(
        'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id, received_at)'
        " VALUES (%s, %s, 'email', 'reply', 'm-old', now())",
        (bo, pid),
    )
    rid = db.execute(
        "SELECT reserve_project_credits(%s, %s, %s, 10, 'chat_turn', 'j', NULL, NULL, 'x') AS r", (pid, org, bo)
    ).fetchone()['r']
    db.execute('SELECT settle_reservation_with_slack(%s, 0)', (rid,))
    spend = db.execute('SELECT project_month_spend(%s) AS s', (pid,)).fetchone()['s']
    # Bo's own rows
    db.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href) VALUES (%s, %s, 'agent_question', '/')",
        (bo, org),
    )
    db.execute("INSERT INTO user_memory_doc (user_id, body) VALUES (%s, 'mine')", (bo,))
    db.execute(
        "INSERT INTO user_api_keys (organization_id, user_id, label, key_hash) VALUES (%s, %s, 'k', %s)",
        (org, bo, uuid.uuid4().hex * 2),
    )

    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))

    for table, col, row_id in (
        ('projects', 'user_id', pid),
        ('chat_sessions', 'user_id', sid),
        ('lists', 'user_id', lid),
        ('outreach_campaigns', 'owner_id', cid),
    ):
        assert db.execute(f'SELECT {col} FROM {table} WHERE id = %s', (row_id,)).fetchone() == {col: None}, table
    for table, col in (
        ('chat_messages', 'user_id'),
        ('jobs', 'user_id'),
        ('list_rows', 'list_id'),
        ('outreach_campaign_targets', 'owner_id'),
        ('exclusions', 'user_id'),
        ('saved_searches', 'user_id'),
        ('outreach_inbound_messages', 'owner_id'),
    ):
        rows = db.execute(f'SELECT {col} FROM {table}').fetchall()
        assert len(rows) == 1, table
        if col != 'list_id':
            assert rows[0][col] is None, table
    assert db.execute('SELECT project_month_spend(%s) AS s', (pid,)).fetchone()['s'] == spend
    gone = db.execute(
        'SELECT status, status_detail, refresh_token_encrypted, user_id FROM user_oauth_connections WHERE id = %s',
        (bo_box,),
    ).fetchone()
    assert gone == {
        'status': 'disconnected',
        'status_detail': 'owner_deleted',
        'refresh_token_encrypted': '',
        'user_id': None,
    }
    linked = [r['sender_id'] for r in db.execute('SELECT sender_id::text FROM project_senders').fetchall()]
    assert linked == [ada_box]  # Bo's mailbox no longer serves the client; Ada's still does
    for table in ('member', 'notifications', 'user_memory_doc', 'user_api_keys'):
        col = '"userId"' if table == 'member' else 'user_id'
        assert db.execute(f'SELECT count(*) AS n FROM {table} WHERE {col} = %s', (bo,)).fetchone()['n'] == 0, table

    # The team reads them through the API, the creator shown as nobody.
    admin = CurrentUser(id=ada, org_id=org, org_role='admin')
    sign_in(app, admin)
    try:
        tc = TestClient(app)
        assert tc.get(f'/v1/campaigns/{cid}').status_code == 200
        assert tc.get(f'/v1/lists/{lid}').status_code == 200
        assert tc.get(f'/v1/chat/sessions/{sid}').status_code == 200
        assert tc.get('/v1/saved-searches').json()['saved_searches'][0]['user_id'] is None
    finally:
        app.dependency_overrides.clear()


async def test_a_campaign_whose_creator_is_gone_keeps_sending_and_its_replies_reach_the_client(db, provider):
    ada, bo, org, pid = _team(db)
    _mailbox(db, ada, org, pid, 'ada@agency.test')
    cid = _campaign(db, bo, org, pid, emails=('anna@client.test', 'bea@client.test'))
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))

    each_org(db, dispatcher.plan, NOW)
    assert (await each_org_async(db, dispatcher.send_due, NOW))['sent'] == 1
    ((msg, _),) = provider.sent
    assert msg['From'] == 'ada@agency.test'
    # The reply address names the campaign; the opt-out link the client, as nobody's account can be named any more.
    assert parse_reply_address(msg['Reply-To'], secret='s3cret') == ReplyKey(cid, campaign=True)
    one_click = msg['List-Unsubscribe'].split(', ')[0]
    token = one_click.removeprefix('<https://api.zenbree.test/v1/unsubscribe/').removesuffix('>')
    assert unsubscribe.parse(token, secret='s3cret') == (pid, msg['To'])

    # A reply to the client's old-form address (on mail sent before the campaign form) lands in its inbox and stops
    # the person.
    done = call_system(
        SystemReason.INBOUND_MAIL,
        replies.record,
        pid,
        channel='email',
        provider_message_id='r1',
        sender=msg['To'],
        text='Volentieri',
        references=[],
    )
    assert done['status'] == 'accepted' and done['targets'] == 1
    row = db.execute('SELECT owner_id, project_id::text, campaign_id::text FROM outreach_inbound_messages').fetchone()
    assert row == {'owner_id': None, 'project_id': pid, 'campaign_id': cid}
    assert (
        call_system(
            SystemReason.INBOUND_MAIL, replies.record, pid, channel='email', provider_message_id='r1', sender=msg['To']
        )['status']
        == 'duplicate'
    )

    # The other person opts out from the link: excluded in the client, the queued message skipped.
    other = 'bea@client.test' if msg['To'] == 'anna@client.test' else 'anna@client.test'
    assert call_system(SystemReason.OPT_OUT, replies.unsubscribe, pid, other) == 'unsubscribed'
    assert db.execute(
        "SELECT 1 FROM exclusions WHERE project_id = %s AND kind = 'email' AND value = %s", (pid, other)
    ).fetchone()
    assert call_system(SystemReason.OPT_OUT, replies.link_state, pid, other)['status'] == 'unsubscribed'


def _sent(db, cid, message_id, key) -> None:
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient, status,'
        ' body, delivered_at, message_id, idempotency_key)'
        " SELECT campaign_id, owner_id, id, 1, 'email', email, 'sent', 'x', now(), %s, %s"
        ' FROM outreach_campaign_targets WHERE campaign_id = %s',
        (message_id, key, cid),
    )


async def test_a_reply_to_the_deleted_creators_address_is_filed_only_in_their_own_campaigns(db, provider):
    """The old Reply-To names Bo's deleted id: the message may be filed only in a campaign Bo created
    (``former_owner_id``), never another organization's nor another deleted colleague's, whatever it quotes. Once
    filed, the reply stops the person in every campaign of that project, a deleted colleague's too (#931)."""
    ada, bo, org, pid = _team(db)
    _mailbox(db, ada, org, pid, 'ada@agency.test')
    cid = _campaign(db, bo, org, pid)
    # Another organization with its own campaign whose creator was deleted too, writing to the same person.
    eve, zed = _user(db), _user(db)
    org2, _ = _org(db, eve), _org(db, zed)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org2, zed),
    )
    pid2 = str(
        db.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Altro') RETURNING id", (org2, zed)
        ).fetchone()['id']
    )
    cid2 = _campaign(db, zed, org2, pid2)
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient, status,'
        ' body, delivered_at, message_id, idempotency_key)'
        " SELECT campaign_id, owner_id, id, 1, 'email', email, 'sent', 'x', now(), '<other@org2.test>', 'k2'"
        ' FROM outreach_campaign_targets WHERE campaign_id = %s',
        (cid2,),
    )
    db.execute('DELETE FROM "user" WHERE id = %s', (zed,))
    # A colleague of Bo's in the same organization, deleted too, whose paused campaign wrote to the same person.
    dee = _user(db)
    _org(db, dee)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, dee),
    )
    cid3 = _campaign(db, dee, org, pid)
    db.execute("UPDATE outreach_campaigns SET status = 'paused' WHERE id = %s", (cid3,))
    _sent(db, cid3, '<dee@org1.test>', 'k3')
    db.execute('DELETE FROM "user" WHERE id = %s', (dee,))
    each_org(db, dispatcher.plan, NOW)
    await each_org_async(db, dispatcher.send_due, NOW)  # sent while Bo was there
    ((msg, _),) = provider.sent
    assert parse_reply_address(msg['Reply-To'], secret='s3cret') == ReplyKey(cid, campaign=True)
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))

    assert db.execute('SELECT former_owner_id::text AS f FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone() == {
        'f': bo
    }
    # Bo's old-form address, on mail sent before the campaign form. Quoting only another organization's, or another
    # deleted colleague's, message: ignored, nothing recorded.
    for i, ref in enumerate(('<other@org2.test>', '<dee@org1.test>')):
        forged = call_system(
            SystemReason.INBOUND_MAIL,
            replies.record,
            bo,
            channel='email',
            provider_message_id=f'x0{i}',
            sender='mallory@evil.test',
            references=[ref],
        )
        assert forged['status'] == 'ignored'
    assert db.execute('SELECT count(*) AS n FROM outreach_inbound_messages').fetchone()['n'] == 0
    assert db.execute('SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s', (cid2,)).fetchone() == {
        'status': 'active'
    }

    done = call_system(
        SystemReason.INBOUND_MAIL,
        replies.record,
        bo,
        channel='email',
        provider_message_id='x1',
        sender=msg['To'],
        references=[msg['Message-ID']],
    )
    assert done['status'] == 'accepted' and done['targets'] == 2  # stopped: Bo's target and Dee's
    assert db.execute('SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s', (cid3,)).fetchone() == {
        'status': 'replied'
    }
    row = db.execute('SELECT owner_id, project_id::text, campaign_id::text FROM outreach_inbound_messages').fetchone()
    assert row == {'owner_id': None, 'project_id': pid, 'campaign_id': cid}
    assert db.execute('SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s', (cid2,)).fetchone() == {
        'status': 'active'
    }
    assert email.utils.parseaddr(msg['To'])[1] == 'anna@client.test'

    # The address the dispatcher minted names the campaign: a reply to it quoting nothing lands there too.
    new = call_system(
        SystemReason.INBOUND_MAIL,
        replies.record,
        None,
        campaign_id=cid,
        channel='email',
        provider_message_id='x2',
        sender=msg['To'],
    )
    assert new['status'] == 'accepted' and new['targets'] == 0  # x1 stopped them already
    filed = (
        'SELECT project_id::text AS p, campaign_id::text AS c FROM outreach_inbound_messages'
        " WHERE provider_message_id = 'x2'"
    )
    assert db.execute(filed).fetchone() == {'p': pid, 'c': cid}


def test_children_keep_their_keys_once_the_creator_is_gone(db):
    """The composite keys stop checking once the creator is NULL (MATCH SIMPLE): single-column keys still tie
    targets, sends and runs to their campaign and saved search, and the migration dropped only its five
    composites."""
    import psycopg

    _, bo, org, pid = _team(db)
    cid = _campaign(db, bo, org, pid)
    tid = db.execute('SELECT id FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient,'
        " idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'anna@client.test', 'k1')",
        (cid, bo, tid),
    )
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, 'L', 'person')"
        ' RETURNING id',
        (org, pid, bo),
    ).fetchone()['id']
    search = db.execute(
        'INSERT INTO saved_searches (user_id, name, tool_name, tool_input, list_id, campaign_id, cadence, next_run_at)'
        " VALUES (%s, 'S', 'search_people', '{}', %s, %s, 'daily', now()) RETURNING id",
        (bo, lid, cid),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO saved_search_runs (saved_search_id, user_id, scheduled_for, config) VALUES (%s, %s, now(), '{}')",
        (search, bo),
    )
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, NULL, 'x@y.z',"
            " 'active')",
            (uuid.uuid4(),),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            'INSERT INTO saved_search_runs (saved_search_id, user_id, scheduled_for, config)'
            " VALUES (%s, NULL, now(), '{}')",
            (uuid.uuid4(),),
        )
    db.execute('DELETE FROM saved_searches WHERE id = %s', (search,))
    assert db.execute('SELECT count(*) AS n FROM saved_search_runs').fetchone()['n'] == 0
    db.execute('DELETE FROM outreach_campaigns WHERE id = %s', (cid,))
    for table in ('outreach_campaign_targets', 'outreach_campaign_sends'):
        assert db.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] == 0, table

    keys = {
        (r['t'], r['def'])
        for r in db.execute(
            "SELECT conrelid::regclass::text AS t, pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE contype = 'f'"
            " AND conrelid IN ('outreach_campaign_targets'::regclass, 'outreach_campaign_sends'::regclass,"
            " 'saved_searches'::regclass, 'saved_search_runs'::regclass)"
        )
    }
    assert keys == {
        (
            'outreach_campaign_sends',
            'FOREIGN KEY (campaign_id, organization_id) REFERENCES outreach_campaigns(id, organization_id) ON DELETE CASCADE',
        ),
        (
            'outreach_campaign_sends',
            'FOREIGN KEY (campaign_id, owner_id, organization_id) REFERENCES outreach_campaigns(id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE',
        ),
        (
            'outreach_campaign_sends',
            'FOREIGN KEY (sender_id, organization_id) REFERENCES user_oauth_connections(id, organization_id) ON DELETE SET NULL (sender_id)',
        ),
        (
            'outreach_campaign_sends',
            'FOREIGN KEY (target_id, campaign_id, owner_id, organization_id) REFERENCES outreach_campaign_targets(id, campaign_id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE SET NULL (target_id)',
        ),
        (
            'outreach_campaign_sends',
            'FOREIGN KEY (target_id, organization_id) REFERENCES outreach_campaign_targets(id, organization_id) ON DELETE SET NULL (target_id)',
        ),
        (
            'outreach_campaign_targets',
            'FOREIGN KEY (campaign_id, organization_id) REFERENCES outreach_campaigns(id, organization_id) ON DELETE CASCADE',
        ),
        (
            'outreach_campaign_targets',
            'FOREIGN KEY (campaign_id, owner_id, organization_id) REFERENCES outreach_campaigns(id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE',
        ),
        (
            'outreach_campaign_targets',
            'FOREIGN KEY (signal_id, organization_id) REFERENCES signals(id, organization_id) ON DELETE SET NULL (signal_id)',
        ),
        (
            'outreach_campaign_targets',
            'FOREIGN KEY (row_id, organization_id) REFERENCES list_rows(id, organization_id) ON DELETE SET NULL (row_id)',
        ),
        (
            'outreach_campaign_targets',
            'FOREIGN KEY (sender_id, organization_id) REFERENCES user_oauth_connections(id, organization_id) ON DELETE SET NULL (sender_id)',
        ),
        (
            'saved_search_runs',
            'FOREIGN KEY (reservation_id, organization_id) REFERENCES credit_transactions(id, organization_id)',
        ),
        (
            'saved_search_runs',
            'FOREIGN KEY (saved_search_id, organization_id) REFERENCES saved_searches(id, organization_id) ON DELETE CASCADE',
        ),
        (
            'saved_search_runs',
            'FOREIGN KEY (saved_search_id, user_id, organization_id) REFERENCES saved_searches(id, user_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE',
        ),
        (
            'saved_search_runs',
            'FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE SET NULL',
        ),
        (
            'saved_searches',
            'FOREIGN KEY (campaign_id, organization_id) REFERENCES outreach_campaigns(id, organization_id) ON DELETE CASCADE',
        ),
        (
            'saved_searches',
            'FOREIGN KEY (campaign_id, user_id, organization_id) REFERENCES outreach_campaigns(id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE',
        ),
        (
            'saved_searches',
            'FOREIGN KEY (list_id, organization_id) REFERENCES lists(id, organization_id) ON DELETE CASCADE',
        ),
        (
            'saved_searches',
            'FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE SET NULL',
        ),
    }


def test_release_revokes_each_sender_at_its_provider_before_the_account_goes(db, provider, monkeypatch):
    from app.integrations import oauth_client
    from app.linkedin import unipile_client

    ada, bo, org, pid = _team(db)
    box = _mailbox(db, bo, org, pid, 'bo@agency.test')
    li = db.execute(
        'INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,'
        " provider_account_id) VALUES (%s, %s, 'unipile', '', 'acc-1') RETURNING id",
        (org, bo),
    ).fetchone()['id']
    revoked, deleted = [], []

    async def revoke(self, token):
        revoked.append(token)
        return True

    async def delete_account(self, *, account_id):
        deleted.append(account_id)

    monkeypatch.setattr(oauth_client.OAuthClient, 'revoke', revoke)
    monkeypatch.setattr(unipile_client.UnipileClient, 'delete_account', delete_account)
    monkeypatch.setattr(unipile_client.UnipileClient, 'is_configured', lambda self: True)
    db.execute('UPDATE "user" SET role = %s WHERE id = %s', ('admin', ada))  # require_admin reads it too
    sign_in(app, CurrentUser(id=ada, org_id=org, role='admin'))
    try:
        res = TestClient(app).post(f'/v1/admin/users/{bo}/before-delete')
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200 and res.json() == {'released': 2, 'cancelled_subscriptions': 0}
    assert revoked == ['refresh-1'] and deleted == ['acc-1']
    rows = db.execute(
        'SELECT status, refresh_token_encrypted FROM user_oauth_connections WHERE id = ANY(%s)', ([box, str(li)],)
    ).fetchall()
    assert rows == [{'status': 'disconnected', 'refresh_token_encrypted': ''}] * 2
    assert db.execute('SELECT count(*) AS n FROM project_senders').fetchone()['n'] == 0


def test_a_member_deleting_their_own_account_releases_only_their_senders(db, provider, monkeypatch):
    """#341: the self-delete path has no admin token, so it releases the caller's own senders, never a colleague's."""
    from app.integrations import oauth_client

    ada, bo, org, pid = _team(db)
    mine = _mailbox(db, bo, org, pid, 'bo@agency.test')
    theirs = _mailbox(db, ada, org, pid, 'ada@agency.test', provider='microsoft')
    revoked = []

    async def revoke(self, token):
        revoked.append(token)
        return True

    monkeypatch.setattr(oauth_client.OAuthClient, 'revoke', revoke)
    sign_in(app, CurrentUser(id=bo, org_id=org))
    try:
        res = TestClient(app).post('/v1/me/before-delete', json={'user_id': bo})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200 and res.json() == {'released': 1, 'cancelled_subscriptions': 0}
    assert revoked == ['refresh-1']
    rows = db.execute(
        'SELECT id::text, status FROM user_oauth_connections WHERE id = ANY(%s::uuid[])', ([mine, theirs],)
    )
    status = {r['id']: r['status'] for r in rows}
    assert status == {mine: 'disconnected', theirs: 'connected'}


async def test_an_old_unsubscribe_link_still_opts_out_after_the_creator_is_gone(db, provider):
    """GDPR: every email we sent can be opted out of, including one sent while its creator existed. The link names
    Bo's deleted id; it reaches Bo's campaigns (``former_owner_id``) and always leaves an exclusion."""
    from app.exclusions.rules import is_excluded, load_rules

    ada, bo, org, pid = _team(db)
    _mailbox(db, ada, org, pid, 'ada@agency.test')
    cid = _campaign(db, bo, org, pid, emails=('anna@client.test', 'bea@client.test'))
    each_org(db, dispatcher.plan, NOW)
    await each_org_async(db, dispatcher.send_due, NOW)  # one sent while Bo was there; the other is queued
    ((msg, _),) = provider.sent
    one_click = msg['List-Unsubscribe'].split(', ')[0]
    token = one_click.removeprefix('<https://api.zenbree.test/v1/unsubscribe/').removesuffix('>')
    other = 'bea@client.test' if msg['To'] == 'anna@client.test' else 'anna@client.test'
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))

    key, address = unsubscribe.parse(token, secret='s3cret')
    assert key == bo
    assert call_system(SystemReason.OPT_OUT, replies.link_state, key, address)['status'] == 'subscribed'
    assert call_system(SystemReason.OPT_OUT, replies.unsubscribe, key, address) == 'unsubscribed'
    assert is_excluded(load_rules(db, pid), {'email': address})
    assert db.execute(
        'SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s AND email = %s', (cid, address)
    ).fetchone() == {'status': 'excluded'}
    # The queued message to the other person is untouched; nothing more goes to the one who opted out.
    each_org(db, dispatcher.plan, NOW + timedelta(days=1))
    await each_org_async(db, dispatcher.send_due, NOW + timedelta(minutes=30))
    assert [m['To'] for m, _ in provider.sent] == [address, other]

    # An address that is no longer a target still leaves a durable exclusion, so a re-enrollment skips it.
    assert call_system(SystemReason.OPT_OUT, replies.unsubscribe, bo, 'gone@client.test') == 'unsubscribed'
    assert is_excluded(load_rules(db, pid), {'email': 'gone@client.test'})

    # An opt-out reply to the old address from somebody none of Bo's campaigns has: excluded in Bo's clients.
    done = call_system(
        SystemReason.INBOUND_MAIL,
        replies.record,
        bo,
        channel='email',
        provider_message_id='u1',
        sender='nobody@client.test',
        subject='unsubscribe',
        authentication={'dmarc': 'pass'},
    )
    assert done == {'status': 'excluded', 'kind': 'unsubscribe'}
    assert is_excluded(load_rules(db, pid), {'email': 'nobody@client.test'})
    # ... but its text is filed nowhere: any client of Bo's may be another organization's than the one that wrote.
    assert (
        db.execute("SELECT count(*) AS n FROM outreach_inbound_messages WHERE provider_message_id = 'u1'").fetchone()[
            'n'
        ]
        == 0
    )
    plain = call_system(
        SystemReason.INBOUND_MAIL,
        replies.record,
        bo,
        channel='email',
        provider_message_id='u2',
        sender='other@client.test',
        text='Ciao',
    )
    assert plain['status'] == 'ignored'  # anything else to the old address has nowhere to go
    # An opt-out from another address that quotes the message Bo's campaign sent: filed in that campaign's client.
    quoted = call_system(
        SystemReason.INBOUND_MAIL,
        replies.record,
        bo,
        channel='email',
        provider_message_id='u3',
        sender='alias@client.test',
        subject='unsubscribe',
        references=[msg['Message-ID']],
    )
    assert quoted['status'] == 'accepted' and quoted['kind'] == 'unsubscribe'
    row = db.execute(
        "SELECT project_id::text, campaign_id::text FROM outreach_inbound_messages WHERE provider_message_id = 'u3'"
    ).fetchone()
    assert row == {'project_id': pid, 'campaign_id': cid}


def test_a_released_sender_can_be_connected_again(db, provider):
    """A deleted member's mailbox is adopted by the colleague who reconnects the same account; their LinkedIn id is
    freed, so the account can be connected again (it is unique across organizations, #317)."""
    from app.integrations import project_senders

    ada, bo, org, pid = _team(db)
    box = _mailbox(db, bo, org, pid, 'shared@agency.test')
    li = db.execute(
        'INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,'
        " provider_account_id) VALUES (%s, %s, 'unipile', '', 'acc-9') RETURNING id::text",
        (org, bo),
    ).fetchone()['id']
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))
    assert db.execute('SELECT provider_account_id FROM user_oauth_connections WHERE id = %s', (li,)).fetchone() == {
        'provider_account_id': None
    }
    done = call_as(
        member(db, ada),
        project_senders.attach,
        ada,
        pid,
        'google',
        account='shared@agency.test',
        cols={'refresh_token_encrypted': token_crypto.encrypt('r2').value, 'account_label': 'shared@agency.test'},
    )
    assert done.is_ok(), done
    row = db.execute('SELECT user_id::text, status FROM user_oauth_connections WHERE id = %s', (box,)).fetchone()
    assert row == {'user_id': ada, 'status': 'connected'}  # adopted: Ada manages it now
    eve = _user(db)
    linked = call_as(
        member(db, eve),
        project_senders.attach,
        eve,
        _org_project(db, eve),
        'unipile',
        account='acc-9',
        cols={'refresh_token_encrypted': 'x', 'provider_account_id': 'acc-9'},
    )
    assert linked.is_ok(), linked  # not ALREADY_CONNECTED_ELSEWHERE


def _org_project(db, uid) -> str:
    from tests.conftest import project

    return project(db, uid)


def test_one_email_to_two_clients_is_two_messages(db):
    """The inbox is keyed by owner, or by client once the owner is gone: the same provider message at two
    organizations' client addresses is stored once in each."""
    _, bo, org, pid = _team(db)
    _, zed, org2, pid2 = _team(db)
    for owner, o, p in ((bo, org, pid), (zed, org2, pid2)):
        _campaign(db, owner, o, p)
    db.execute('DELETE FROM "user" WHERE id = ANY(%s)', ([bo, zed],))
    for p in (pid, pid2):
        assert (
            call_system(
                SystemReason.INBOUND_MAIL,
                replies.record,
                p,
                channel='email',
                provider_message_id='same',
                sender='anna@client.test',
            )['status']
            == 'accepted'
        )
    assert sorted(
        r['p'] for r in db.execute('SELECT project_id::text AS p FROM outreach_inbound_messages').fetchall()
    ) == sorted([pid, pid2])
    assert (
        call_system(
            SystemReason.INBOUND_MAIL,
            replies.record,
            pid,
            channel='email',
            provider_message_id='same',
            sender='anna@client.test',
        )['status']
        == 'duplicate'
    )


async def test_old_links_one_click_page_and_erasure_work_after_the_creator_is_gone(db, provider, monkeypatch):
    """RFC 8058 one-click, the page's state and the erasure request all take a token naming the deleted creator."""
    from app.notifications import service as notifications

    ada, bo, org, pid = _team(db)
    _mailbox(db, ada, org, pid, 'ada@agency.test')
    _campaign(db, bo, org, pid)
    each_org(db, dispatcher.plan, NOW)
    await each_org_async(db, dispatcher.send_due, NOW)
    ((msg, _),) = provider.sent
    token = msg['List-Unsubscribe'].split(', ')[0].removeprefix('<https://api.zenbree.test/v1/unsubscribe/')[:-1]
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))
    tc = TestClient(app)
    assert tc.get(f'/v1/unsubscribe/{token}/state').json()['status'] == 'subscribed'
    assert tc.post(f'/v1/unsubscribe/{token}').json() == {'status': 'unsubscribed'}
    assert tc.get(f'/v1/unsubscribe/{token}/state').json()['status'] == 'unsubscribed'
    sent: list[str] = []
    monkeypatch.setattr(notifications, 'send_email', lambda to, *a, **k: sent.append(to) or True)
    monkeypatch.setattr(replies.settings, 'resend_api_key', 're_x')
    monkeypatch.setattr(replies.settings, 'email_from', 'Z <z@zenbree.test>')
    assert call_system(SystemReason.OPT_OUT, replies.request_erasure, bo, msg['To']) == 'sent'
    ada_email = db.execute('SELECT email FROM "user" WHERE id = %s', (ada,)).fetchone()['email']
    assert sent == [ada_email]  # the oldest admin of the client's organization


async def test_release_records_the_former_owner_before_the_delete(db, provider):
    _, bo, org, pid = _team(db)
    cid = _campaign(db, bo, org, pid)
    db.execute('UPDATE "user" SET role = %s WHERE id = %s', ('admin', bo))  # require_admin reads it too
    sign_in(app, CurrentUser(id=bo, org_id=org, role='admin'))
    try:
        assert TestClient(app).post(f'/v1/admin/users/{bo}/before-delete').status_code == 200
    finally:
        app.dependency_overrides.clear()
    row = db.execute('SELECT owner_id::text, former_owner_id::text FROM outreach_campaigns WHERE id = %s', (cid,))
    assert row.fetchone() == {'owner_id': bo, 'former_owner_id': bo}


def test_orphan_opt_outs_are_capped_per_client_though_no_message_is_stored(db, monkeypatch):
    """The daily cap on reply-driven exclusions counts the exclusions themselves: an unmatched opt-out to a deleted
    member's old address stores no message, yet a forged flood must still stop at the cap."""
    from app.exclusions.rules import is_excluded, load_rules

    monkeypatch.setattr(replies, 'MAX_OPT_OUTS_PER_DAY', 3)
    _, bo, org, pid = _team(db)
    _campaign(db, bo, org, pid)
    db.execute("INSERT INTO exclusions (project_id, kind, value) VALUES (%s, 'domain', 'manual.test')", (pid,))
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))
    for i in range(4):
        done = call_system(
            SystemReason.INBOUND_MAIL,
            replies.record,
            bo,
            channel='email',
            provider_message_id=f'f{i}',
            sender=f'p{i}@x.test',
            subject='unsubscribe',
            authentication={'dmarc': 'pass'},
        )
        assert done == {'status': 'excluded', 'kind': 'unsubscribe'}
    rules = load_rules(db, pid)
    assert [is_excluded(rules, {'email': f'p{i}@x.test'}) for i in range(4)] == [True, True, True, False]
    assert db.execute('SELECT count(*) AS n FROM outreach_inbound_messages').fetchone()['n'] == 0
