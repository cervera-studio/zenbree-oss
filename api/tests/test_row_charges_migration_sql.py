"""#660: the migration that moves per-row charges out of ``cell_meta`` into ``row_charges``."""

from psycopg.types.json import Jsonb

from app.core.migrate import MIGRATIONS
from tests.test_agent import _job, _user, needs_db
from tests.test_agent import db as db
from tests.test_lookups_sql import _list

pytestmark = needs_db

MIGRATION = '20261012173606_row_charges.sql'
FILLED = {'status': 'filled'}


def _old_rows(db, list_id: str, live: str, done: str) -> dict[str, str]:
    """Every shape a charge left in ``cell_meta`` before #660, by row name. ``live``: a job that runs now; ``done``:
    one that ended."""
    rows = {
        'held by a live job': {
            'email': {**FILLED, 'pending_job': live},
            'email_status': {**FILLED, 'pending_job': live},
        },
        'held by a finished job': {'phone': {**FILLED, 'pending_job': done}, 'title': {'status': 'manual'}},
        'held by a deleted job': {'email': {**FILLED, 'pending_job': '00000000-0000-4000-8000-000000000000'}},
        'refunded': {'email': {'status': 'refunded'}, 'email_status': {'status': 'refunded'}},
        'only refunded entries': {'phone': {'status': 'refunded'}},
        'both kinds': {'email': {'status': 'refunded'}, 'phone': {**FILLED, 'pending_job': done}},
        'untouched': {'full_name': {'status': 'manual'}, 'ai_fit': {'status': 'filled', 'model': 'x'}},
    }
    return {
        name: str(
            db.execute(
                'INSERT INTO list_rows (list_id, cells, cell_meta, dedupe_key) VALUES (%s, %s, %s, %s) RETURNING id',
                (list_id, Jsonb({}), Jsonb(meta), name),
            ).fetchone()['id']
        )
        for name, meta in rows.items()
    }


def test_the_migration_moves_every_charge_mark_into_one_record(db):
    uid = _user(db)
    _, done = _job(db, uid, 'find phones')
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (done,))
    _, live = _job(db, uid, 'find emails')
    db.execute("UPDATE jobs SET status = 'running', updated_at = now() WHERE id = %s", (live,))
    ids = _old_rows(db, _list(db), live, done)
    db.execute('DROP TABLE row_charges')  # the fixture ran the migration on an empty schema: run it on this data
    db.execute((MIGRATIONS / MIGRATION).read_text())

    names = {v: k for k, v in ids.items()}
    records = sorted(
        (
            (names[r['row_id']], r['job_id'], r['tool_call_id'], r['keys'], r['credits'], r['state'], r['settled'])
            for r in db.execute(
                'SELECT row_id::text, job_id::text, tool_call_id, keys, credits, state, settled_at IS NOT NULL AS settled'
                ' FROM row_charges'
            )
        ),
        key=lambda record: (record[0], record[5]),
    )
    assert records == [
        ('both kinds', done, None, ['phone'], 0, 'charged', True),
        ('both kinds', None, None, ['email'], 0, 'refunded', True),
        ('held by a deleted job', None, None, ['email'], 0, 'charged', True),
        ('held by a finished job', done, None, ['phone'], 0, 'charged', True),
        ('held by a live job', live, None, ['email', 'email_status'], 0, 'held', False),
        ('only refunded entries', None, None, ['phone'], 0, 'refunded', True),
        ('refunded', None, None, ['email', 'email_status'], 0, 'refunded', True),
    ]
    meta = {names[r['id']]: r['cell_meta'] for r in db.execute('SELECT id::text, cell_meta FROM list_rows')}
    assert meta == {
        'held by a live job': {'email': FILLED, 'email_status': FILLED},
        'held by a finished job': {'phone': FILLED, 'title': {'status': 'manual'}},
        'held by a deleted job': {'email': FILLED},
        'refunded': {},
        'only refunded entries': {},
        'both kinds': {'phone': FILLED},
        'untouched': {'full_name': {'status': 'manual'}, 'ai_fit': {'status': 'filled', 'model': 'x'}},
    }
    # the web rejects the whole list page for one entry without a status (INTERNAL.RESPONSE_CONTRACT)
    assert all('status' in entry for row in meta.values() for entry in row.values())
