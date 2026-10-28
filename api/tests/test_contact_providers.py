"""Per-provider kill switches, hit-rate rows and enrich_phone (pay-if-found)."""

import httpx
import pytest

from app.agent.summary import summary_of
from app.agent.tools import registry
from app.agent.tools.handlers import enrich_email, enrich_phone
from app.agent.tools.handlers._rows import DISABLED
from app.agent.tools.registry import ToolContext
from app.core.config import settings
from app.people import apollo_phone
from app.people.adapters.treg_contact_enrichment import TregContactEnrichment
from app.people.ports.contact_enrichment import ContactRevealHints
from app.people.treg_client import TregClient
from tests.conftest import body

CTX = ToolContext(user_id='00000000-0000-0000-0000-000000000001', list_id='l1')
PEOPLE = [
    {'row_id': 'r1', 'first_name': 'Anna', 'last_name': 'Neri', 'company_domain': 'acme.it'},
    {'row_id': 'r2', 'first_name': 'Bruno', 'last_name': 'Gialli', 'company_domain': 'acme.it'},
    {'row_id': 'r3', 'first_name': 'Dino', 'phone': '+390000'},  # skipped: has a phone
]
# Apollo knows both, and its phone job finishes with no number; PDL has Anna's mobile.
APOLLO = {'person': {'id': 'x', 'first_name': 'Anna', 'last_name': 'Neri', 'email': 'anna@acme.it'}, 'request_id': 7}
NO_NUMBER = {'webhook_result': {'people': [{'id': 'x', 'phone_numbers': []}]}}
PDL = {
    'Anna': {'status': 200, 'data': {'first_name': 'Anna', 'last_name': 'Neri', 'mobile_phone': '+39333111222'}},
    'Bruno': {'status': 200, 'data': {'first_name': 'Bruno', 'last_name': 'Gialli'}},
}


def _vendors(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith('/people/match'):
        return httpx.Response(200, json=APOLLO)
    if request.url.path.endswith('/webhook_result/7'):
        return httpx.Response(200, json=NO_NUMBER)
    if request.url.path.endswith('/call/pdl.people.enrich'):
        return httpx.Response(200, json=PDL[request.url.params['first_name']])
    # Hunter answered through the relay and has no record: a miss, not a failed source.
    return httpx.Response(404, json={'status': 404, 'error': {'type': 'not_found'}}, headers={'x-treg-cost-micro': '0'})


def _lookups(vendor_db) -> list[tuple]:
    return [p[1:] for s, p in vendor_db.executed if 'contact_lookups' in s]


async def test_phone_waterfall_bills_only_found_phones(http, keys, monkeypatch, vendor_db):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE)
    http.handler = _vendors
    res = await enrich_phone.handle({'row_ids': ['r1', 'r2', 'r3']}, CTX)
    assert res.ok and res.data['found'] == 1
    cells = {u['row_id']: u['cells'] for u in res.row_updates}
    assert cells == {
        'r1': {'phone': '+39333111222', 'phone_status': 'revealed', 'phone_source': 'treg'},
        'r2': {'phone': None, 'phone_status': 'not_found'},
    }
    assert res.data == {'searched': 2, 'found': 1, 'failed': []}
    # Apollo was asked for the phone, and an email-only answer did not stop the chain.
    assert all(body(c).get('reveal_phone_number') for c in http.calls if c.url.path.endswith('/people/match'))
    assert ('apollo', 'phone', False) in _lookups(vendor_db)
    assert ('pdl', 'phone', True) in _lookups(vendor_db)


async def test_phone_of_rows_that_all_have_one_searches_nobody(http, keys, monkeypatch):
    """#562: the work is already done, so ok with no count (never «0 telefoni», never an error), and nothing billed."""
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[2:])
    res = await enrich_phone.handle({'row_ids': ['r3']}, CTX)
    assert res.ok and not res.row_updates
    assert 'found' not in res.data and 'left to search' in res.data['nothing_left']
    assert summary_of(registry.get('enrich_phone'), res, {}) == res.outcome
    assert not http.calls


async def test_phone_source_that_failed_leaves_no_verdict(http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'treg_api_key', '')
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    http.handler = lambda r: httpx.Response(503, json={'error': 'down'})
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert res.ok and res.data['found'] == 0
    assert res.row_updates == [
        {'row_id': 'r1', 'cells': {'phone': None, 'phone_status': 'failed'}, 'if_empty': 'phone'}
    ]
    assert res.data['failed'] == [{'row_id': 'r1', 'name': 'Anna Neri'}]  # the chat says "retry", not "no phone"


async def test_phone_waterfall_with_apollo_down_leaves_no_verdict(http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[1:2])
    http.handler = lambda r: httpx.Response(503) if r.url.path.endswith('/people/match') else _vendors(r)
    res = await enrich_phone.handle({'row_ids': ['r2']}, CTX)
    assert res.row_updates == [
        {'row_id': 'r2', 'cells': {'phone': None, 'phone_status': 'failed'}, 'if_empty': 'phone'}
    ]


async def test_phone_of_an_apollo_search_row_with_apollo_off_leaves_no_verdict(http, keys, monkeypatch, kill_switch):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    preview = {'row_id': 'r1', 'person_id': 'ap1', 'first_name': 'Anna', 'last_name': 'N***i', 'company_name': 'Acme'}
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: [preview])
    kill_switch.add('vendor_disabled:apollo')
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert res.row_updates == [
        {'row_id': 'r1', 'cells': {'phone': None, 'phone_status': 'failed'}, 'if_empty': 'phone'}
    ]
    assert not http.calls  # Treg cannot match a masked surname: nobody was asked


async def test_phone_miss_without_apollo_is_not_final(http, keys, monkeypatch, kill_switch):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[1:2])
    http.handler = _vendors
    kill_switch.add('vendor_disabled:apollo')
    res = await enrich_phone.handle({'row_ids': ['r2']}, CTX)
    assert any('pdl' in c.url.path for c in http.calls)  # Treg answered: no number
    assert res.row_updates == [
        {'row_id': 'r2', 'cells': {'phone': None, 'phone_status': 'failed'}, 'if_empty': 'phone'}
    ]
    assert res.data['failed'] == [{'row_id': 'r2', 'name': 'Bruno Gialli'}]  # no verdict without Apollo: retry later


@pytest.mark.parametrize(
    ('row', 'asked'),
    [
        ({'full_name': 'Anna Maria Neri'}, {'first_name': 'Anna', 'last_name': 'Maria Neri'}),
        ({'full_name': 'Dott. Mario Rossi'}, {'first_name': 'Mario', 'last_name': 'Rossi'}),
        ({'last_name': 'Neri', 'email': 'anna@acme.it'}, {'last_name': 'Neri', 'email': 'anna@acme.it'}),
    ],
)
async def test_phone_of_an_imported_row_reaches_apollo(http, keys, monkeypatch, row, asked):
    csv_row = {'row_id': 'r1', 'company_name': 'Acme', **row}
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: [csv_row])
    http.handler = _vendors
    await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    match = [body(c) for c in http.calls if c.url.path.endswith('/people/match')]
    assert [{k: b.get(k) for k in asked} for b in match] == [asked]


async def test_email_of_a_row_with_only_a_full_name_reaches_apollo(http, keys, monkeypatch):
    monkeypatch.setattr(
        enrich_email, 'load_rows', lambda list_id, row_ids: [{'row_id': 'r1', 'full_name': 'Anna Neri'}]
    )
    http.handler = _vendors
    res = await enrich_email.handle({'row_ids': ['r1']}, CTX)
    match = [body(c) for c in http.calls if c.url.path.endswith('/people/match')]
    assert [(b['first_name'], b['last_name']) for b in match] == [('Anna', 'Neri')]
    assert res.row_updates[0]['cells']['email'] == 'anna@acme.it'


@pytest.mark.parametrize(
    'poll',
    [
        lambda r: httpx.Response(503),  # the job failed
        lambda r: httpx.Response(200, json={'webhook_result': None}),  # still pending at the deadline
        lambda r: httpx.Response(200, json={'webhook_result': {'people': [{'id': 'x', 'status': 'pending'}]}}),
        lambda r: httpx.Response(200, json={'webhook_result': {'people': [{'phone_numbers': [], 'status': 'queued'}]}}),
    ],
)
async def test_phone_job_that_never_finished_leaves_no_verdict(http, keys, monkeypatch, poll):
    monkeypatch.setattr(apollo_phone.poll_phone_result, '__kwdefaults__', {'interval': 0.0, 'ceiling': 0.0})
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    http.handler = lambda r: _vendors(r) if r.url.path.endswith('/people/match') else poll(r)
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert res.row_updates == [
        {'row_id': 'r1', 'cells': {'phone': None, 'phone_status': 'failed'}, 'if_empty': 'phone'}
    ]


@pytest.mark.parametrize(
    'cells',
    [
        {'last_name': 'Neri', 'company_domain': 'acme.it'},
        {'first_name': 'Anna', 'company_domain': 'acme.it'},  # Apollo needs the last name too
        {'first_name': 'Anna', 'last_name': 'Neri', 'person_id': '3f2a-aiark'},  # an AI Ark id is no Apollo anchor
    ],
)
async def test_phone_of_a_row_no_source_can_search_asks_nobody_and_leaves_no_verdict(http, keys, monkeypatch, cells):
    row = {'row_id': 'r1', **cells}
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: [row])
    http.handler = _vendors
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert res.row_updates == [{'row_id': 'r1', 'cells': {'phone': None}, 'if_empty': 'phone'}]
    assert not http.calls
    assert res.data['failed'] == []  # no source failed: a retry cannot help until the row gains an anchor


@pytest.mark.parametrize(
    ('cells', 'asked'),
    [
        ({'linkedin_url': 'https://linkedin.com/in/anna'}, {'linkedin_url': 'https://linkedin.com/in/anna'}),
        ({'person_id': 'ap1'}, {'id': 'ap1'}),
        (
            {'person_id': '3f2a-aiark', 'linkedin_url': 'https://linkedin.com/in/anna'},
            {'id': None, 'linkedin_url': 'https://linkedin.com/in/anna'},
        ),
    ],
)
async def test_phone_asks_apollo_with_an_anchor_it_can_match(http, keys, monkeypatch, cells, asked):
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: [{'row_id': 'r1', **cells}])
    http.handler = _vendors
    await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    match = [body(c) for c in http.calls if c.url.path.endswith('/people/match')]
    assert [{k: b.get(k) for k in asked} for b in match] == [asked]


async def test_a_found_email_offers_a_phone_search_that_found_nothing_again(http, keys, monkeypatch):
    row = {'row_id': 'r1', 'first_name': 'Anna', 'last_name': 'Neri', 'phone_status': 'not_found'}
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: [row])
    http.handler = _vendors
    res = await enrich_email.handle({'row_ids': ['r1']}, CTX)
    cells = res.row_updates[0]['cells']
    assert cells['email'] == 'anna@acme.it'
    assert 'phone_status' in cells and cells['phone_status'] is None
    http.handler = lambda r: httpx.Response(200, json={'person': None})  # no email this time
    res = await enrich_email.handle({'row_ids': ['r1']}, CTX)
    assert 'phone_status' not in res.row_updates[0]['cells']


async def test_phone_match_that_started_no_job_leaves_no_verdict(http, keys, monkeypatch):
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    http.handler = lambda r: httpx.Response(200, json={'person': APOLLO['person']})
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert res.row_updates == [
        {'row_id': 'r1', 'cells': {'phone': None, 'phone_status': 'failed'}, 'if_empty': 'phone'}
    ]


async def test_phone_refused_by_apollo_leaves_no_verdict(http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    preview = {'row_id': 'r1', 'person_id': 'ap1', 'first_name': 'Anna', 'last_name': 'N***i', 'company_name': 'Acme'}
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: [preview])
    http.handler = lambda r: httpx.Response(422, json={'error': 'webhook_url is required'})
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert res.row_updates == [
        {'row_id': 'r1', 'cells': {'phone': None, 'phone_status': 'failed'}, 'if_empty': 'phone'}
    ]


@pytest.mark.parametrize(('no_key', 'switched_off'), [('apollo_api_key', 'treg'), ('treg_api_key', 'apollo')])
async def test_phone_with_one_source_unkeyed_and_the_other_off_is_disabled(
    http, keys, monkeypatch, kill_switch, no_key, switched_off
):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    monkeypatch.setattr(settings, no_key, '')
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    kill_switch.add(f'vendor_disabled:{switched_off}')
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert not res.ok and res.error == DISABLED and not res.row_updates


async def test_phone_kill_switch_per_provider(http, keys, monkeypatch, kill_switch):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    http.handler = _vendors
    kill_switch.add('vendor_disabled:pdl')
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert res.ok and res.data['found'] == 0
    assert not any('pdl' in c.url.path for c in http.calls)
    assert any(c.url.path.endswith('/people/match') for c in http.calls)  # Apollo still on

    kill_switch.update({'vendor_disabled:apollo', 'vendor_disabled:hunter'})
    res = await enrich_phone.handle({'row_ids': ['r1']}, CTX)
    assert not res.ok and res.error == DISABLED


async def test_email_apollo_switch_leaves_the_rest_of_the_waterfall(http, keys, monkeypatch, kill_switch):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    monkeypatch.setattr(settings, 'millionverifier_api_key', '')
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    monkeypatch.setattr(enrich_email, 'email_status', lambda e: _async('unknown'))
    http.handler = lambda r: httpx.Response(
        200, json={'status': 200, 'data': {'first_name': 'Anna', 'last_name': 'Neri', 'work_email': 'a@acme.it'}}
    )
    kill_switch.add('vendor_disabled:apollo')
    res = await enrich_email.handle({'row_ids': ['r1']}, CTX)
    assert res.ok and res.data['found'] == 1
    assert res.row_updates[0]['cells']['email'] == 'a@acme.it'
    assert not any(c.url.path.endswith('/people/match') for c in http.calls)

    kill_switch.update({'vendor_disabled:aiark', 'vendor_disabled:treg'})
    res = await enrich_email.handle({'row_ids': ['r1']}, CTX)
    assert not res.ok and res.error == DISABLED


async def test_email_with_only_ai_ark_keyed_leaves_other_rows_without_a_verdict(http, keys, monkeypatch):
    """#520: Apollo has no key and Treg is not configured, so a row AI Ark did not find has nobody to ask."""
    monkeypatch.setattr(settings, 'apollo_api_key', '')
    monkeypatch.setattr(settings, 'aiark_api_key', 'aiark-key')
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: PEOPLE[:2])
    http.handler = _vendors
    res = await enrich_email.handle({'row_ids': ['r1', 'r2']}, CTX)
    assert res.ok and res.data['found'] == 0
    failed = {'email': None, 'email_status': 'failed'}  # retried by a later batch, never a final not_found
    assert res.row_updates == [{'row_id': r, 'cells': failed} for r in ('r1', 'r2')]
    assert not http.calls
    # nobody failed to answer: a retry finds the same sources
    assert res.data == {'searched': 2, 'found': 0, 'failed': [], 'statuses': ['failed', 'failed']}


async def test_email_with_only_ai_ark_keyed_still_asks_ai_ark_for_its_own_people(http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'apollo_api_key', '')
    monkeypatch.setattr(settings, 'aiark_api_key', 'aiark-key')
    rows = [{**PEOPLE[0], 'person_id': '3f2a-aiark'}, PEOPLE[1]]
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: rows)
    http.handler = lambda r: httpx.Response(503)
    res = await enrich_email.handle({'row_ids': ['r1', 'r2']}, CTX)
    assert http.calls and not any(c.url.path.endswith('/people/match') for c in http.calls)  # AI Ark only
    assert [u['cells'] for u in res.row_updates] == [{'email': None, 'email_status': 'failed'}] * 2
    assert [f['row_id'] for f in res.data['failed']] == ['r1']  # AI Ark failed for Anna; Bruno had nobody to ask


async def test_email_with_no_keyed_source_left_on_is_disabled(http, keys, monkeypatch, kill_switch):
    monkeypatch.setattr(settings, 'apollo_api_key', '')
    monkeypatch.setattr(settings, 'aiark_api_key', 'aiark-key')
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    kill_switch.add('vendor_disabled:aiark')
    res = await enrich_email.handle({'row_ids': ['r1']}, CTX)
    assert not res.ok and res.error == DISABLED and not res.row_updates and not http.calls


async def _async(value):
    return value


def test_treg_skips_switched_off_providers():
    hints = ContactRevealHints(first_name='Anna', last_name='Neri', organization_domain='acme.it')
    plan = TregContactEnrichment(TregClient(), skip=frozenset({'pdl'}))._plan(hints)
    assert [a.provider for a in plan] == ['hunter']


async def test_phone_ids_that_match_no_row_fail_and_search_nobody(keys, monkeypatch):
    """#550: ids that load no row are not a search: not ok, nothing billed."""
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: [])
    res = await enrich_phone.handle({'row_ids': ['3f2a8e4c-0000-4000-8000-000000000000']}, CTX)
    assert not res.ok and not res.row_updates and 'match a row' in res.error


async def test_phone_ids_that_partly_match_name_the_misses(http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'treg_api_key', 'treg-key')
    monkeypatch.setattr(enrich_phone, 'load_rows', lambda list_id, row_ids: PEOPLE[:1])
    http.handler = _vendors
    res = await enrich_phone.handle({'row_ids': ['r1', 'ghost']}, CTX)
    assert res.ok and res.data['searched'] == 1 and res.data['unmatched_row_ids'] == ['ghost']


async def test_phone_with_an_empty_id_list_searches_nobody_and_says_so(keys):
    """#550: enrich_phone has no next batch: an empty row_ids is the no-match failure, never «0 telefoni»."""
    res = await enrich_phone.handle({'row_ids': []}, CTX)
    assert not res.ok and not res.row_updates and res.error_code == 'skipped' and 'match a row' in res.error
