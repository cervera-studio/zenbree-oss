"""CSV import as a worker job (#344): duplicates against the organization's lists, the discarded rows, progress."""

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.agent import events
from app.core.system_reasons import SystemReason
from app.lists.csv_run import stream_key
from app.main import app
from tests.conftest import call_system, member, project, sign_in, sign_out
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_csv_import import _imported

client = TestClient(app)
__all__ = ['db']

FIRST = b'email,title\na@x.test,CEO\nb@x.test,CTO\n'
SECOND = b'email,title\nb@x.test,CTO again\nc@x.test,COO\nc@x.test,COO twice\n,Nobody\n'


def _upload(content: bytes, name: str) -> dict:
    return _imported(client.post('/v1/lists/upload', params={'entity': 'person', 'name': name}, content=content))


@needs_db
def test_rows_in_the_organizations_lists_are_discarded_and_downloadable(db, inline_worker):
    uid, other = _user(db), _user(db)
    db.execute('UPDATE "user" SET locale = %s WHERE id = %s', ('it', uid))  # the file is in the user's language
    sign_in(app, member(db, uid))
    try:
        _upload(FIRST, 'first')
        preview = client.post('/v1/lists/upload/preview?entity=person', content=SECOND).json()
        assert (preview['to_import'], preview['in_lists'], preview['duplicates'], preview['skipped']) == (1, 1, 1, 1)

        done = _upload(SECOND, 'second')
        assert (done['rows_added'], done['in_lists'], done['duplicates'], done['skipped'], done['discarded']) == (
            1,
            1,
            1,
            1,
            3,
        )
        assert done['processed'] == done['total'] == 1
        rows = list(csv.reader(io.StringIO(client.get(f'/v1/lists/imports/{done["id"]}/discarded.csv').text)))
        assert rows[0] == ['email', 'title', 'Motivo']
        assert {tuple(r) for r in rows[1:]} == {
            ('b@x.test', 'CTO again', 'Già nelle tue liste'),
            ('c@x.test', 'COO twice', 'Duplicata nel file'),
            ('', 'Nobody', 'Senza nome, dominio o LinkedIn'),
        }

        # another organization neither sees the import nor shares its lists
        sign_in(app, member(db, other))
        assert client.get(f'/v1/lists/imports/{done["id"]}').status_code == 404
        assert client.get(f'/v1/lists/imports/{done["id"]}/discarded.csv').status_code == 404
        assert client.get(f'/v1/lists/imports/{done["id"]}/stream').status_code == 404
        assert _upload(FIRST, 'theirs')['rows_added'] == 2
    finally:
        sign_out(app)


@needs_db
def test_progress_frames_end_with_done_and_the_import_comes_back_until_seen(db, inline_worker):
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        done = _upload(FIRST, 'first')
        sent = [
            (e, d)
            for _, f in events.redis_sync().xrange(stream_key(done['id']))
            for e, d in [(f[b'event'], f[b'data'])]
        ]
        assert [e for e, _ in sent] == [b'progress', b'progress', b'done']
        assert client.get('/v1/lists/imports/active').json()['id'] == done['id']
        assert client.post(f'/v1/lists/imports/{done["id"]}/seen').status_code == 204
        assert client.get('/v1/lists/imports/active').json() is None
    finally:
        sign_out(app)


@needs_db
def test_a_failing_import_ends_failed_and_drops_the_file(db, monkeypatch):
    from app.lists import csv_run
    from app.worker import run_csv_import

    monkeypatch.setattr(run_csv_import, 'delay', csv_run.run)
    monkeypatch.setattr(csv_run, 'add_rows', lambda *a: 1 / 0)
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        res = client.post('/v1/lists/upload', params={'entity': 'person'}, content=FIRST)
        state = client.get(f'/v1/lists/imports/{res.json()["id"]}').json()
        assert (state['status'], state['error']) == ('failed', 'LIST.IMPORT_FAILED')
        row = db.execute('SELECT content FROM csv_imports WHERE id = %s', (state['id'],)).fetchone()
        assert row['content'] is None
        assert pytest.approx(0) == state['rows_added']
    finally:
        sign_out(app)


@needs_db
def test_only_the_projects_own_lists_count_as_already_there(db, inline_worker):
    uid = _user(db)
    project(db, uid)  # the default project exists before client B
    other = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'client B')"
        ' RETURNING id',
        (uid, uid),
    ).fetchone()['id']
    sign_in(app, member(db, uid))
    try:
        _upload(FIRST, 'in the default project')
        into_b = client.post('/v1/lists/upload', params={'entity': 'person', 'project_id': str(other)}, content=FIRST)
        assert _imported(into_b)['in_lists'] == 0  # the other client's list does not count
        again = client.post('/v1/lists/upload', params={'entity': 'person', 'project_id': str(other)}, content=FIRST)
        assert _imported(again)['in_lists'] == 2
        # the dialog of project B does not resume the default project's import
        assert (
            client.get('/v1/lists/imports/active', headers={'X-Project-Id': str(other)}).json()['id']
            == again.json()['id']
        )
        assert client.get('/v1/lists/imports/active').json()['id'] != again.json()['id']
    finally:
        sign_out(app)


@needs_db
def test_the_janitor_fails_a_stuck_import_and_drops_what_it_holds(db):
    from app.lists.csv_run import reap

    uid = _user(db)
    org, pid = member(db, uid).org_id, project(db, uid)

    def make(status, age, **cols):
        return db.execute(
            'INSERT INTO csv_imports (organization_id, user_id, project_id, entity, name, status, content, discarded,'
            " updated_at) VALUES (%s, %s, %s, 'person', 'x', %s, 'a@b.test', 'row', now() - make_interval(mins => %s))"
            ' RETURNING id',
            (org, uid, pid, status, age),
        ).fetchone()['id']

    def row(i):
        return db.execute('SELECT status, content, discarded FROM csv_imports WHERE id = %s', (i,)).fetchone()

    stuck, live, old_done, new_done = (
        make('running', 20),
        make('running', 2),
        make('done', 8 * 24 * 60),
        make('done', 60),
    )
    waiting, lost = make('queued', 20), make('queued', 130)  # a queued run waits behind other jobs
    assert call_system(SystemReason.CSV_REAPER, reap) == 2
    assert row(waiting)['status'] == 'queued' and row(waiting)['content'] is not None
    assert row(lost)['status'] == 'failed'
    assert (row(stuck)['status'], row(stuck)['content']) == ('failed', None)
    assert row(live)['status'] == 'running' and row(live)['content'] is not None
    assert (row(old_done)['content'], row(old_done)['discarded']) == (None, None)
    assert row(new_done)['discarded'] == 'row'
    last = events.redis_sync().xrevrange(stream_key(str(stuck)), count=1)
    assert last[0][1][b'event'] == b'failed'


@needs_db
def test_a_file_the_worker_cannot_use_fails_with_its_cause(db, monkeypatch):
    from app.lists import csv_import, csv_run
    from app.worker import run_csv_import

    def unusable(*a):
        raise csv_import.CsvError('LIST.CSV_EMPTY', 'the file is empty')

    monkeypatch.setattr(run_csv_import, 'delay', csv_run.run)
    monkeypatch.setattr(csv_run, 'read_csv', unusable)
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        res = client.post('/v1/lists/upload', params={'entity': 'person'}, content=FIRST)
        state = client.get(f'/v1/lists/imports/{res.json()["id"]}').json()
        assert (state['status'], state['error']) == ('failed', 'LIST.CSV_EMPTY')
    finally:
        sign_out(app)
