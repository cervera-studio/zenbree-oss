"""The placeholder name of a new client says «client», as the interface does (#307).

Needs TEST_DATABASE_URL; builds a throwaway database from api/db migrations.
"""

import shutil

import pytest

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import empty_database
from tests.test_tools_sql import _user

MIGRATION = '20261016074139_client_placeholder_name.sql'


@pytest.fixture
def before(tmp_path):
    """A schema migrated up to just before this change, and the directory to finish it from."""
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name < MIGRATION:
            shutil.copy(path, tmp_path)
    with empty_database() as c:
        migrate(c, tmp_path)
        yield c, tmp_path


def test_renames_only_the_placeholder_names(before):
    c, directory = before
    user = _user(c)
    org = c.execute('SELECT default_organization(%s) AS id', (user,)).fetchone()['id']
    rows = [
        ('Il mio progetto', True),
        ('My project', True),
        ('Mi proyecto', True),
        ('Mon projet', True),
        ('Il mio progetto', False),  # typed by the user: theirs
        ('Rossi Srl', False),
    ]
    ids = [
        c.execute(
            'INSERT INTO projects (organization_id, user_id, name, name_is_placeholder) VALUES (%s, %s, %s, %s)'
            ' RETURNING id',
            (org, user, name, placeholder),
        ).fetchone()['id']
        for name, placeholder in rows
    ]

    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]

    names = {r['id']: r['name'] for r in c.execute('SELECT id, name FROM projects')}
    assert [names[i] for i in ids] == [
        'Il mio cliente',
        'My client',
        'Mi cliente',
        'Mon client',
        'Il mio progetto',
        'Rossi Srl',
    ]
