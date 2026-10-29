import httpx
import pytest

from app.agent.summary import summary_of
from app.agent.tools import registry
from app.agent.tools.handlers import enrich_email
from app.agent.tools.registry import ToolContext
from app.core.config import settings
from app.lists.routes import get_rows
from app.lists.rows import add_rows, apply_row_updates, create_list
from app.people import email_verification
from app.people.email_verification import Deliverability, EmailVerification, email_status
from tests.conftest import act_in_own_org, body, member
from tests.test_agent import _job, _user, needs_db
from tests.test_agent import db as db
from tests.web_fixtures import web_fixture, web_row

CTX = ToolContext(user_id='00000000-0000-0000-0000-000000000001', list_id='l1')

# Apollo match answers by first name; MillionVerifier answers by address.
MATCHES = {
    'Anna': {
        'person': {
            'id': 'a',
            'first_name': 'Anna',
            'last_name': 'Neri',
            'title': 'CEO',
            'email': 'anna@acme.it',
            'email_status': 'verified',
        }
    },
    'Bruno': {
        'person': {
            'id': 'b',
            'first_name': 'Bruno',
            'last_name': 'Gialli',
            'email': 'bruno@acme.it',
            'email_status': 'guessed',
        }
    },
    'Carla': {},  # no match
    'Elsa': {'person': {'id': 'e', 'first_name': 'Elsa', 'last_name': 'Verdi', 'email': 'elsa@acme.it'}},
}
MV = {'anna@acme.it': 'ok', 'bruno@acme.it': 'invalid', 'elsa@acme.it': 'catch_all'}


def _vendor(request: httpx.Request) -> httpx.Response:
    if 'millionverifier' in request.url.host:
        return httpx.Response(200, json={'result': MV[request.url.params['email']], 'error': ''})
    return httpx.Response(200, json=MATCHES[body(request)['first_name']])


PEOPLE = [
    {'row_id': 'r1', 'first_name': 'Anna', 'last_name': 'Ne··i', 'company_domain': 'https://www.acme.it/'},
    {'row_id': 'r2', 'first_name': 'Bruno', 'last_name': 'Gialli', 'company_name': 'Acme'},
    {'row_id': 'r3', 'first_name': 'Carla', 'last_name': 'Blu', 'company_domain': 'acme.it'},
    {'row_id': 'r4', 'first_name': 'Dino', 'email': 'already@acme.it'},  # skipped: has an email
]


@pytest.mark.web_fixture
async def test_waterfall_bills_only_usable_emails(http, keys, monkeypatch, vendor_db):
    monkeypatch.setattr(settings, 'millionverifier_api_key', 'mv-key')
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: PEOPLE)
    http.handler = _vendor
    res = await enrich_email.handle({'people': PEOPLE}, CTX)
    assert res.ok and res.entity == 'person'
    cells = {u['row_id']: u['cells'] for u in res.row_updates}
    assert set(cells) == {'r1', 'r2', 'r3'}
    assert cells['r1']['email'] == 'anna@acme.it' and cells['r1']['email_status'] == 'verified'
    assert cells['r1']['last_name'] == 'Neri' and cells['r1']['full_name'] == 'Anna Neri'  # mask replaced
    # the model names people as the source revealed them, never as the user guessed a masked surname (#560)
    assert res.data['revealed'] == [
        {'row_id': 'r1', 'name': 'Anna Neri'},
        {'row_id': 'r2', 'name': 'Bruno Gialli'},
    ]
    assert cells['r2']['email_status'] == 'invalid'
    assert cells['r3'] == {'email': None, 'email_status': 'not_found'}
    assert res.data['found'] == 1  # found 2, one rejected by the verifier, one not found
    summary = summary_of(registry.get('enrich_email'), res, {})
    assert (summary.kind, summary.searched, summary.tally.found, summary.tally.failed) == ('partial', 3, 1, 0)
    web_fixture('summary-email-found', summary.model_dump(mode='json'))
    # The masked surname was not sent; the domain was normalised.
    anna = next(body(c) for c in http.calls if c.url.path.endswith('/people/match') and body(c)['first_name'] == 'Anna')
    assert 'last_name' not in anna and anna.get('domain') == 'acme.it'
    vendors = [p[1] for s, p in vendor_db.executed if 'vendor_calls' in s]
    assert vendors.count('millionverifier') == 2 and vendors.count('apollo') == 3


def _apollo_down_for(*names: str):
    def vendor(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(503, json={'error': 'down'})
            if body(request).get('first_name') in names
            else _vendor(request)
        )

    return vendor


@pytest.mark.web_fixture
async def test_a_source_that_failed_leaves_the_email_open_and_names_the_person(http, keys, monkeypatch):
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: [PEOPLE[0], PEOPLE[2]])
    http.handler = _apollo_down_for('Anna')
    res = await enrich_email.handle({'row_ids': ['r1', 'r3']}, CTX)
    assert res.ok and res.data['found'] == 0
    cells = {u['row_id']: u['cells'] for u in res.row_updates}
    assert cells == {
        'r1': {'email': None, 'email_status': 'failed'},
        'r3': {'email': None, 'email_status': 'not_found'},
    }
    assert res.data['failed'] == [{'row_id': 'r1', 'name': 'Anna Ne··i'}]
    assert res.data['searched'] == 2 and res.data['found'] == 0
    summary = summary_of(registry.get('enrich_email'), res, {})
    assert (summary.kind, summary.searched, summary.tally.found, summary.tally.failed) == ('none_found', 2, 0, 1)
    web_fixture('summary-email-some-failed', summary.model_dump(mode='json'))


@pytest.mark.web_fixture
@pytest.mark.parametrize(
    ('name', 'people', 'down', 'summary'),
    [
        ('summary-email-all-failed', [PEOPLE[0], PEOPLE[2]], ('Anna', 'Carla'), ('all_failed', 2, 2)),
        ('summary-email-none-found', [PEOPLE[2]], (), ('none_found', 1, 0)),
    ],
)
async def test_a_batch_that_found_no_email_counts_what_it_searched_and_what_failed(
    http, keys, monkeypatch, name, people, down, summary
):
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: people)
    http.handler = _apollo_down_for(*down)
    res = await enrich_email.handle({'row_ids': [p['row_id'] for p in people]}, CTX)
    assert res.ok and res.data['found'] == 0
    found = summary_of(registry.get('enrich_email'), res, {})
    assert (found.kind, found.searched, found.tally.failed) == summary and found.tally.found == 0
    web_fixture(name, found.model_dump(mode='json'))


# One person for each email state the list shows, as the waterfall and the rows route leave it.
EVERY_STATE = [
    {'first_name': 'Anna', 'last_name': 'Neri', 'company_domain': 'acme.it'},
    {'first_name': 'Bruno', 'last_name': 'Gialli', 'company_name': 'Acme'},
    {'first_name': 'Elsa', 'last_name': 'Verdi', 'company_domain': 'acme.it'},
    {'first_name': 'Carla', 'last_name': 'Blu', 'company_domain': 'acme.it'},
    {'first_name': 'Fabio', 'last_name': 'Rosa', 'company_domain': 'acme.it'},
    {'first_name': 'Gina', 'last_name': 'Neri', 'email': 'gina@acme.it'},  # typed by hand: no verdict
    {'first_name': 'Ugo', 'last_name': 'Neri', 'company_domain': 'acme.it'},  # left out of the batch
]


@needs_db
@pytest.mark.web_fixture
async def test_the_list_reads_each_email_state_the_waterfall_stored(db, http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'millionverifier_api_key', 'mv-key')
    user_id = _user(db)
    act_in_own_org(db, user_id)  # the tool runs in its turn's scope
    session_id, job_id = _job(db, user_id, 'find work emails')
    list_id = create_list(db, user_id=user_id, session_id=session_id, name='People', entity='person')
    row_ids = add_rows(db, list_id, 'person', EVERY_STATE)
    ctx = ToolContext(user_id=user_id, job_id=job_id, list_id=list_id, session_id=session_id)
    http.handler = _apollo_down_for('Fabio')

    res = await enrich_email.handle({'row_ids': row_ids[:5]}, ctx)
    apply_row_updates(db, list_id, 'person', res.row_updates)

    rows = get_rows(list_id, member(db, user_id), cursor=0, limit=100)['rows']
    by_name = {r['cells']['first_name']: r for r in rows}
    assert {name: r['lookups']['email'] for name, r in by_name.items()} == {
        'Anna': {'state': 'found', 'verdict': 'verified'},
        'Bruno': {'state': 'found', 'verdict': 'invalid'},
        'Elsa': {'state': 'found', 'verdict': 'catch_all'},
        'Carla': {'state': 'not_found'},
        'Fabio': {'state': 'failed'},
        'Gina': {'state': 'found', 'verdict': 'unknown'},
        'Ugo': {'state': 'not_searched'},
    }
    summary = summary_of(registry.get('enrich_email'), res, {})
    assert (summary.kind, summary.searched, summary.tally.found, summary.tally.failed) == ('partial', 5, 2, 1)
    for name, fixture in [
        ('Anna', 'email-found'),
        ('Bruno', 'email-invalid'),
        ('Elsa', 'email-catch-all'),
        ('Carla', 'email-not-found'),
        ('Fabio', 'email-failed'),
        ('Gina', 'email-unknown'),
        ('Ugo', 'email-not-searched'),
    ]:
        web_row(fixture, by_name[name])
    web_fixture('summary-email-found-and-failed', summary.model_dump(mode='json'))


@needs_db
async def test_a_row_whose_source_failed_is_taken_again(db, http, keys):
    user_id = _user(db)
    act_in_own_org(db, user_id)  # the tool runs in its turn's scope
    session_id, job_id = _job(db, user_id, 'find work emails')
    list_id = create_list(db, user_id=user_id, session_id=session_id, name='People', entity='person')
    anna, _carla = add_rows(db, list_id, 'person', [PEOPLE[0], PEOPLE[2]])
    ctx = ToolContext(user_id=user_id, job_id=job_id, list_id=list_id, session_id=session_id)
    http.handler = _apollo_down_for('Anna')

    res = await enrich_email.handle({}, ctx)  # an uploaded list: the next rows never tried
    apply_row_updates(db, list_id, 'person', res.row_updates)
    assert res.data['searched'] == 2 and [p['row_id'] for p in res.data['failed']] == [anna]
    # Carla's miss is final; Anna's outage is not, so the next batch takes her again.
    assert enrich_email.next_unenriched(list_id) == [anna]


@needs_db
async def test_rows_never_tried_come_before_a_retry(db, http, keys, monkeypatch):
    """A lasting outage must not fill every batch with the same failed rows: the rows never tried go first."""
    user_id = _user(db)
    act_in_own_org(db, user_id)  # the tool runs in its turn's scope
    session_id, job_id = _job(db, user_id, 'find work emails')
    list_id = create_list(db, user_id=user_id, session_id=session_id, name='People', entity='person')
    anna, carla = add_rows(db, list_id, 'person', [PEOPLE[0], PEOPLE[2]])
    ctx = ToolContext(user_id=user_id, job_id=job_id, list_id=list_id, session_id=session_id)
    http.handler = _apollo_down_for('Anna')
    monkeypatch.setattr(enrich_email, 'MAX_PEOPLE', 1)

    res = await enrich_email.handle({}, ctx)
    apply_row_updates(db, list_id, 'person', res.row_updates)
    assert [p['row_id'] for p in res.data['failed']] == [anna]
    assert enrich_email.next_unenriched(list_id) == [carla]


def _searched_nobody(res) -> None:
    """#562: the work is already done, so ok with no count (never «0 email», never an error), and nothing billed."""
    assert res.ok and not res.row_updates
    assert 'found' not in res.data and 'left to search' in res.data['nothing_left']
    assert summary_of(registry.get('enrich_email'), res, {}) == res.outcome and res.outcome.kind == 'nothing_to_search'


async def test_rows_that_all_have_an_email_search_nobody(http, keys, monkeypatch):
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: [{'row_id': 'r', 'email': 'x@y.z'}])
    res = await enrich_email.handle({'people': [{'row_id': 'r'}, {'row_id': 'gone'}]}, CTX)
    _searched_nobody(res)
    assert res.data['unmatched_row_ids'] == ['gone']
    assert not http.calls


@pytest.mark.parametrize('args', [{}, {'row_ids': []}, {'people': []}])
async def test_a_list_with_no_row_left_searches_nobody(http, keys, monkeypatch, args):
    monkeypatch.setattr(enrich_email, 'next_unenriched', lambda list_id: [])
    _searched_nobody(await enrich_email.handle(args, CTX))
    assert not http.calls


@needs_db
async def test_a_list_whose_people_all_have_an_email_or_a_verdict_searches_nobody(db, http, keys):
    user_id = _user(db)
    act_in_own_org(db, user_id)  # the tool runs in its turn's scope
    session_id, job_id = _job(db, user_id, 'find work emails')
    list_id = create_list(db, user_id=user_id, session_id=session_id, name='People', entity='person')
    add_rows(
        db,
        list_id,
        'person',
        [
            {**PEOPLE[0], 'email': 'anna@acme.it', 'email_status': 'verified'},
            {**PEOPLE[2], 'email_status': 'not_found'},
        ],
    )
    ctx = ToolContext(user_id=user_id, job_id=job_id, list_id=list_id, session_id=session_id)
    _searched_nobody(await enrich_email.handle({}, ctx))
    assert not http.calls


async def test_verifier_falls_back_to_mx_without_key(monkeypatch, http):
    monkeypatch.setattr(settings, 'millionverifier_api_key', '')
    monkeypatch.setattr(
        email_verification, 'verify_email', lambda e: EmailVerification(e, Deliverability.DELIVERABLE, 'mx')
    )
    assert await email_status('a@acme.it') == 'mx_ok'
    assert http.calls == []


async def test_verifier_with_its_switch_on_falls_back_to_mx(monkeypatch, http, kill_switch):
    kill_switch.add('vendor_disabled:millionverifier')
    monkeypatch.setattr(settings, 'millionverifier_api_key', 'mv-key')
    monkeypatch.setattr(
        email_verification, 'verify_email', lambda e: EmailVerification(e, Deliverability.DELIVERABLE, 'mx')
    )
    assert await email_status('a@acme.it') == 'mx_ok'
    assert http.calls == []


async def test_verifier_error_falls_back_to_mx(monkeypatch, http, vendor_db):
    monkeypatch.setattr(settings, 'millionverifier_api_key', 'mv-key')
    monkeypatch.setattr(
        email_verification, 'verify_email', lambda e: EmailVerification(e, Deliverability.UNKNOWN, 'timeout')
    )
    http.handler = lambda r: httpx.Response(200, json={'result': '', 'error': 'Insufficient credits'})
    assert await email_status('a@acme.it') == 'unknown'
    row = next(p for s, p in vendor_db.executed if 'vendor_calls' in s)
    assert row[1] == 'millionverifier' and row[3] == 0.0 and row[5] is False  # failed check is not billed


RID = '11111111-1111-4111-8111-111111111111'


async def test_row_ids_read_from_the_jobs_list(http, keys, monkeypatch):
    import contextlib

    from tests.conftest import FakeConn

    class Rows(FakeConn):
        def fetchall(self):
            return [{'id': RID, 'cells': {'first_name': 'Carla', 'last_name': 'Blu', 'company_domain': 'acme.it'}}]

    fake = Rows()
    monkeypatch.setattr(enrich_email, 'get_conn', lambda: contextlib.nullcontext(fake))
    monkeypatch.setattr(enrich_email, 'list_rules', lambda conn, list_id: frozenset())
    http.handler = _vendor
    res = await enrich_email.handle({'row_ids': [RID, 'not-a-uuid']}, CTX)
    assert res.ok and [u['row_id'] for u in res.row_updates] == [RID]
    assert fake.executed[0][1] == ('l1', [RID])  # scoped by the job's list, junk id dropped


@needs_db
async def test_excluded_rows_never_reach_enrichment_or_billing(db, monkeypatch):
    user_id = _user(db)
    act_in_own_org(db, user_id)  # the tool runs in its turn's scope
    session_id, job_id = _job(db, user_id, 'find work emails')
    list_id = create_list(db, user_id=user_id, session_id=session_id, name='People', entity='person')
    excluded_id, included_id = add_rows(
        db,
        list_id,
        'person',
        [
            {'first_name': 'Excluded', 'last_name': 'Person', 'company_domain': 'excluded.test'},
            {'first_name': 'Included', 'last_name': 'Person', 'company_domain': 'included.test'},
        ],
    )
    db.execute('UPDATE jobs SET list_id = %s WHERE id = %s', (list_id, job_id))
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (excluded_id,))
    ctx = ToolContext(user_id=user_id, job_id=job_id, list_id=list_id, session_id=session_id)

    loaded = enrich_email.load_rows(list_id, [excluded_id, included_id])
    assert [person['row_id'] for person in loaded] == [included_id]

    reached_provider: list[str] = []

    class ConfiguredProvider:
        is_configured = True

    async def fake_enrich_one(person, *_clients):
        reached_provider.append(person['row_id'])
        return {
            'row_id': person['row_id'],
            'cells': {'email': 'included@example.test', 'email_status': 'verified'},
        }

    monkeypatch.setattr(enrich_email, 'ApolloClient', ConfiguredProvider)
    monkeypatch.setattr(enrich_email, 'TregClient', ConfiguredProvider)
    monkeypatch.setattr(enrich_email, 'AiArkClient', ConfiguredProvider)
    monkeypatch.setattr(enrich_email, '_enrich_one', fake_enrich_one)

    excluded = await enrich_email.handle({'row_ids': [excluded_id]}, ctx)
    assert not excluded.ok and not excluded.row_updates and 'match a row' in excluded.error
    assert reached_provider == []

    excluded_person = await enrich_email.handle({'people': [{'row_id': excluded_id}]}, ctx)
    assert not excluded_person.ok and not excluded_person.row_updates
    assert reached_provider == []

    included = await enrich_email.handle({'row_ids': [excluded_id, included_id]}, ctx)
    assert included.ok and included.data['found'] == 1 and included.data['searched'] == 1
    assert reached_provider == [included_id]
    assert [update['row_id'] for update in included.row_updates] == [included_id]


@pytest.mark.parametrize('args', [{'row_ids': ['r1']}, {}])
async def test_a_call_without_a_list_is_refused(keys, args):
    res = await enrich_email.handle(args, ToolContext(user_id=CTX.user_id))
    assert not res.ok and 'no list yet' in res.error


@pytest.mark.parametrize('outcome', ['ok', 'vendor_error', 'transport_error'])
async def test_millionverifier_key_never_logged(monkeypatch, http, caplog, app_logging, outcome):
    """MillionVerifier takes its key as ?api=: neither our logs nor httpx's may carry the URL."""
    import logging

    import structlog

    monkeypatch.setattr(settings, 'millionverifier_api_key', 'MV-SECRET-KEY')
    monkeypatch.setattr(email_verification, 'verify_email', lambda e: EmailVerification(e, Deliverability.UNKNOWN, 'x'))

    def handler(request):
        if outcome == 'transport_error':
            raise httpx.ConnectError(f'failed: {request.url}', request=request)
        if outcome == 'vendor_error':
            return httpx.Response(401, json={'error': 'Invalid api key MV-SECRET-KEY'})
        return httpx.Response(200, json={'result': 'ok', 'error': ''})

    http.handler = handler
    with caplog.at_level(logging.INFO), structlog.testing.capture_logs() as logs:
        await email_status('a@acme.it')
    assert 'MV-SECRET-KEY' not in repr(logs) + caplog.text


async def test_ids_that_match_no_row_fail_and_search_nobody(keys, monkeypatch):
    """#550: the model passed the list id as a row id. That is no search: not ok, no «0 email», nothing billed."""
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: [])
    for args in (
        {'row_ids': ['3f2a8e4c-0000-4000-8000-000000000000']},
        {'people': [{'row_id': 'nope'}]},
        {'people': [{'row_id': ''}]},
        {'row_ids': ['']},
    ):
        res = await enrich_email.handle(args, CTX)
        assert not res.ok and not res.row_updates
        assert 'match a row' in res.error


async def test_ids_that_partly_match_search_the_rest_and_name_the_misses(http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'millionverifier_api_key', 'mv-key')
    http.handler = _vendor
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    res = await enrich_email.handle({'row_ids': ['r1', 'R9', 'r9']}, CTX)
    assert res.ok and res.data['searched'] == 1 and res.data['unmatched_row_ids'] == ['R9', 'r9']
    clean = await enrich_email.handle({'row_ids': ['r1']}, CTX)
    assert 'unmatched_row_ids' not in clean.data


@pytest.mark.parametrize('args', [{}, {'row_ids': []}, {'people': []}])
async def test_an_empty_id_list_takes_the_next_rows_like_no_ids(http, keys, monkeypatch, args):
    """#550: no id given is no search of nobody: it takes the next batch, never «0 email» for a search not made."""
    monkeypatch.setattr(settings, 'millionverifier_api_key', 'mv-key')
    http.handler = _vendor
    monkeypatch.setattr(enrich_email, 'next_unenriched', lambda list_id: ['r1'])
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: PEOPLE[:1] if row_ids == ['r1'] else [])
    res = await enrich_email.handle(args, CTX)
    assert res.ok and res.data['searched'] == 1 and 'unmatched_row_ids' not in res.data


@needs_db
async def test_a_row_id_in_another_uuid_spelling_matches_its_row(db, monkeypatch):
    """Postgres reads a braced, upper-case or hyphen-less uuid as the same row: it is searched, never named unmatched."""
    user_id = _user(db)
    act_in_own_org(db, user_id)  # the tool runs in its turn's scope
    session_id, _ = _job(db, user_id, 'find work emails')
    list_id = create_list(db, user_id=user_id, session_id=session_id, name='People', entity='person')
    (row_id,) = add_rows(db, list_id, 'person', [{'first_name': 'Anna', 'last_name': 'Neri', 'company_name': 'Acme'}])

    async def found(person, *_clients):
        return {'row_id': person['row_id'], 'cells': {'email': 'anna@acme.it', 'email_status': 'verified'}}

    class Configured:
        is_configured = True

    for client in ('ApolloClient', 'TregClient', 'AiArkClient'):
        monkeypatch.setattr(enrich_email, client, Configured)
    monkeypatch.setattr(enrich_email, '_enrich_one', found)
    ctx = ToolContext(user_id=user_id, list_id=list_id, session_id=session_id)
    for spelling in (row_id.replace('-', '').upper(), '{' + row_id + '}', f'urn:uuid:{row_id}'):
        res = await enrich_email.handle({'row_ids': [spelling]}, ctx)
        assert res.ok and res.data['searched'] == 1 and 'unmatched_row_ids' not in res.data, spelling
