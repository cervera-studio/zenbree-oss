import asyncio
import json
from contextlib import aclosing
from datetime import timedelta

import psycopg
import pytest
from structlog.testing import capture_logs

from app.core.event_stream import NOT_A_MEMBER
from app.core.scope import acting_as
from app.home import live, overview, routes
from app.home.overview import load_overview
from tests.conftest import member, project
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db
from tests.test_home_overview import _campaign, _job, _reply

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: home stream SQL tests NOT run')


class Stream:
    def __init__(self, resp):
        self._frames: asyncio.Queue[str | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._pump(resp))

    async def _pump(self, resp):
        async with aclosing(resp.body_iterator) as it:
            async for chunk in it:
                await self._frames.put(str(chunk))
        await self._frames.put(None)

    # The first frame of a stream arrives in under 0.1 s even with 4 workers on the SQL suite (measured over 39 streams);
    # the 5 s it had missed once on a stalled runner (#879). A wait that is long only costs time when a test fails.
    async def next(self, seconds: float = 30) -> tuple[str, dict] | None:
        async with asyncio.timeout(seconds):
            while (frame := await self._frames.get()) is not None:
                if not frame.startswith(':'):
                    head, data = frame.strip().split('\n')
                    return head.removeprefix('event: '), json.loads(data.removeprefix('data: '))
        return None

    async def quiet(self, seconds: float) -> list[tuple[str, dict]]:
        out = []
        try:
            async with asyncio.timeout(seconds):
                while (frame := await self.next(seconds)) is not None:
                    out.append(frame)
        except TimeoutError:
            pass
        return out

    async def close(self):
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


async def listener_attached(db, replacing: frozenset[int] = frozenset()) -> frozenset[int]:
    """The hub's LISTEN connects in a task of its own, so the time a write needs to reach a stream includes that
    connect, which a loaded Postgres can stretch past any budget. A test that writes waits for the listener first."""
    async with asyncio.timeout(30):
        while True:
            pids = frozenset(
                r['pid']
                for r in db.execute(
                    'SELECT pid FROM pg_stat_activity WHERE application_name = %s AND datname = current_database()'
                    " AND state = 'idle' AND query LIKE 'LISTEN%%'",
                    (live.APPLICATION_NAME,),
                )
            )
            if pids - replacing:
                return pids
            await asyncio.sleep(0.05)


# A share of the throttle's production waits, not new values: a production wait that shrinks shrinks here too.
SCALE = 0.25


@pytest.fixture
def short_waits(monkeypatch):
    monkeypatch.setattr(live, 'QUIET_SECONDS', live.QUIET_SECONDS * SCALE)
    monkeypatch.setattr(live, 'MAX_WAIT_SECONDS', live.MAX_WAIT_SECONDS * SCALE)
    # The listener reads it when it connects, so it holds only for a listener that a later open_stream starts.
    monkeypatch.setattr(live, 'RECONNECT_MIN_SECONDS', 0.01)


@pytest.fixture
async def open_stream(db):
    opened: list[Stream] = []

    async def start(uid: str) -> Stream:
        user = member(db, uid)
        with acting_as(user.org_id, uid):
            s = Stream(await routes.stream(user=user, project_id=project(db, uid)))
        opened.append(s)
        await listener_attached(db)
        return s

    yield start
    for s in opened:
        await s.close()


async def test_the_first_frame_is_the_overview_snapshot(db, open_stream):
    uid = _user(db)
    _campaign(db, uid, 'Live', waiting=2)
    s = await open_stream(uid)
    user = member(db, uid)
    expected = json.loads(load_overview(db, uid, user.org_id, project(db, uid)).overview.model_dump_json())
    assert await s.next() == ('snapshot', expected)


async def test_a_committed_write_sends_the_new_overview(db, open_stream):
    uid = _user(db)
    s = await open_stream(uid)
    assert (await s.next())[1]['approvals']['total'] == 0
    _campaign(db, uid, 'Live', waiting=3)
    event, body = await s.next()
    assert event == 'overview'
    assert body['approvals']['total'] == 3


async def test_another_users_reply_changes_no_frame(db, open_stream):
    uid = _user(db)
    mate = _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt")'
        " VALUES (default_organization(%s), %s, 'member', now())",
        (uid, mate),
    )
    s = await open_stream(uid)
    await s.next()
    _reply(db, mate, 'Theirs')
    assert await s.quiet(2) == []
    _reply(db, uid, 'Mine')
    event, body = await s.next()
    assert (event, body['replies']['count'], body['replies']['latest']['name']) == ('overview', 1, 'Mine')


async def test_a_burst_of_job_updates_is_at_most_two_frames(db, open_stream, short_waits):
    uid = _user(db)
    pid = project(db, uid)
    _job(db, uid, pid, [{'tool': 'search_people', 'units': 20, 'done': 0}], total=20)
    s = await open_stream(uid)
    await s.next()
    started = asyncio.get_running_loop().time()
    for i in range(1, 21):
        db.execute('UPDATE jobs SET progress_done = %s WHERE user_id = %s', (i, uid))
        await asyncio.sleep(0.05 * SCALE)
    burst = asyncio.get_running_loop().time() - started
    frames = [await s.next()]
    while frames[-1][1]['running'][0]['progress_done'] < 20:
        frames.append(await s.next())
    # A busy machine stretches the burst past the max wait, which rightly forces one frame per wait. A literal,
    # not live.MAX_WAIT_SECONDS: a regression that shrinks the wait must still fail here.
    assert len(frames) <= 2 + int(burst / (1.5 * SCALE))


async def test_a_removed_members_stream_ends_at_the_next_snapshot(db, open_stream):
    """The removal's own NOTIFY wakes the stream, and the snapshot's checkout refuses the member: EventStream turns
    that into the `error` frame. The idle and banned cases, which only the timed re-check sees, are in
    tests/test_stream_member_sql.py."""
    uid = _user(db)
    s = await open_stream(uid)
    await s.next()
    assert await s.quiet(1) == []
    db.execute('DELETE FROM member WHERE "userId" = %s', (uid,))
    assert await s.next() == ('error', NOT_A_MEMBER)
    assert await s.next() is None


async def test_a_stale_run_drops_out_with_no_write(db, open_stream, monkeypatch):
    uid = _user(db)
    pid = project(db, uid)
    _job(db, uid, pid, [])
    snapshot = live._snapshot

    # The stale limit is set from the first snapshot's own now(), so a slow stream open cannot age the run past it.
    def stale_in_1_5_s(load, user, project_id):
        def aged(conn, *args):
            age = conn.execute('SELECT now() - updated_at AS age FROM jobs WHERE user_id = %s', (uid,)).fetchone()[
                'age'
            ]
            monkeypatch.setattr(overview, 'STALE_JOB_MINUTES', (age + timedelta(seconds=1.5)) / timedelta(minutes=1))
            monkeypatch.setattr(live, '_snapshot', snapshot)
            return load(conn, *args)

        return snapshot(aged, user, project_id)

    monkeypatch.setattr(live, '_snapshot', stale_in_1_5_s)
    # The listener wakes every stream once it connects. A slow connect would then recompute after the run went stale
    # and pass with no refresh timer. The test writes nothing, so that wake has nothing to catch up on.
    monkeypatch.setattr(live.HUB, '_wake_all', lambda: None)
    s = await open_stream(uid)
    assert len((await s.next())[1]['running']) == 1
    event, body = await s.next()
    assert (event, body['running']) == ('overview', [])


async def test_the_stream_catches_up_after_the_listen_connection_drops(db, open_stream, short_waits):
    uid = _user(db)
    s = await open_stream(uid)
    await s.next()
    killed = db.execute(
        'SELECT pg_terminate_backend(pid), pid FROM pg_stat_activity'
        ' WHERE application_name = %s AND datname = current_database()',
        (live.APPLICATION_NAME,),
    ).fetchall()
    assert killed
    await listener_attached(db, frozenset(r['pid'] for r in killed))
    _campaign(db, uid, 'Live', waiting=1)
    event, body = await s.next()
    assert (event, body['approvals']['total']) == ('overview', 1)


async def test_the_listener_logs_an_unexpected_error_and_keeps_waking_streams(
    db, open_stream, short_waits, monkeypatch
):
    uid = _user(db)
    s = await open_stream(uid)
    await s.next()
    connect = psycopg.AsyncConnection.connect
    failed = []

    async def broken_once(*args, **kwargs):
        if not failed:
            failed.append(True)
            raise RuntimeError('not a psycopg error')
        return await connect(*args, **kwargs)

    monkeypatch.setattr(psycopg.AsyncConnection, 'connect', broken_once)
    with capture_logs() as logs:
        killed = db.execute(
            'SELECT pg_terminate_backend(pid), pid FROM pg_stat_activity'
            ' WHERE application_name = %s AND datname = current_database()',
            (live.APPLICATION_NAME,),
        ).fetchall()
        await listener_attached(db, frozenset(r['pid'] for r in killed))
        _campaign(db, uid, 'Live', waiting=1)
        event, body = await s.next()
    assert failed
    assert (event, body['approvals']['total']) == ('overview', 1)
    assert any(e['event'] == 'home.listen_crashed' and e['log_level'] == 'error' for e in logs)


async def test_a_listener_that_ended_is_replaced_on_the_next_subscribe(db, open_stream):
    uid = _user(db)
    first = await open_stream(uid)
    await first.next()
    ended = live.HUB._task
    assert ended is not None
    old = await listener_attached(db)
    ended.cancel()
    await asyncio.gather(ended, return_exceptions=True)
    second = await open_stream(uid)
    await listener_attached(db, old)
    await second.next()
    _campaign(db, uid, 'Live', waiting=1)
    for s in (first, second):
        event, body = await s.next()
        assert (event, body['approvals']['total']) == ('overview', 1)


async def test_the_listen_connection_closes_with_the_last_stream(db, open_stream):
    def listening() -> int:
        sql = 'SELECT count(*) AS n FROM pg_stat_activity WHERE application_name = %s AND datname = current_database()'
        return db.execute(sql, (live.APPLICATION_NAME,)).fetchone()['n']

    uid = _user(db)
    first, second = await open_stream(uid), await open_stream(uid)
    await first.next()
    await second.next()
    assert await first.quiet(1) == []
    assert listening() == 1
    await first.close()
    assert listening() == 1
    await second.close()
    for _ in range(30):
        if not listening():
            break
        await asyncio.sleep(0.1)
    assert listening() == 0


async def test_the_stream_ends_when_postgres_goes_down(db, open_stream, monkeypatch):
    uid = _user(db)
    s = await open_stream(uid)
    await s.next()

    def down(*_):
        raise psycopg.OperationalError('connection refused')

    monkeypatch.setattr(live, '_snapshot', down)
    _campaign(db, uid, 'Live', waiting=1)
    assert await s.next() is None
