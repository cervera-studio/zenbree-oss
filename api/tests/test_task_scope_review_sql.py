"""Review round 1 of the task-scope PR: a forged header closes nobody's run but its own member's, and the beats
select only owners who are live members."""

import asyncio
from datetime import UTC, datetime

import pytest
from psycopg.types.json import Jsonb

from app import worker
from app.core import scope as scopes
from app.lists.ai_columns import fill_column, queue_fill
from app.scheduling import runner as search_runner
from app.scheduling import service as searches
from app.watchlists import runner as watchlist_runner
from app.watchlists import service as watchlist_service
from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_scope_sql import _search, _user
from tests.test_task_scope_sql import broker as broker
from tests.test_task_scope_sql import world as world

pytestmark = needs_db


def _add_member(db, org: str, name: str) -> str:
    user = str(_user(db, name))
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, user),
    )
    return user


def _remove(db, org: str, user: str) -> None:
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (user, org))


def _watchlist(db, user: str, list_id: str) -> str:
    return db.execute(
        'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, next_run_at)'
        " VALUES (%s, %s, 'Hiring', ARRAY['SDR'], 'weekly', now()) RETURNING id",
        (user, list_id),
    ).fetchone()['id']


def test_a_forged_header_does_not_close_a_teammates_search_run(db, world):
    """V is removed from A; a message for U's run carries V's scope. The claim ends with ``ActorGone`` and U's
    run stays queued."""
    a, u = world['a'], world['u']
    v = _add_member(db, a, 'v')
    search = db.execute('SELECT * FROM saved_searches WHERE id = %s', (world['search_a'],)).fetchone()
    run = searches.enqueue(db, search, datetime.now(UTC))
    _remove(db, a, v)
    with scopes.acting_as(a, v), pytest.raises(scopes.ActorGone):
        search_runner.claim(run['id'])
    status = db.execute('SELECT status, error_code FROM saved_search_runs WHERE id = %s', (run['id'],)).fetchone()
    assert (status['status'], status['error_code']) == ('queued', None)
    _remove(db, a, u)  # the owner's own run is still closed when the owner is the one who left
    with scopes.acting_as(a, u), pytest.raises(scopes.ActorGone):
        search_runner.claim(run['id'])
    status = db.execute('SELECT status, error_code FROM saved_search_runs WHERE id = %s', (run['id'],)).fetchone()
    assert (status['status'], status['error_code']) == ('failed', 'creator_left')


def test_a_forged_header_does_not_close_a_teammates_watchlist_run(db, world):
    a, u = world['a'], world['u']
    v = _add_member(db, a, 'v')
    wl = _watchlist(db, u, world['lists'][a])
    run = watchlist_service.enqueue(db, wl, datetime.now(UTC))
    _remove(db, a, v)
    with scopes.acting_as(a, v), pytest.raises(scopes.ActorGone):
        watchlist_runner.claim(run['id'])
    status = db.execute('SELECT status, error_code FROM watchlist_runs WHERE id = %s', (run['id'],)).fetchone()
    assert (status['status'], status['error_code']) == ('queued', None)
    _remove(db, a, u)
    with scopes.acting_as(a, u), pytest.raises(scopes.ActorGone):
        watchlist_runner.claim(run['id'])
    status = db.execute('SELECT status, error_code FROM watchlist_runs WHERE id = %s', (run['id'],)).fetchone()
    assert (status['status'], status['error_code']) == ('failed', 'creator_left')


def test_a_forged_header_does_not_fail_a_teammates_fill(db):
    from tests.conftest import member
    from tests.test_ai_columns import ACME_PAGE, ANSWERS, SEARCH, FakeProvider, _list_with_rows

    ada = str(_user(db, 'ada'))
    team = member(db, ada).org_id
    bo = _add_member(db, str(team), 'bo')
    lid = _list_with_rows(db, ada, ['Acme'])
    SEARCH['Acme'], ANSWERS['Acme'] = [ACME_PAGE], {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    assert queue_fill(db, lid, 'ai_h', by=ada)
    _remove(db, str(team), bo)

    def status() -> str:
        return db.execute(
            "SELECT config->'fill'->>'status' AS s FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)
        ).fetchone()['s']

    with scopes.acting_as(team, bo), pytest.raises(scopes.ActorGone):
        asyncio.run(fill_column(lid, 'ai_h', provider=FakeProvider()))
    assert status() == 'queued'


_DEAD_OWNERS = 101  # one more than the beat's LIMIT: they would fill the whole selection


def _beat_scopes(broker, task: str) -> list[tuple[str, str]]:
    return sorted((h['zb_scope']['org'], h['zb_scope']['user']) for _, h in broker.pop(task, []))


def test_the_search_beat_skips_dead_owners_and_does_not_starve_live_ones(db, world, broker):
    """A removed owner's due rows never advance, so without the live-member filter they sort first every minute and
    hold the whole LIMIT: the live owner's search would never be selected."""
    a, u = world['a'], world['u']
    w = _add_member(db, a, 'w')
    _, live = _search(db, a, w)
    db.execute("UPDATE saved_searches SET next_run_at = now() - interval '1 minute' WHERE id = %s", (live,))
    db.execute(
        'INSERT INTO saved_searches (user_id, name, tool_name, tool_input, list_id, cadence, next_run_at)'
        " SELECT %s, 'dead', 'search_companies', '{}', %s, 'daily', now() - interval '1 hour' - n * interval '1 second'"
        ' FROM generate_series(1, %s) n',
        (u, world['lists'][a], _DEAD_OWNERS),
    )
    _remove(db, a, u)
    db.execute(
        "UPDATE saved_searches SET next_run_at = now() + interval '1 day' WHERE list_id = %s",
        (world['lists'][world['b']],),
    )
    worker.dispatch_saved_searches()
    assert _beat_scopes(broker, 'run_saved_search') == [(a, w)]


def test_the_watchlist_beat_skips_dead_creators_and_does_not_starve_live_ones(db, world, broker):
    a, u = world['a'], world['u']
    w = _add_member(db, a, 'w')
    live_list, _ = _search(db, a, w)
    live = _watchlist(db, w, live_list)
    db.execute("UPDATE watchlists SET next_run_at = now() - interval '1 minute' WHERE id = %s", (live,))
    db.execute(
        'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, next_run_at)'
        " SELECT %s, %s, 'dead', ARRAY['SDR'], 'weekly', now() - interval '1 hour' - n * interval '1 second'"
        ' FROM generate_series(1, %s) n',
        (u, world['lists'][a], _DEAD_OWNERS),
    )
    _remove(db, a, u)
    worker.dispatch_watchlists()
    assert _beat_scopes(broker, 'run_watchlist') == [(a, w)]
