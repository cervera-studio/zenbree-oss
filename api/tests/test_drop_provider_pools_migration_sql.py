import json
import os
import shutil

import pytest

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import empty_database, project, sender
from tests.pin_invariant import PIN_INVARIANT_SQL
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: drop provider pools test NOT run')
NAME = '20261019081401_drop_provider_pools.sql'
STEPS = [{'step': 1, 'channel': 'email', 'subject': 'Ciao', 'body': 'Ciao {{firstName}}', 'send_after_days': 0}]
ROW_WITHOUT_DROPPED_COLUMN = {
    'outreach_campaigns': "to_jsonb(x) - 'sender_pool'",
    'outreach_campaign_targets': "to_jsonb(x) - 'sender_provider'",
    'outreach_campaign_senders': 'to_jsonb(x)',
    'outreach_campaign_sends': 'to_jsonb(x)',
    'outreach_inbox_replies': 'to_jsonb(x)',
    'project_senders': 'to_jsonb(x)',
}


@pytest.fixture
def before(tmp_path):
    for f in MIGRATIONS.glob('*.sql'):
        if f.name < NAME:
            shutil.copy(f, tmp_path / f.name)
    with empty_database() as c:
        assert migrate(c, tmp_path)
        shutil.copy(MIGRATIONS / NAME, tmp_path / NAME)
        yield c, tmp_path


def _campaign(c, uid, pid, status: str, providers: list[str], accounts: list[str]) -> str:
    cid = c.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, sender_pool)
           SELECT organization_id, id, %s, 'C', %s, %s, %s FROM projects WHERE id = %s RETURNING id::text""",
        (uid, status, json.dumps(STEPS), providers, pid),
    ).fetchone()['id']
    c.execute(
        """INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)
           SELECT %s, sender_id, project_id, provider, organization_id FROM project_senders
           WHERE project_id = %s AND sender_id = ANY(%s::uuid[])""",
        (cid, pid, accounts),
    )
    return cid


def _target(c, cid, uid, email: str, provider: str | None, pin: str | None) -> str:
    return c.execute(
        """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, sender_provider, sender_id,
               sender_pinned_at)
           VALUES (%s, %s, %s, 'active', %s, %s, CASE WHEN %s::uuid IS NOT NULL THEN now() END) RETURNING id::text""",
        (cid, uid, email, provider, pin, pin),
    ).fetchone()['id']


def _sent(c, cid, uid, tid, email: str, provider: str, account: str) -> None:
    c.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               subject, body, idempotency_key, sender_provider, sender_id, attempts, attempted_at, delivered_at)
           VALUES (%s, %s, %s, 1, 'email', 'sent', %s, 'Ciao', 'Ciao', gen_random_uuid()::text, %s, %s, 1, now(),
                   now())""",
        (cid, uid, tid, email, provider, account),
    )


def _snapshot(c) -> dict[str, tuple[int, str]]:
    return {
        table: tuple(
            c.execute(
                f"SELECT count(*) AS n, md5(coalesce(string_agg(r::text, ',' ORDER BY r::text), '')) AS h"
                f' FROM (SELECT {row} AS r FROM {table} x) rows'
            ).fetchone()[k]
            for k in ('n', 'h')
        )
        for table, row in ROW_WITHOUT_DROPPED_COLUMN.items()
    }


def _columns(c, table: str) -> set[str]:
    rows = c.execute('SELECT column_name FROM information_schema.columns WHERE table_name = %s', (table,))
    return {r['column_name'] for r in rows}


def test_the_drop_keeps_every_pool_pin_send_and_reply(before):
    c, directory = before
    uid = _user(c)
    pid = project(c, uid)
    google = sender(c, uid, 'google', account_label='a@acme.test')
    microsoft = sender(c, uid, 'microsoft', account_label='m@acme.test')
    live = _campaign(c, uid, pid, 'active', ['google', 'microsoft'], [google, microsoft])
    draft = _campaign(c, uid, pid, 'draft', ['google'], [google])
    _campaign(c, uid, pid, 'active', ['microsoft'], [])
    pinned = _target(c, live, uid, 'anna@client.test', 'google', google)
    _sent(c, live, uid, pinned, 'anna@client.test', 'google', google)
    _target(c, live, uid, 'bea@client.test', 'microsoft', None)
    _target(c, draft, uid, 'cleo@client.test', None, None)
    inbound = c.execute(
        """INSERT INTO outreach_inbound_messages (project_id, channel, kind, provider_message_id, target_id)
           VALUES (%s, 'email', 'reply', 'm1', %s) RETURNING id""",
        (pid, pinned),
    ).fetchone()['id']
    c.execute(
        """INSERT INTO outreach_inbox_replies (inbound_id, owner_id, channel, sender_provider, sender_id, recipient,
               body, status, delivered_at) VALUES (%s, %s, 'email', 'google', %s, 'anna@client.test', 'Sì.', 'sent',
               now())""",
        (inbound, uid, google),
    )
    kept = _snapshot(c)
    assert {t: n for t, (n, _) in kept.items()} == {
        'outreach_campaigns': 3,
        'outreach_campaign_targets': 3,
        'outreach_campaign_senders': 3,
        'outreach_campaign_sends': 1,
        'outreach_inbox_replies': 1,
        'project_senders': 2,
    }

    assert migrate(c, directory) == [NAME]

    assert _snapshot(c) == kept
    assert 'sender_pool' not in _columns(c, 'outreach_campaigns')
    assert 'sender_provider' not in _columns(c, 'outreach_campaign_targets')
    assert 'sender_provider' in _columns(c, 'outreach_campaign_sends')
    assert 'sender_provider' in _columns(c, 'outreach_inbox_replies')
    assert c.execute(PIN_INVARIANT_SQL).fetchall() == []
