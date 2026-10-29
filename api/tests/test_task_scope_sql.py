"""Each task runs as the scope its work belongs to (PR 8 of the organization-scoping run), end to end: the beat or
the API queues the message, a Celery request built from it runs the task as the worker does, and the database
decides what the scope may read and write.

A removed member's person work stops: their saved searches and watchlists are not queued, a run queued before the
removal ends at the worker's claim, and the owner's other organization still runs its own. Organization work goes on:
a webhook delivery a member queued reaches the receiver after the member left (#333).

The seed: U is a member of A and of B, with a list and a saved search in each.
"""

from datetime import UTC, datetime

import pytest
from psycopg.types.json import Jsonb

from app import worker
from app.agent.tools.registry import ToolDef, ToolResult
from app.core import scope as scopes
from app.people.apollo_models import JobPostingsResult
from app.scheduling import runner as search_runner
from app.scheduling import service as searches
from app.watchlists import runner as watchlist_runner
from app.watchlists import service as watchlist_service
from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_scope_entry_points import _delivered, _Producer
from tests.test_scope_sql import _org, _search, _user
from tests.test_webhooks import _endpoint, _payload
from tests.test_webhooks import fernet_key as fernet_key
from tests.test_webhooks import receiver as receiver

pytestmark = needs_db


@pytest.fixture
def world(db):
    u = str(_user(db, 'u'))
    a, b = (str(_org(db, name, u)) for name in ('org-a', 'org-b'))
    (list_a, search_a), (list_b, _) = _search(db, a, u), _search(db, b, u)
    for org in (a, b):
        db.execute(
            'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (%s, %s, 10)', (org, u)
        )
    return {'u': u, 'a': a, 'b': b, 'lists': {a: list_a, b: list_b}, 'search_a': str(search_a)}


def _leave(db, world: dict) -> None:
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (world['u'], world['a']))


@pytest.fixture
def broker(monkeypatch) -> dict[str, list]:
    """The messages the app queues, by task name, as the broker would hold them: headers included."""
    sent: dict[str, list] = {}
    send = worker.celery_app.send_task

    def send_task(name, args=None, kwargs=None, **options):
        producer = _Producer()
        options['producer'] = producer
        result = send(name, args, kwargs, **options)
        sent.setdefault(name, []).extend(producer.sent)
        return result

    monkeypatch.setattr(worker.celery_app, 'send_task', send_task)
    return sent


def _scopes(messages: list) -> list:
    return sorted((headers.get('zb_scope') or {}).items() for _, headers in messages)


@pytest.fixture
def search_tool(monkeypatch) -> list:
    """The scope each saved-search vendor call ran in."""
    seen: list = []

    async def handler(args, ctx) -> ToolResult:
        seen.append(scopes.current_or_none())
        return ToolResult(ok=True, rows=[{'name': 'Acme', 'domain': 'acme.test'}], entity='company')

    tool = ToolDef('search_companies', '', {}, handler)
    monkeypatch.setattr(search_runner.registry, 'get', lambda _: tool)
    return seen


def test_a_removed_owners_searches_stop_and_their_other_organization_runs_its_own(db, world, broker, search_tool):
    """U leaves A with a manual run of A's search queued. The beat queues each run as its owner: A's queued run as
    Member(A, U), which the worker's claim refuses (``creator_left``, nothing held, no vendor call), and B's due search
    as Member(B, U), which runs in B. A's due search is not queued again."""
    a, b, u = world['a'], world['b'], world['u']
    with scopes.acting_as(a, u):  # the API's scope for U's POST /run in A
        searches.run_now(u, world['search_a'])
    _leave(db, world)

    worker.dispatch_saved_searches()
    sent = broker.pop('run_saved_search', [])
    for message in sent:
        _delivered(worker.run_saved_search, message)

    assert _scopes(sent) == sorted({'org': org, 'user': u}.items() for org in (a, b))
    assert search_tool == [scopes.Member(scopes.OrgId(b), scopes.UserId(u))]
    runs = db.execute(
        'SELECT organization_id::text AS org, status, error_code FROM saved_search_runs ORDER BY created_at'
    ).fetchall()
    assert {r['org']: (r['status'], r['error_code']) for r in runs} == {
        a: ('failed', 'creator_left'),
        b: ('succeeded', None),
    }
    assert db.execute("SELECT count(*) AS n FROM credit_transactions WHERE status = 'reserved'").fetchone()['n'] == 0


class _Apollo:
    """Apollo as the watchlist runner sees it: every account has no postings; records the scope of each read."""

    is_configured = True

    def __init__(self) -> None:
        self.reads: list = []

    async def get_job_postings(self, *, org_id, org_name='', page=1):
        from app.core.errors import ok

        self.reads.append(scopes.current_or_none())
        return ok(JobPostingsResult(org_name=org_name, total=0, postings=[]))


def test_a_removed_creators_watchlists_stop_and_their_other_organization_reads_its_own(db, world, broker, monkeypatch):
    """The watchlist beat, like the saved-search one: A's run, queued before U left, ends at the worker's claim;
    B's due watchlist runs as Member(B, U). A's watchlist is not due: it waits for its creator."""
    a, b, u = world['a'], world['b'], world['u']
    apollo = _Apollo()
    monkeypatch.setattr(watchlist_runner, 'ApolloClient', lambda: apollo)
    monkeypatch.setattr(watchlist_runner, 'vendor_disabled_now', lambda provider: False)
    watchlists = {}
    for org, list_id in world['lists'].items():
        db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s)',
            (list_id, Jsonb({'name': 'Acme', 'apollo_id': 'o1'}), 'acme'),
        )
        watchlists[org] = db.execute(
            'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, next_run_at)'
            " VALUES (%s, %s, 'Hiring', ARRAY['SDR'], 'weekly', now()) RETURNING id",
            (u, list_id),
        ).fetchone()['id']
    watchlist_service.enqueue(db, watchlists[a], datetime.now(UTC))  # A's run, queued while U was a member
    _leave(db, world)

    worker.dispatch_watchlists()
    sent = broker.pop('run_watchlist', [])
    for message in sent:
        _delivered(worker.run_watchlist, message)

    assert _scopes(sent) == sorted({'org': org, 'user': u}.items() for org in (a, b))
    assert apollo.reads == [scopes.Member(scopes.OrgId(b), scopes.UserId(u))]
    runs = db.execute('SELECT organization_id::text AS org, status, error_code FROM watchlist_runs').fetchall()
    assert {r['org']: (r['status'], r['error_code']) for r in runs} == {
        a: ('failed', 'creator_left'),
        b: ('succeeded', None),
    }


def test_a_webhook_a_member_queued_is_delivered_after_they_left(db, world, receiver, fernet_key, broker):
    """Organization work outlives the member who caused it (#333): ``deliver_webhooks`` runs as ForOrg of the
    organization that queued it, so the receiver gets the event U's action queued after U left A."""
    a, u = world['a'], world['u']
    endpoint = _endpoint(db, u, org=a)
    with scopes.acting_as(a, u):
        worker.deliver_webhooks.apply_async((a, endpoint, _payload()))
    _leave(db, world)

    (message,) = broker.pop('deliver_webhooks')
    _delivered(worker.deliver_webhooks, message)

    assert message[1]['zb_scope'] == {'org': a, 'user': u}
    assert len(receiver.calls) == 1
