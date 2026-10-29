import httpx
import pytest

from app.integrations.instantly_sync.stops import remove
from app.outreach.stop_outbox import Due, Later, NotEnrolled, Refused, Stop, Stopped, StopStatus

KEY = 'ins-key'
WS = 'ws-1'
LEAD = '01a1454b-2cd7-7456-bc97-879268d3da5d'
CAMPAIGN = '0a1b2c3d-0000-4000-8000-000000000001'


def due(*, lead_id: str | None = LEAD, keep_lead=False, outcome='accepted', workspace=WS) -> Due:
    return Due(Stop('s-1', StopStatus.PENDING, 0), 'ann@acme.test', keep_lead, outcome, lead_id, CAMPAIGN, workspace)


async def same(stop: Stop) -> bool:
    return True


def go(item: Due, unchanged=same):
    return remove(KEY, WS, item, unchanged)


def instantly(answers: dict[str, object]):
    def handle(request: httpx.Request) -> httpx.Response:
        answer = answers.get(request.method, 404)
        if isinstance(answer, type):
            raise answer('lost', request=request)
        status, body = answer if isinstance(answer, tuple) else (answer, {})
        return httpx.Response(status, json=body)

    return handle


def calls(http) -> list[str]:
    return [f'{r.method} {r.url.path}' for r in http.calls]


@pytest.mark.parametrize('status', [3, -1, -2, -3, 3.0])
async def test_a_lead_instantly_already_stopped_is_never_deleted(http, status):
    http.handler = instantly({'GET': (200, {'id': LEAD, 'status': status})})
    assert await go(due()) == Stopped()
    assert calls(http) == [f'GET /api/v2/leads/{LEAD}']


@pytest.mark.parametrize('answers', [{'GET': 404}, {'GET': (200, {'status': 2}), 'DELETE': 404}])
async def test_a_lead_instantly_no_longer_has_is_stopped(http, answers):
    http.handler = instantly(answers)
    assert await go(due()) == Stopped()


@pytest.mark.parametrize('status', [1, 2, None, 'active', True])
async def test_a_lead_still_sending_or_unreadable_is_deleted(http, status):
    http.handler = instantly({'GET': (200, {'status': status}), 'DELETE': 200})
    assert await go(due()) == Stopped()
    assert [r.method for r in http.calls] == ['GET', 'DELETE']


async def test_the_lead_whose_own_reply_caused_the_stop_is_never_deleted(http):
    http.handler = instantly({'GET': (200, {'status': 1}), 'DELETE': 200})
    assert await go(due(keep_lead=True)) == Refused('INSTANTLY.STOP_IN_INSTANTLY')
    http.handler = instantly({'GET': (200, {'status': 3}), 'DELETE': 200})
    assert await go(due(keep_lead=True)) == Stopped()
    http.handler = instantly({'GET': 404, 'DELETE': 200})
    assert await go(due(keep_lead=True)) == Stopped()
    assert 'DELETE' not in [r.method for r in http.calls]


@pytest.mark.parametrize(
    ('answers', 'event'),
    [
        ({'GET': httpx.ReadTimeout}, Later('INSTANTLY.UNKNOWN_OUTCOME')),
        ({'GET': httpx.ConnectError}, Later('INSTANTLY.UNREACHABLE')),
        ({'GET': 503}, Later('INSTANTLY.UNKNOWN_OUTCOME')),
        ({'GET': 429}, Later('INSTANTLY.RATE_LIMITED')),
        ({'GET': (200, {'status': 1}), 'DELETE': httpx.ReadTimeout}, Later('INSTANTLY.UNKNOWN_OUTCOME')),
        ({'GET': (200, {'status': 1}), 'DELETE': 429}, Later('INSTANTLY.RATE_LIMITED')),
        ({'GET': 401}, Refused('INSTANTLY.INVALID_KEY')),
        ({'GET': 402}, Refused('INSTANTLY.PLAN_REQUIRED')),
        ({'GET': 403}, Refused('INSTANTLY.STOP_SCOPE')),
        ({'GET': (200, {'status': 1}), 'DELETE': 403}, Refused('INSTANTLY.STOP_SCOPE')),
        ({'GET': (200, {'status': 1}), 'DELETE': 401}, Refused('INSTANTLY.STOP_SCOPE')),
        ({'GET': (200, {'status': 1}), 'DELETE': 400}, Refused('INSTANTLY.REJECTED')),
    ],
)
async def test_each_failure_waits_or_fails_and_never_confirms(http, answers, event):
    http.handler = instantly(answers)
    assert await go(due()) == event


async def test_a_lead_without_an_instantly_id_is_found_in_its_campaign_by_email(http):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/v2/leads/list':
            return httpx.Response(200, json={'items': [{'id': LEAD, 'email': 'Ann@Acme.test'}]})
        return httpx.Response(200, json={'status': 1})

    http.handler = handle
    assert await go(due(lead_id=None, outcome='uncertain')) == Stopped()
    assert calls(http) == ['POST /api/v2/leads/list', f'GET /api/v2/leads/{LEAD}', f'DELETE /api/v2/leads/{LEAD}']
    assert b'"contacts":["ann@acme.test"]' in http.calls[0].content.replace(b' ', b'')


async def test_a_lead_instantly_does_not_list_yet_waits(http):
    http.handler = lambda request: httpx.Response(200, json={'items': []})
    assert await go(due(lead_id=None, outcome='publishing')) == Later('INSTANTLY.LEAD_NOT_SETTLED')
    assert calls(http) == ['POST /api/v2/leads/list']


@pytest.mark.parametrize('outcome', ['skipped', 'failed', 'pending'])
async def test_a_lead_that_never_reached_instantly_needs_no_call(http, outcome):
    assert await go(due(outcome=outcome)) == NotEnrolled()
    assert http.calls == []


async def test_a_lead_of_another_workspace_is_never_asked_with_this_key(http):
    assert await go(due(workspace='ws-old')) == Refused('INSTANTLY.WORKSPACE_CHANGED')
    assert http.calls == []


async def test_a_lead_id_is_one_path_segment(http):
    http.handler = instantly({'GET': 404})
    await go(due(lead_id='../campaigns/x'))
    assert http.calls[0].url.raw_path == b'/api/v2/leads/..%2Fcampaigns%2Fx'
