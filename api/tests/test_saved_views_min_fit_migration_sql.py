"""The saved-view fit floor migration on views saved before it: they keep no fit filter, the rest is untouched.

Needs TEST_DATABASE_URL. Builds the schema from every migration but this one, seeds an old view, then runs it.
"""

import os
import shutil

import psycopg
import pytest

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import empty_database, project
from tests.test_home_overview import _list
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: saved view fit migration test NOT run')
NAME = '20261022095349_saved_views_min_fit.sql'


@pytest.fixture
def before(tmp_path):
    older = tmp_path / 'db'
    older.mkdir()
    for f in MIGRATIONS.glob('*.sql'):
        if f.name != NAME:
            shutil.copy(f, older / f.name)
    with empty_database() as c:
        assert migrate(c, older)
        yield c


def test_an_old_view_keeps_no_fit_filter_and_the_floor_stays_in_range(before):
    uid = _user(before)
    lid = _list(before, uid, 'Agencies')
    vid = before.execute(
        'INSERT INTO saved_views (organization_id, project_id, list_id, name, tab, query)'
        " VALUES (default_organization(%s), %s, %s, 'Hot', 'delivered', 'milan') RETURNING id",
        (uid, project(before, uid), lid),
    ).fetchone()['id']

    assert migrate(before) == [NAME]

    row = before.execute('SELECT name, tab, query, min_fit FROM saved_views WHERE id = %s', (vid,)).fetchone()
    assert row == {'name': 'Hot', 'tab': 'delivered', 'query': 'milan', 'min_fit': None}
    for edge in (0, 100):
        before.execute('UPDATE saved_views SET min_fit = %s WHERE id = %s', (edge, vid))
    with pytest.raises(psycopg.errors.CheckViolation):
        before.execute('UPDATE saved_views SET min_fit = 101 WHERE id = %s', (vid,))
