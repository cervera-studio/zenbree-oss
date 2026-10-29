import os
import shutil

import pytest

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import empty_database, project
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: clear sent drafts test NOT run')
NAME = '20261018190649_clear_sent_drafts.sql'


@pytest.fixture
def before(tmp_path):
    for f in MIGRATIONS.glob('*.sql'):
        if f.name < NAME:
            shutil.copy(f, tmp_path / f.name)
    with empty_database() as c:
        assert migrate(c, tmp_path)
        shutil.copy(MIGRATIONS / NAME, tmp_path / NAME)
        yield c, tmp_path


def _message(conn, pid: str, name: str, answers: tuple[str, ...]) -> None:
    mid = conn.execute(
        """INSERT INTO outreach_inbound_messages (project_id, channel, kind, provider_message_id, draft_body)
           VALUES (%s, 'email', 'reply', %s, %s) RETURNING id""",
        (pid, name, f'Bozza {name}'),
    ).fetchone()['id']
    for status in answers:
        conn.execute(
            """INSERT INTO outreach_inbox_replies (inbound_id, channel, sender_provider, recipient, body, status,
                   delivered_at) VALUES (%s, 'email', 'google', 'anna@client.test', 'Sì.', %s,
                   CASE WHEN %s = 'sent' THEN now() END)""",
            (mid, status, status),
        )


def test_only_a_message_with_a_sent_answer_loses_its_draft(before):
    conn, directory = before
    pid = project(conn, _user(conn))
    answers = {
        'sent': ('sent',),
        'retried': ('failed', 'sent'),
        'failed': ('failed',),
        'unknown': ('unknown',),
        'sending': ('sending',),
        'unanswered': (),
    }
    for name, statuses in answers.items():
        _message(conn, pid, name, statuses)

    assert migrate(conn, directory) == [NAME]

    rows = conn.execute('SELECT provider_message_id AS name, draft_body FROM outreach_inbound_messages').fetchall()
    assert {r['name']: r['draft_body'] for r in rows} == {
        'sent': None,
        'retried': None,
        'failed': 'Bozza failed',
        'unknown': 'Bozza unknown',
        'sending': 'Bozza sending',
        'unanswered': 'Bozza unanswered',
    }
