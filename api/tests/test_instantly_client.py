"""Instantly API v2 client and lead fields (#929), against a mock transport. No database."""

import json
import uuid

import httpx
import pytest

from app.core import tasks
from app.core.errors import is_err
from app.integrations import instantly_client as client
from app.integrations import instantly_fields as fields
from app.integrations.instantly_fields import Mapping

KEY = 'ins-secret-key'
CAMPAIGN = str(uuid.uuid4())


def _answer(status=200, body=None, headers=None):
    return lambda request: httpx.Response(status, json=body if body is not None else {}, headers=headers or {})


async def test_the_key_goes_as_a_bearer_token_and_the_workspace_is_read(http):
    http.handler = _answer(body={'id': 'ws-1', 'name': 'Acme'})
    found = await client.workspace(KEY)
    assert found.value == client.Workspace(id='ws-1', name='Acme')
    request = http.calls[0]
    assert request.headers['authorization'] == f'Bearer {KEY}' and request.url.path == '/api/v2/workspaces/current'


@pytest.mark.parametrize(
    ('status', 'code'),
    [
        (401, 'INSTANTLY.INVALID_KEY'),
        (402, 'INSTANTLY.PLAN_REQUIRED'),
        (403, 'INSTANTLY.MISSING_SCOPE'),
        (404, 'INSTANTLY.NOT_FOUND'),
        (429, 'INSTANTLY.RATE_LIMITED'),
        (400, 'INSTANTLY.REJECTED'),
        (500, 'INSTANTLY.UNKNOWN_OUTCOME'),
        (503, 'INSTANTLY.UNKNOWN_OUTCOME'),
    ],
)
async def test_each_failure_has_its_own_code(http, status, code):
    http.handler = _answer(status)
    result = await client.workspace(KEY)
    assert is_err(result) and result.error.code == code


async def test_retry_after_is_kept_for_a_throttled_call(http):
    http.handler = _answer(429, headers={'Retry-After': '7'})
    assert (await client.workspace(KEY)).error.context['retry_after'] == 7


async def test_no_connection_is_safe_to_repeat_and_a_lost_answer_is_not(http):
    def refuse(request):
        raise httpx.ConnectError('down', request=request)

    def lose(request):
        raise httpx.ReadTimeout('slow', request=request)

    http.handler = refuse
    assert (await client.add_leads(KEY, CAMPAIGN, [{'email': 'a@b.test'}])).error.code == 'INSTANTLY.UNREACHABLE'
    http.handler = lose
    assert (await client.add_leads(KEY, CAMPAIGN, [{'email': 'a@b.test'}])).error.code == 'INSTANTLY.UNKNOWN_OUTCOME'


async def test_an_unreadable_success_is_not_taken_for_one(http):
    http.handler = lambda request: httpx.Response(200, content=b'<html>')
    assert (await client.add_leads(KEY, CAMPAIGN, [{'email': 'a@b.test'}])).error.code == 'INSTANTLY.UNKNOWN_OUTCOME'
    http.handler = _answer(body={'status': 'success'})  # no created_leads: nothing can be mapped back
    assert (await client.add_leads(KEY, CAMPAIGN, [{'email': 'a@b.test'}])).error.code == 'INSTANTLY.UNKNOWN_OUTCOME'


async def test_campaigns_are_read_page_by_page(http):
    pages = {
        None: {'items': [{'id': 'c1', 'name': 'One', 'status': 1}], 'next_starting_after': 'c1'},
        'c1': {
            'items': [
                {'id': 'c2', 'name': 'Two', 'status': 0},
                {'name': 'no id'},
                {'id': 'c3', 'name': 'Three', 'status': 4},
            ]
        },
    }
    http.handler = lambda request: httpx.Response(200, json=pages[request.url.params.get('starting_after')])
    found = await client.list_campaigns(KEY)
    assert [(c.id, c.may_send) for c in found.value] == [('c1', True), ('c2', False), ('c3', True)]
    assert http.calls[0].url.params['limit'] == '100'


async def test_accounts_are_read_page_by_page_and_an_item_without_email_is_left_out(http):
    pages = {
        None: {
            'items': [{'email': 'ann@acme.test', 'status': 1, 'warmup_status': 1, 'daily_limit': 30}],
            'next_starting_after': 'a1',
        },
        'a1': {'items': [{'email': ' bob@acme.test ', 'status': -3, 'warmup_status': -1}, {'status': 1}]},
    }
    http.handler = lambda request: httpx.Response(200, json=pages[request.url.params.get('starting_after')])
    found = (await client.list_accounts(KEY)).value
    assert found == [
        client.Account('ann@acme.test', 1, 1, 30),
        client.Account('bob@acme.test', -3, -1, None),
    ]
    assert http.calls[0].url.path == '/api/v2/accounts' and http.calls[0].url.params['limit'] == '100'


async def test_accounts_stop_at_the_cap(http):
    def page(request):
        n = int(request.url.params.get('starting_after') or 0)
        items = [{'email': f'm{n + i}@acme.test', 'status': 1, 'warmup_status': 1} for i in range(100)]
        return httpx.Response(200, json={'items': items, 'next_starting_after': str(n + 100)})

    http.handler = page
    assert len((await client.list_accounts(KEY)).value) == client.MAX_ACCOUNTS == 500
    assert len(http.calls) == 5


@pytest.mark.parametrize('value', [None, 'active', 7, True, 1.0])
async def test_a_status_outside_the_enum_reads_unknown_not_an_error(http, value):
    http.handler = _answer(
        body={'items': [{'email': 'a@b.test', 'status': value, 'warmup_status': value, 'daily_limit': True}]}
    )
    account = (await client.list_accounts(KEY)).value[0]
    assert (account.status, account.warmup_status, account.daily_limit) == (
        client.UNKNOWN_STATUS,
        client.UNKNOWN_STATUS,
        None,
    )


async def test_a_campaign_id_that_is_not_a_uuid_never_reaches_the_url(http):
    result = await client.get_campaign(KEY, '../workspaces/current')
    assert result.error.code == 'INSTANTLY.NOT_FOUND' and http.calls == []


async def test_created_leads_map_back_by_input_index(http):
    http.handler = _answer(
        body={'created_leads': [{'index': 1, 'id': 'l-2'}, {'index': 0, 'id': 'l-1'}], 'duplicated_leads': 1}
    )
    added = await client.add_leads(KEY, CAMPAIGN, [{'email': 'a@b.test'}, {'email': 'c@d.test'}, {'email': 'e@f.test'}])
    assert added.value.created == {0: 'l-1', 1: 'l-2'} and added.value.counts == {'duplicated_leads': 1}
    sent = json.loads(http.calls[0].content)
    assert sent['campaign_id'] == CAMPAIGN and 'list_id' not in sent


async def test_lead_batches_are_bounded():
    with pytest.raises(ValueError, match='between 1 and 1000'):
        await client.add_leads(KEY, CAMPAIGN, [{'email': 'a@b.test'}] * 1001)


async def test_leads_are_found_by_email(http):
    http.handler = _answer(body={'items': [{'id': 'l-1', 'email': 'A@B.test'}, {'email': 'x'}]})
    assert (await client.find_leads(KEY, CAMPAIGN, ['a@b.test'])).value == {'a@b.test': 'l-1'}
    sent = json.loads(http.calls[0].content)
    assert sent == {'campaign': CAMPAIGN, 'contacts': ['a@b.test'], 'limit': 100}


def test_the_batch_task_runs_as_the_member_who_confirmed_it():
    from app.worker import celery_app

    assert 'run_instantly_enrollment' in celery_app.tasks
    assert tasks.KINDS['run_instantly_enrollment'] == tasks.MEMBER


# ── lead fields ────────────────────────────────────────────────────────────


def test_the_lead_carries_the_mapped_public_cells_only():
    cells = {
        'full_name': 'Dott. Eva De Luca',
        'title': 'CTO',
        'company_name': 'Acme',
        'company_domain': 'acme.test',
        'email_status': 'verified',
        'email_source': 'apollo',
        'person_id': 'p-1',
        'city': 'Rome',
    }
    out = fields.lead(cells, 'eva@acme.test', Mapping(custom=['city']))
    assert out == {
        'email': 'eva@acme.test',
        'first_name': 'Eva',
        'last_name': 'De Luca',
        'job_title': 'CTO',
        'company_name': 'Acme',
        'website': 'acme.test',
        'custom_variables': {'city': 'Rome'},
    }


def test_a_column_that_must_not_leave_is_refused():
    columns = {'city', 'email_status', 'email_source', 'person_id', 'email', 'title'}
    for key in ('email_status', 'email_source', 'person_id', 'email'):
        assert fields.check(Mapping(custom=[key]), columns)
        assert fields.check(Mapping(fields={'personalization': key}), columns)
    assert fields.check(Mapping(fields={'job_title': 'nope'}), columns)
    assert fields.check(Mapping(fields={'shoe_size': 'city'}), columns)
    assert fields.check(Mapping(custom=['City Name']), {'City Name'})
    assert fields.check(Mapping(fields={'job_title': 'title', 'website': ''}, custom=['city']), columns) is None


def test_a_lead_value_is_one_line_without_tokens():
    out = fields.lead(
        {'title': 'Head\nof {{Sales}} [EU]', 'city': ' Rome\r\n'}, 'eva@acme.test', Mapping(custom=['city'])
    )
    assert out['job_title'] == 'Head of Sales EU' and out['custom_variables'] == {'city': 'Rome'}


def test_an_email_must_look_like_one():
    assert fields.clean_email({'email': ' Eva@Acme.test '}) == 'eva@acme.test'
    assert fields.clean_email({'email': 'nope'}) == '' and fields.clean_email({}) == ''
