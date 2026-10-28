import asyncio

from app.agent.tools.handlers.search_companies import discover_companies
from app.agent.tools.handlers.search_people import discover_people


class FakeClient:
    async def search_people(self, **kw):
        self.kw = kw
        return None


def _run(args):
    client = FakeClient()
    asyncio.run(discover_people(client, args, 1))  # type: ignore[arg-type] # fake client
    return client.kw


def test_unknown_industry_becomes_keyword_tag():
    kw = _run({'industries': ['Manufacturing', 'IT Services'], 'keyword_tags': ['saas']})
    assert kw['keyword_tags'] == ['saas', 'Manufacturing']
    assert kw['industry_tag_ids']


def test_only_unknown_industry_still_filters():
    kw = _run({'industries': ['Produzione']})
    assert kw['keyword_tags'] == ['Produzione']
    assert kw['industry_tag_ids'] is None


def test_duplicates_collapse_and_known_names_make_no_keyword():
    assert _run({'industries': ['IT Services', 'it services']})['keyword_tags'] is None
    kw = _run({'industries': ['Produzione', 'produzione'], 'keyword_tags': ['Produzione']})
    assert kw['keyword_tags'] == ['Produzione']


def test_search_companies_same_rule():
    class Client:
        async def discover_organizations(self, **kw):
            self.kw = kw

    client = Client()
    args = {'industries': ['Software', 'software', 'Produzione', 'produzione'], 'keywords': ['Produzione']}
    asyncio.run(discover_companies(client, args, 1))  # type: ignore[arg-type] # fake client
    assert client.kw['keyword_tags'] == ['Produzione']
    assert client.kw['industry_tag_ids']
