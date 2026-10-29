"""#323: a list in organization B cannot name organization A's chat as its own.

The agent-tool guards that reach a list through the chat join on the chat's organization, and they were tested with
that bad row: a list that names A's chat but lives in B. The composite key (chat_session_id, organization_id) now
refuses the row, so the guards have no such list to meet. The guards stay as the second line; this test is the first.
"""

import psycopg
import pytest

from tests.conftest import member, project
from tests.test_agent import _job, _user, needs_db
from tests.test_agent import db as db

pytestmark = needs_db


def test_a_list_cannot_name_the_chat_of_another_organization(db):
    a, b = _user(db), _user(db)
    sid, _ = _job(db, a, 'find people')
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match='lists_chat_session_id_org_fkey'):
        db.execute(
            'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity)'
            " VALUES (%s, %s, %s, %s, 'B list', 'person')",
            (member(db, b).org_id, project(db, b), b, sid),
        )
