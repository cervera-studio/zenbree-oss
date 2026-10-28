"""HubSpot read gate rules and client (#120). HubSpot mocked; no DB."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.crm import gate, hubspot_client

NOW = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
ME = '77'  # the connected user's own HubSpot owner id


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('1790000000000', datetime.fromtimestamp(1_790_000_000, tz=UTC)),  # epoch ms on older payloads
        ('yesterday', None),
    ],
)
def test_parse_datetime(raw, expected):
    assert gate.parse_datetime(raw) == expected


def _contacted(days_ago: float) -> dict:
    return {'email': 'anna@client.test', 'notes_last_contacted': (NOW - timedelta(days=days_ago)).isoformat()}


def _verdict(contact, company=None, me=ME):
    return gate.verdict(contact, company, me, NOW)


def test_cooldown_holds_for_thirty_days_after_the_last_contact():
    assert _verdict(_contacted(5)) == ('HUBSPOT_COOLDOWN', NOW + timedelta(days=25))
    assert _verdict(_contacted(29.9))[0] == 'HUBSPOT_COOLDOWN'
    assert _verdict(_contacted(30)) == (None, None)  # the window has lapsed
    assert _verdict(_contacted(40)) == (None, None)


def test_my_own_accounts_are_not_blocked_by_ownership():
    mine = {'hubspot_owner_id': ME}
    assert _verdict(mine, mine) == (None, None)
    # Without an owner id of our own (the user owns nothing there), any owner is someone else.
    assert _verdict(mine, None, me=None) == ('HUBSPOT_OWNED', None)


def test_the_first_matching_rule_wins():
    everything = {'hs_email_optout': 'true', 'hubspot_owner_id': '12', **_contacted(1)}
    customer = {'lifecyclestage': 'customer', 'hs_num_open_deals': '3', 'hubspot_owner_id': '12'}
    assert _verdict(everything, customer)[0] == 'HUBSPOT_OPTED_OUT'
    assert _verdict({**everything, 'hs_email_optout': 'false'}, customer)[0] == 'HUBSPOT_CUSTOMER'
    assert _verdict(_contacted(1), {'hs_num_open_deals': '1', 'hubspot_owner_id': '12'})[0] == 'HUBSPOT_OPEN_DEAL'
    assert _verdict({'hubspot_owner_id': '12', **_contacted(1)})[0] == 'HUBSPOT_OWNED'


def _batch(status=207, results=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={'results': results if results is not None else []})

    return handler


async def test_read_contacts_by_email(http):
    http.handler = _batch(
        results=[
            {'id': '101', 'properties': {'email': 'Anna@Client.test', 'associatedcompanyid': '9'}},
            {'id': '102', 'properties': None},  # malformed record: ignored
        ]
    )
    got = await hubspot_client.read_contacts('at-1', ['anna@client.test', 'nobody@client.test'])
    assert got.value == {'anna@client.test': {'email': 'Anna@Client.test', 'associatedcompanyid': '9', 'id': '101'}}
    (request,) = http.calls
    assert str(request.url) == 'https://api.hubapi.com/crm/objects/2026-03/contacts/batch/read'
    assert request.headers['authorization'] == 'Bearer at-1'
    body = json.loads(request.content)
    assert body['idProperty'] == 'email' and body['inputs'] == [
        {'id': 'anna@client.test'},
        {'id': 'nobody@client.test'},
    ]
    assert set(body['properties']) == {
        'email',
        'notes_last_contacted',
        'hs_email_optout',
        'hubspot_owner_id',
        'associatedcompanyid',
    }


async def test_read_companies_by_id(http):
    http.handler = _batch(status=200, results=[{'id': '9', 'properties': {'lifecyclestage': 'customer'}}])
    got = await hubspot_client.read_companies('at-1', ['9', '10'])
    assert got.value == {'9': {'lifecyclestage': 'customer'}}
    (request,) = http.calls
    assert str(request.url) == 'https://api.hubapi.com/crm/objects/2026-03/companies/batch/read'
    body = json.loads(request.content)
    assert body['inputs'] == [{'id': '9'}, {'id': '10'}] and 'idProperty' not in body
    assert set(body['properties']) == {'lifecyclestage', 'hubspot_owner_id', 'hs_num_open_deals'}


async def test_find_owner_by_email(http):
    http.handler = _batch(status=200, results=[{'id': 77, 'email': 'me@acme.test'}])
    assert (await hubspot_client.find_owner('at-1', 'me@acme.test')).value == '77'
    (request,) = http.calls
    assert request.method == 'GET' and request.url.path == '/crm/owners/2026-03'
    assert request.url.params['email'] == 'me@acme.test'
    http.handler = _batch(status=200)
    assert (await hubspot_client.find_owner('at-1', 'me@acme.test')).value is None  # owns nothing


@pytest.mark.parametrize(
    ('status', 'code'),
    [
        (429, 'HUBSPOT.RATE_LIMITED'),
    ],
)
async def test_read_errors(http, status, code):
    http.handler = _batch(status=status)
    assert (await hubspot_client.read_contacts('at-1', ['a@b.test'])).error.code == code
    assert (await hubspot_client.read_companies('at-1', ['9'])).error.code == code


async def test_read_contacts_transport_and_body_errors(http):
    def boom(request):
        raise httpx.ConnectError('down')

    http.handler = boom
    assert (await hubspot_client.read_contacts('at-1', ['a@b.test'])).error.code == 'HUBSPOT.TRANSPORT'
    http.handler = lambda r: httpx.Response(200, json={'status': 'COMPLETE'})
    assert (await hubspot_client.read_contacts('at-1', ['a@b.test'])).error.code == 'HUBSPOT.INVALID_JSON'


async def test_reads_refuse_an_oversized_batch():
    with pytest.raises(ValueError, match='at most 100'):
        await hubspot_client.read_contacts('at-1', [f'p{i}@b.test' for i in range(101)])
    with pytest.raises(ValueError, match='at most 100'):
        await hubspot_client.read_companies('at-1', [str(i) for i in range(101)])


def test_retry_delay_doubles_to_an_hour_and_respects_retry_after():
    assert [gate.retry_delay(n) for n in range(8)] == [60, 120, 240, 480, 960, 1920, 3600, 3600]
    assert gate.retry_delay(0, retry_after=300) == 300  # HubSpot asked for longer
    assert gate.retry_delay(4, retry_after=30) == 960
    assert gate.ReadFailed('HUBSPOT.AUTH').permanent and not gate.ReadFailed('HUBSPOT.RATE_LIMITED').permanent
