"""The email and phone handlers claim the rows they search before their first vendor call (#659)."""

import httpx
import pytest

from app.agent.tools.handlers import enrich_email, enrich_phone
from app.agent.tools.registry import ToolContext
from app.core.config import settings
from tests.conftest import body

PEOPLE = [
    {'row_id': 'r1', 'first_name': 'Anna', 'last_name': 'Neri', 'company_domain': 'acme.it'},
    {'row_id': 'r2', 'first_name': 'Bruno', 'last_name': 'Gialli', 'company_domain': 'acme.it'},
    {'row_id': 'r3', 'first_name': 'Dino', 'email': 'dino@acme.it', 'phone': '+390000'},  # nothing to search
    {'row_id': 'r4', 'full_name': 'Elsa'},  # a phone search has nothing to match it by
]


class Claims:
    """Records the order of the claim and the vendor calls; ``fail`` makes the first vendor call raise."""

    def __init__(self, http, fail: bool = False) -> None:
        self.http, self.fail, self.claimed, self.calls_at_claim = http, fail, [], []

    def __call__(self, key, row_ids) -> None:
        self.claimed.append((key, list(row_ids)))
        self.calls_at_claim.append(len(self.http.calls))


def _vendor(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={'person': {'id': 'x', 'first_name': body(request).get('first_name', '')}})


@pytest.fixture
def claims(http, keys, monkeypatch):
    http.handler = _vendor
    monkeypatch.setattr(
        enrich_email, 'load_rows', lambda list_id, row_ids: [p for p in PEOPLE if p['row_id'] in row_ids]
    )
    monkeypatch.setattr(
        enrich_phone, 'load_rows', lambda list_id, row_ids: [p for p in PEOPLE if p['row_id'] in row_ids]
    )
    monkeypatch.setattr(settings, 'treg_api_key', '')
    return Claims(http)


def _ctx(claims: Claims) -> ToolContext:
    return ToolContext(user_id='00000000-0000-0000-0000-000000000001', list_id='l1', claims=claims)  # type: ignore[arg-type]


async def test_enrich_email_claims_the_people_it_searches_before_any_vendor_call(claims):
    res = await enrich_email.handle({'row_ids': ['r1', 'r2', 'r3']}, _ctx(claims))

    assert res.ok
    assert claims.claimed == [('email', ['r1', 'r2'])]  # r3 has an email: not searched
    assert claims.calls_at_claim == [0]
    assert claims.http.calls  # and the vendors were asked after it


async def test_enrich_phone_claims_only_the_people_a_source_can_be_asked_about(claims):
    res = await enrich_phone.handle({'row_ids': ['r1', 'r2', 'r4']}, _ctx(claims))

    assert res.ok
    assert claims.claimed == [('phone', ['r1', 'r2'])]
    assert claims.calls_at_claim == [0]


@pytest.mark.parametrize('handle', [enrich_email.handle, enrich_phone.handle])
async def test_nothing_to_search_claims_nothing(claims, handle):
    res = await handle({'row_ids': ['r3']}, _ctx(claims))

    assert res.ok and claims.claimed == []
