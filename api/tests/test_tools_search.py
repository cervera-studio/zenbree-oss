"""Search handlers: vendor payload → canonical rows. No live calls (httpx MockTransport)."""

import httpx
import pytest

from app.agent.tools import registry
from app.agent.tools.handlers import linkedin_search, search_companies, search_jobs_signals, search_people
from app.agent.tools.registry import ToolContext
from tests.conftest import body

CTX = ToolContext(user_id='00000000-0000-0000-0000-000000000001', project_id='00000000-0000-0000-0000-000000000002')
COMPANY_KEYS = {
    'name',
    'domain',
    'website',
    'linkedin_url',
    'industry',
    'employees',
    'city',
    'country',
    'description',
    'source',
}
PERSON_KEYS = {
    'first_name',
    'last_name',
    'full_name',
    'title',
    'company_name',
    'company_domain',
    'linkedin_url',
    'email',
    'email_status',
    'city',
    'country',
    'source',
}

APOLLO_ORG = {
    'id': 'org1',
    'name': 'Acme Srl',
    'primary_domain': 'acme.it',
    'website_url': 'https://www.acme.it',
    'linkedin_url': 'https://linkedin.com/company/acme',
    'industry': 'marketing & advertising',
    'estimated_num_employees': 25,
    'city': 'Milan',
    'country': 'Italy',
}


def test_all_m1_tools_registered():
    names = {t.name: t for t in registry.all_tools()}
    for name in ('search_companies', 'search_people', 'search_jobs_signals', 'linkedin_search'):
        assert names[name].price is None  # rows are free: the credit pays for contacts found
    assert names['enrich_email'].price.credits > 0
    # vendor picked by the arguments → the handler checks the kill switch, not the loop
    assert names['search_companies'].provider is None and names['search_people'].provider is None
    assert names['search_people'].input_schema['properties']['employee_size']['minItems'] == 2
    assert names['search_companies'].input_schema['properties']['employee_size']['maxItems'] == 2
    assert {'functions', 'technologies_any', 'job_titles', 'revenue_min'} <= names['search_people'].input_schema[
        'properties'
    ].keys()
    assert 'keyword_tags' in names['search_people'].input_schema['properties']
    assert {'domains', 'name', 'funding_types', 'job_titles'} <= names['search_companies'].input_schema[
        'properties'
    ].keys()


@pytest.mark.parametrize(
    ('tool', 'args', 'vendor', 'other'),
    [
        (search_companies, {'local_query': 'dentisti'}, 'serper', {'locations': ['Milan, Italy']}),
        (search_companies, {'locations': ['Milan, Italy']}, 'apollo', {'local_query': 'dentisti'}),
        (search_people, {'titles': ['CEO'], 'source': 'aiark'}, 'aiark', {'titles': ['CEO']}),
        (search_people, {'titles': ['CEO']}, 'apollo', {'titles': ['CEO'], 'source': 'aiark'}),
    ],
)
async def test_kill_switch_follows_the_vendor_actually_used(http, keys, kill_switch, tool, args, vendor, other):
    kill_switch.add(f'vendor_disabled:{vendor}')
    res = await tool.handle(args, CTX)
    assert not res.ok and 'disabled' in res.error and http.calls == []
    # the other path of the same tool is not blocked by this vendor's switch
    assert (await tool.handle(other, CTX)).error != res.error


async def test_search_companies_rejects_invalid_source_before_kill_switch(http, keys, kill_switch):
    kill_switch.add('vendor_disabled:apollo')
    res = await search_companies.handle({'source': 'unknown'}, CTX)
    assert not res.ok and 'unsupported' in res.error
    assert http.calls == []


async def test_search_companies_apollo(http, keys, vendor_db):
    http.handler = lambda r: httpx.Response(
        200, json={'organizations': [APOLLO_ORG], 'pagination': {'total_entries': 1, 'page': 1}}
    )
    res = await search_companies.handle(
        {
            'locations': ['Milan, Italy'],
            'industries': ['marketing', 'agenzie'],
            'job_titles': ['engineer'],
            'min_active_jobs': 2,
            'latest_funding_min': 1000000,
            'total_funding_min': 2000000,
            'revenue_min': 5000000,
            'revenue_max': 10000000,
            'sort_by': 'employees',
            'sort_descending': False,
        },
        CTX,
    )
    assert res.ok and res.entity == 'company'
    row = res.rows[0]
    assert row.keys() >= COMPANY_KEYS
    assert (row['name'], row['domain'], row['employees'], row['city'], row['country'], row['source']) == (
        'Acme Srl',
        'acme.it',
        25,
        'Milan',
        'Italy',
        'apollo',
    )
    sent = body(http.calls[0])
    assert 'agenzie' in sent.get('q_organization_keyword_tags', [])  # unknown industry → keyword, not dropped
    assert sent['q_organization_job_titles'] == ['engineer']
    assert sent['organization_num_jobs_range'] == {'min': 2}
    assert sent['latest_funding_amount_range'] == {'min': 1000000}
    assert sent['total_funding_range'] == {'min': 2000000}
    assert sent['revenue_range'] == {'min': 5000000, 'max': 10000000}
    assert sent['sort_by_field'] == 'organization_estimated_number_employees' and sent['sort_ascending'] is True
    # paid_call wrote one vendor_calls row: 1 Apollo credit
    insert = next(p for s, p in vendor_db.executed if 'vendor_calls' in s)
    assert insert[:3] == (CTX.user_id, 'apollo', 'apollo.discover_organizations') and insert[3] == 0.02
    assert 'local_location "Milan, Italy"' in res.data['local_fallback']  # 1 match in a place → Maps offered


@pytest.mark.parametrize(
    ('n', 'args', 'offered'),
    [
        (search_companies.LOCAL_FALLBACK_MIN, {'locations': ['Milan, Italy']}, False),
        (0, {'locations': ['Milan, Italy']}, True),
        (0, {'locations': ['Milan, Italy'], 'page': 2}, False),
        (0, {'keywords': ['saas']}, False),  # no place to search on Maps
    ],
)
async def test_search_companies_local_fallback_threshold(http, keys, vendor_db, n, args, offered):
    orgs = [{**APOLLO_ORG, 'id': f'o{i}', 'primary_domain': f'c{i}.it'} for i in range(n)]
    http.handler = lambda r: httpx.Response(200, json={'organizations': orgs, 'pagination': {'total_entries': n}})
    res = await search_companies.handle(args, CTX)
    assert res.ok and ('local_fallback' in res.data) is offered


async def test_a_zero_row_discovery_page_costs_one_apollo_credit_and_the_user_nothing(http, keys, vendor_db):
    """#259: Apollo meters the page, not the rows, like the same endpoint's name search."""
    http.handler = lambda r: httpx.Response(200, json={'organizations': [], 'pagination': {'total_entries': 0}})
    res = await search_companies.handle({'keywords': ['saas']}, CTX)
    assert res.ok
    insert = next(p for s, p in vendor_db.executed if 'vendor_calls' in s)
    assert insert[:3] == (CTX.user_id, 'apollo', 'apollo.discover_organizations') and insert[3] == 0.02


async def test_search_companies_no_local_fallback_when_maps_disabled(http, keys, kill_switch):
    kill_switch.add('vendor_disabled:serper')
    http.handler = lambda r: httpx.Response(200, json={'organizations': [], 'pagination': {'total_entries': 0}})
    res = await search_companies.handle({'locations': ['Milan, Italy']}, CTX)
    assert res.ok and 'local_fallback' not in res.data


async def test_search_companies_local_serper(http, keys, vendor_db):
    http.handler = lambda r: httpx.Response(
        200,
        json={
            'places': [
                {
                    'title': 'Trattoria Da Mario',
                    'address': 'Via Roma 1, 20121 Milano MI, Italia',
                    'website': 'https://www.damario.it/',
                    'phoneNumber': '+39 02 123',
                    'rating': 4.5,
                    'ratingCount': 120,
                    'category': 'Ristorante',
                    'cid': '42',
                }
            ]
        },
    )
    res = await search_companies.handle({'local_query': 'trattoria', 'local_location': 'Milano'}, CTX)
    assert res.ok
    row = res.rows[0]
    assert row.keys() >= COMPANY_KEYS
    assert (row['name'], row['domain'], row['country'], row['source'], row['rating']) == (
        'Trattoria Da Mario',
        'damario.it',
        'Italia',
        'google_maps',
        4.5,
    )
    assert [p[1:3] for s, p in vendor_db.executed if 'vendor_calls' in s] == [('serper', 'serper.places')]


async def test_search_companies_apollo_name(http, keys):
    http.handler = lambda r: httpx.Response(
        200,
        json={
            'accounts': [],
            'organizations': [
                {
                    'id': 'org-1',
                    'name': 'Beta SpA',
                    'primary_domain': 'beta.it',
                    'website_url': 'https://beta.it',
                    'estimated_num_employees': 40,
                    'city': 'Milano',
                    'country': 'Italy',
                }
            ],
        },
    )
    res = await search_companies.handle({'name': 'Beta', 'locations': ['Milano']}, CTX)
    assert res.ok and res.rows[0]['name'] == 'Beta SpA' and res.rows[0]['domain'] == 'beta.it'
    assert body(http.calls[0])['q_organization_name'] == 'Beta'
    assert body(http.calls[0])['organization_locations'] == ['Milano']


async def test_search_jobs_signals(http, keys):
    def handler(r):
        if r.url.path.endswith('/job_postings'):
            return httpx.Response(
                200,
                json={
                    'organization_job_postings': [
                        {'title': 'Sales Development Representative', 'url': 'https://jobs/1'},
                        {'title': 'Designer'},
                    ]
                },
            )
        return httpx.Response(200, json={'organizations': [APOLLO_ORG], 'pagination': {'total_entries': 1}})

    http.handler = handler
    res = await search_jobs_signals.handle({'job_titles': ['Sales Development'], 'locations': ['Italy']}, CTX)
    assert res.ok
    row = res.rows[0]
    assert row.keys() >= COMPANY_KEYS
    assert row['hiring_for'] == 'Sales Development'
    assert row['open_roles'] == 'Sales Development Representative' and row['job_url'] == 'https://jobs/1'


async def test_search_jobs_signals_requires_titles(keys):
    assert not (await search_jobs_signals.handle({}, CTX)).ok


async def test_search_people_apollo(http, keys):
    http.handler = lambda r: httpx.Response(
        200,
        json={
            'total_entries': 1,
            'people': [
                {
                    'id': 'p1',
                    'first_name': 'Giulia',
                    'last_name_obfuscated': 'Ro***i',
                    'title': 'Head of Sales',
                    'linkedin_url': 'https://linkedin.com/in/giulia',
                    'has_email': True,
                    'organization': {'name': 'Acme Srl', 'primary_domain': 'acme.it'},
                }
            ],
        },
    )
    res = await search_people.handle(
        {
            'titles': ['Head of Sales'],
            'locations': ['Milan, Italy'],
            'functions': ['sales'],
            'keyword_tags': ['b2b saas'],
            'company_locations': ['Italy'],
            'company_ids': ['org-1'],
            'technologies_all': ['shopify'],
            'technologies_any': ['hubspot'],
            'technologies_excluded': ['salesforce'],
            'email_status': ['verified'],
            'include_similar_titles': False,
            'job_titles': ['engineer'],
            'job_locations': ['Milan'],
            'min_active_jobs': 1,
            'max_active_jobs': 4,
            'job_posted_after': '2026-01-01',
            'job_posted_before': '2026-03-01',
            'revenue_min': 1000000,
            'revenue_max': 2000000,
        },
        CTX,
    )
    assert res.ok and res.entity == 'person'
    row = res.rows[0]
    assert row.keys() >= PERSON_KEYS
    assert (row['first_name'], row['last_name'], row['company_domain'], row['person_id'], row['source']) == (
        'Giulia',
        'Ro···i',
        'acme.it',
        'p1',
        'apollo',
    )
    sent = body(http.calls[0])
    assert sent['person_functions'] == ['sales'] and sent['organization_ids'] == ['org-1']
    assert sent['q_organization_keyword_tags'] == ['b2b saas']
    assert sent['currently_using_all_of_technology_uids'] == ['shopify']
    assert sent['currently_using_any_of_technology_uids'] == ['hubspot']
    assert sent['currently_not_using_any_of_technology_uids'] == ['salesforce']
    assert sent['contact_email_status'] == ['verified'] and sent['include_similar_titles'] is False
    assert sent['q_organization_job_titles'] == ['engineer'] and sent['organization_job_locations'] == ['Milan']
    assert sent['organization_num_jobs_range'] == {'min': 1, 'max': 4}
    assert sent['organization_job_posted_at_range'] == {'min': '2026-01-01', 'max': '2026-03-01'}
    assert sent['revenue_range'] == {'min': 1000000, 'max': 2000000}


async def test_search_people_aiark(http, keys, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'aiark_api_key', 'test-key')
    http.handler = lambda r: httpx.Response(
        200,
        json={
            'totalElements': 1,
            'content': [
                {
                    'id': '3f2b1c9e-0000-4000-8000-000000000001',
                    'profile': {'first_name': 'Luca', 'last_name': 'Bianchi', 'title': 'CEO'},
                    'company': {'summary': {'name': 'Beta Spa'}, 'link': {'domain': 'beta.it'}},
                }
            ],
        },
    )
    res = await search_people.handle(
        {
            'titles': ['CEO'],
            'source': 'aiark',
            'employee_size': [10, 200],
            'company_locations': ['Europe Italy Milan'],
            'technologies_any': ['hubspot'],
            'revenue_min': 1000000,
            'revenue_max': 2000000,
        },
        CTX,
    )
    assert res.ok
    row = res.rows[0]
    assert row.keys() >= PERSON_KEYS
    assert (row['full_name'], row['company_name'], row['company_domain'], row['source']) == (
        'Luca Bianchi',
        'Beta Spa',
        'beta.it',
        'aiark',
    )
    sent = body(http.calls[0])
    assert sent['page'] == 0
    assert sent['account']['employeeSize']['range'] == [{'start': 10, 'end': 200}]
    assert sent['account']['location']['any']['include'] == ['Europe Italy Milan']
    assert sent['account']['technologies']['any']['include'] == {'mode': 'WORD', 'content': ['hubspot']}
    assert sent['account']['revenue']['range'] == [{'start': 1000000, 'end': 2000000}]


async def test_search_companies_aiark(http, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'aiark_api_key', 'test-key')
    http.handler = lambda r: httpx.Response(
        200,
        json={
            'totalElements': 1,
            'content': [
                {
                    'id': 'company-1',
                    'summary': {'name': 'Beta SpA', 'staff': {'total': 40}},
                    'link': {'domain': 'beta.it', 'website': 'https://beta.it'},
                    'financial': {'funding': {'type': 'SEED'}},
                    'location': {'headquarter': {'city': 'Milano', 'country': 'Italy'}},
                }
            ],
        },
    )
    res = await search_companies.handle(
        {
            'source': 'aiark',
            'domains': ['beta.it'],
            'employee_size': [10, 100],
            'funding_types': ['SEED'],
            'revenue_min': 1000000,
            'page': 2,
        },
        CTX,
    )
    assert res.ok and res.rows[0]['domain'] == 'beta.it' and res.rows[0]['employees'] == 40
    sent = body(http.calls[0])
    assert sent['page'] == 1 and sent['account']['domain']['any']['include'] == ['beta.it']
    assert sent['account']['employeeSize']['range'] == [{'start': 10, 'end': 100}]
    assert sent['account']['funding']['type'] == ['SEED']
    assert sent['account']['revenue']['range'] == [{'start': 1000000, 'end': 1000000000000}]


async def test_linkedin_search_without_connection_guides_user(monkeypatch, http, keys):
    monkeypatch.setattr(linkedin_search, 'connected_account_id', lambda project_id: None)
    res = await linkedin_search.handle({'keywords': 'head of sales'}, CTX)
    assert not res.ok and 'connect' in res.error.lower() and 'LinkedIn' in res.error
    assert http.calls == []  # never dialled Unipile


@pytest.mark.parametrize('api', ['classic', 'sales_navigator'])
async def test_linkedin_search_people(monkeypatch, http, keys, api):
    monkeypatch.setattr(linkedin_search, 'connected_account_id', lambda project_id: 'acc_1')

    def handler(r):
        if r.url.path.endswith('/search/parameters'):
            return httpx.Response(
                200, json={'object': 'LinkedinSearchParametersList', 'items': [{'id': '102277331', 'title': 'Milan'}]}
            )
        return httpx.Response(
            200,
            json={
                'object': 'LinkedinSearch',
                'cursor': 'next1',
                'paging': {'total_count': 900},
                'items': [
                    {
                        'type': 'PEOPLE',
                        'id': 'ACoA1',
                        'name': 'Marco Verdi',
                        'first_name': 'Marco',
                        'last_name': 'Verdi',
                        'headline': 'Sales @ Gamma',
                        'location': 'Milan, Lombardy, Italy',
                        'public_profile_url': 'https://www.linkedin.com/in/marcoverdi',
                        'current_positions': [{'company': 'Gamma', 'role': 'Head of Sales'}],
                    }
                ],
            },
        )

    http.handler = handler
    res = await linkedin_search.handle({'keywords': 'head of sales', 'location': 'Milano', 'api': api}, CTX)
    assert res.ok and res.entity == 'person' and res.data['cursor'] == 'next1'
    row = res.rows[0]
    assert row.keys() >= PERSON_KEYS
    assert (row['full_name'], row['title'], row['company_name'], row['city'], row['country']) == (
        'Marco Verdi',
        'Head of Sales',
        'Gamma',
        'Milan',
        'Italy',
    )
    search = next(c for c in http.calls if c.url.path.endswith('/linkedin/search'))
    assert search.url.params['account_id'] == 'acc_1'
    sent = body(search)
    assert sent['location'] == ({'include': ['102277331']} if api == 'sales_navigator' else ['102277331'])
