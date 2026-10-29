import httpx
import pytest
import redis

from app.agent.tools import search_cache
from app.agent.tools.handlers import search_companies, search_people
from app.agent.tools.registry import ToolContext
from tests.conftest import body

USER = '00000000-0000-0000-0000-000000000001'
APOLLO_PERSON = {
    'total_entries': 1,
    'people': [
        {
            'id': 'p1',
            'first_name': 'Ada',
            'last_name_obfuscated': 'L***e',
            'organization': {'name': 'Acme', 'primary_domain': 'acme.test'},
        }
    ],
}
AIARK_PERSON = {
    'totalElements': 1,
    'content': [
        {
            'id': 'p2',
            'profile': {'first_name': 'Ada', 'last_name': 'Lovelace'},
            'company': {'summary': {'name': 'Acme'}, 'link': {'domain': 'acme.test'}},
        }
    ],
}


@pytest.fixture
def cache(monkeypatch):
    # fakeredis is already a locked API dependency and keeps these tests offline.
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(search_cache, '_redis', lambda: fake)
    return fake


async def test_search_people_cache_hit_returns_the_same_rows_without_a_second_vendor_call(cache, http, keys, vendor_db):
    http.handler = lambda _: httpx.Response(200, json=APOLLO_PERSON)
    ctx = ToolContext(user_id=USER, session_id='session-1')

    first = await search_people.handle({'titles': ['CEO'], 'page': 1}, ctx)
    second = await search_people.handle({'page': 1, 'titles': ['CEO']}, ctx)

    assert first.ok and second.ok and first.rows == second.rows
    assert len(http.calls) == 1
    assert len([sql for sql, _ in vendor_db.executed if 'vendor_calls' in sql]) == 1


def test_search_cache_key_includes_endpoint_and_complete_arguments():
    ctx = ToolContext(user_id=USER, session_id='session-1')
    base = {'source': 'apollo', 'page': 2, 'titles': ['CEO'], 'revenue_min': 1000000}
    key = search_cache.cache_key(
        ctx, tool='search_people', provider='apollo', endpoint='/mixed_people/api_search', arguments=base
    )

    assert key != search_cache.cache_key(
        ctx, tool='search_people', provider='apollo', endpoint='/other', arguments=base
    )
    assert key != search_cache.cache_key(
        ctx,
        tool='search_people',
        provider='apollo',
        endpoint='/mixed_people/api_search',
        arguments={**base, 'revenue_min': 2000000},
    )


async def test_search_people_cache_isolated_by_user_session_provider_filters_and_page(cache, http, keys, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'aiark_api_key', 'test-key')
    http.handler = lambda request: httpx.Response(
        200, json=AIARK_PERSON if request.url.path.endswith('/v1/people') else APOLLO_PERSON
    )
    args = {'titles': ['CEO']}
    calls = [
        (args, ToolContext(user_id=USER, session_id='session-1')),
        (args, ToolContext(user_id=USER, session_id='session-1')),  # cache hit
        (args, ToolContext(user_id=USER, session_id='session-2')),
        (args, ToolContext(user_id='00000000-0000-0000-0000-000000000002', session_id='session-1')),
        ({**args, 'page': 2}, ToolContext(user_id=USER, session_id='session-1')),
        ({**args, 'keywords': ['SaaS']}, ToolContext(user_id=USER, session_id='session-1')),
        ({**args, 'source': 'aiark'}, ToolContext(user_id=USER, session_id='session-1')),
        ({**args, 'source': 'aiark'}, ToolContext(user_id=USER, session_id='session-1')),  # cache hit
    ]

    results = [await search_people.handle(search_args, ctx) for search_args, ctx in calls]

    assert all(result.ok for result in results)
    assert len(http.calls) == 6
    assert [body(request)['page'] for request in http.calls if request.url.path.endswith('/api_search')] == [
        1,
        1,
        1,
        2,
        1,
    ]
    assert len([request for request in http.calls if request.url.path.endswith('/v1/people')]) == 1


async def test_search_companies_caches_apollo_and_aiark_results(cache, http, keys, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'aiark_api_key', 'test-key')
    http.handler = lambda request: httpx.Response(
        200,
        json=(
            {
                'totalElements': 1,
                'content': [{'id': 'c1', 'summary': {'name': 'Acme'}, 'link': {'domain': 'acme.test'}}],
            }
            if request.url.path.endswith('/v1/companies')
            else {'organizations': [{'id': 'c1', 'name': 'Acme', 'primary_domain': 'acme.test'}]}
        ),
    )
    ctx = ToolContext(user_id=USER, session_id='session-1')

    apollo = await search_companies.handle({'name': 'Acme'}, ctx)
    apollo_hit = await search_companies.handle({'name': 'Acme'}, ctx)
    aiark = await search_companies.handle({'source': 'aiark', 'domains': ['acme.test']}, ctx)
    aiark_hit = await search_companies.handle({'source': 'aiark', 'domains': ['acme.test']}, ctx)

    assert apollo.ok and apollo_hit.rows == apollo.rows
    assert aiark.ok and aiark_hit.rows == aiark.rows
    assert len(http.calls) == 2


async def test_search_errors_are_not_cached(cache, http, keys):
    http.handler = lambda _: httpx.Response(503, json={'error': 'unavailable'})
    ctx = ToolContext(user_id=USER, session_id='session-1')

    first = await search_people.handle({'titles': ['CEO']}, ctx)
    second = await search_people.handle({'titles': ['CEO']}, ctx)

    assert not first.ok and not second.ok
    assert len(http.calls) == 2


async def test_search_uses_vendor_when_redis_is_unavailable(http, keys, monkeypatch):
    def unavailable():
        raise redis.ConnectionError('redis unavailable')

    monkeypatch.setattr(search_cache, '_redis', unavailable)
    http.handler = lambda _: httpx.Response(200, json=APOLLO_PERSON)

    result = await search_people.handle({'titles': ['CEO']}, ToolContext(user_id=USER, session_id='session-1'))

    assert result.ok and result.rows
    assert len(http.calls) == 1
