"""The company study as a stream (#350): progress frames, the summary read mid-reply, and cancel by disconnect."""

import asyncio
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.llm.base import LLMEvent, ProviderAPIError
from app.main import app
from app.onboarding import study, study_stream, support
from app.onboarding.partial_reply import completed_summary
from app.onboarding.study import Drafting, OfferFound, PageRead
from tests.conftest import act_as, member, project, sign_in
from tests.test_agent import _user
from tests.test_agent import db as db
from tests.test_onboarding import FakeLLM, frames, needs_db, run_study

REPLY = (
    '{"company_name": "Acme", "summary": "Vende \\"ancore\\" alle agenzie", "personas": ["CEO"], '
    '"markets": [], "pain_points": [], "solution": "", "buying_triggers": [], "exclusions": [], "proof": ""}'
)
HOME = {'success': True, 'url': 'https://acme.it', 'content': '[Chi siamo](/chi-siamo) [Servizi](/servizi)'}


def _site(monkeypatch, readable=('https://acme.it', 'https://acme.it/chi-siamo', 'https://acme.it/servizi')):
    def fetch(url):
        if url not in readable:
            return {'success': False, 'url': url}
        return HOME if url == 'https://acme.it' else {'success': True, 'url': url, 'content': f'page {url}'}

    monkeypatch.setattr(study, 'fetch_page', fetch)


def test_summary_is_read_only_once_its_closing_quote_arrives():
    ends = [completed_summary(REPLY[:n]) for n in range(len(REPLY) + 1)]
    first = next(n for n, s in enumerate(ends) if s)
    assert REPLY[first - 1] == '"' and REPLY[first - 2] != '\\'
    assert set(ends[first:]) == {'Vende "ancore" alle agenzie'}
    assert completed_summary('{"summary": "half \\') is None
    assert completed_summary('{"summary": ["a list"]}') is None
    assert completed_summary('{"summary": "  "}') is None


def test_offer_comes_while_the_reply_is_still_streaming(monkeypatch):
    llm = FakeLLM(REPLY)
    monkeypatch.setattr(study, 'get_provider', lambda: llm)
    sent: list[int] = []
    original = llm.chat_stream

    async def counting(**kwargs):
        async for event in original(**kwargs):
            sent.append(1)
            yield event

    llm.chat_stream = counting

    async def offer_at() -> int:
        async for event in study.study_company(None, 'Vendiamo ancore', 'it'):
            if isinstance(event, OfferFound):
                return len(sent)
        raise AssertionError('no offer')

    assert asyncio.run(offer_at()) < len(REPLY) // 7


def test_description_only_study_reads_no_page(monkeypatch):
    monkeypatch.setattr(study, 'get_provider', lambda: FakeLLM(REPLY))
    events = run_study(None, 'Vendiamo ancore')
    assert not any(isinstance(e, PageRead) for e in events) and events[0] == Drafting()


# ── DB ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def user(db):
    uid = _user(db)
    who = member(db, uid)
    sign_in(app, who)
    try:
        yield who
    finally:
        app.dependency_overrides.clear()


def _used(db, user) -> int:
    return db.execute('SELECT onboarding_free_used FROM user_credits WHERE user_id = %s', (user.id,)).fetchone()[
        'onboarding_free_used'
    ]


def _saved(db, user):
    return db.execute(
        'SELECT website, profile FROM business_profiles WHERE project_id = %s', (project(db, user.id),)
    ).fetchone()


@needs_db
def test_website_study_streams_its_progress_and_saves_the_profile(user, db, monkeypatch):
    _site(monkeypatch, readable=('https://acme.it', 'https://acme.it/servizi'))
    monkeypatch.setattr(study, 'get_provider', lambda: FakeLLM(REPLY))
    res = TestClient(app).post('/v1/onboarding/profile', json={'website': 'acme.it'})
    assert res.headers['content-type'].startswith('text/event-stream')
    got = frames(res)
    assert [e for e, _ in got] == ['page', 'page', 'drafting', 'offer', 'profile']
    assert [d['url'] for e, d in got if e == 'page'] == ['https://acme.it', 'https://acme.it/servizi']
    assert got[3][1] == {'summary': 'Vende "ancore" alle agenzie'}
    assert got[4][1]['pages'] == ['https://acme.it', 'https://acme.it/servizi']
    assert got[4][1]['profile']['company_name'] == 'Acme'
    saved = _saved(db, user)
    assert saved['website'] == 'https://acme.it' and saved['profile']['summary'] == 'Vende "ancore" alle agenzie'
    own = db.execute(
        "SELECT value FROM exclusions WHERE project_id = %s AND origin = 'own_domain'", (project(db, user.id),)
    ).fetchall()
    assert [r['value'] for r in own] == ['acme.it']
    assert _used(db, user) == 1


@needs_db
def test_a_failed_llm_arrives_as_an_error_frame_and_gives_the_run_back(user, db, monkeypatch):
    class Down(FakeLLM):
        async def chat_stream(self, **kwargs):
            raise ProviderAPIError('provider down')
            yield

    monkeypatch.setattr(study, 'get_provider', lambda: Down(''))
    res = TestClient(app).post('/v1/onboarding/profile', json={'description': 'Vendiamo ancore'})
    assert frames(res) == [
        ('drafting', {}),
        ('error', {'code': 'ONBOARDING.STUDY_FAILED', 'message': 'Could not analyse the business, try again'}),
    ]
    assert _used(db, user) == 0 and _saved(db, user) is None


@needs_db
def test_a_provider_failure_after_streamed_text_keeps_the_free_run_used(user, db, monkeypatch):
    class DropsMidStream(FakeLLM):
        async def chat_stream(self, **kwargs):
            yield LLMEvent('text_delta', text='{"company')
            raise ProviderAPIError('connection lost')

    monkeypatch.setattr(study, 'get_provider', lambda: DropsMidStream(''))
    res = TestClient(app).post('/v1/onboarding/profile', json={'description': 'Vendiamo ancore'})
    assert [e for e, _ in frames(res)] == ['drafting', 'error']
    assert _used(db, user) == 1


@needs_db
def test_a_save_failure_after_the_profile_arrived_keeps_the_free_run_used(user, db, monkeypatch):
    monkeypatch.setattr(study, 'get_provider', lambda: FakeLLM(REPLY))

    def broken(*args):
        raise RuntimeError('save failed')

    monkeypatch.setattr(study_stream, 'save_study', broken)
    res = TestClient(app).post('/v1/onboarding/profile', json={'description': 'Vendiamo ancore'})
    assert frames(res)[-1][0] == 'error'
    assert _used(db, user) == 1


@needs_db
def test_a_billed_reply_that_fails_keeps_the_free_run_used(user, db, monkeypatch):
    monkeypatch.setattr(study, 'get_provider', lambda: FakeLLM('not json'))
    res = TestClient(app).post('/v1/onboarding/profile', json={'description': 'Vendiamo ancore'})
    assert [e for e, _ in frames(res)] == ['drafting', 'error']
    assert _used(db, user) == 1


def _stalled_after(*events):
    """A study that yields ``events`` and then waits for good: only a cancel ends it."""

    async def fake(website, description, locale):
        for event in events:
            yield event
        await asyncio.Event().wait()

    return fake


async def _leave_after(user, project_id: str, event: str) -> list[str]:
    """Drives the stream as the route does, the client leaving once ``event`` arrived."""
    left = asyncio.Event()

    async def receive():
        await left.wait()
        return {'type': 'http.disconnect'}

    seen = []
    body = study_stream.StudyIn(website='acme.it')
    async for frame in study_stream.study_stream(body, user, project_id, 'it', receive):
        seen.append(frame.split('\n')[0].removeprefix('event: '))
        if seen[-1] == event:
            left.set()
    return seen


@needs_db
@pytest.mark.parametrize(
    ('events', 'leave_on', 'used'),
    [((PageRead('https://acme.it'),), 'page', 0), ((PageRead('https://acme.it'), Drafting()), 'drafting', 1)],
)
def test_leaving_cancels_the_study_and_refunds_only_before_the_llm(user, db, monkeypatch, events, leave_on, used):
    monkeypatch.setattr(study_stream, 'study_company', _stalled_after(*events))
    act_as(user.org_id, user.id)  # the study's request
    support.use_free_run(user.org_id)
    seen = asyncio.run(asyncio.wait_for(_leave_after(user, project(db, user.id), leave_on), 5))
    assert seen[-1] == leave_on
    assert _used(db, user) == used and _saved(db, user) is None


@needs_db
def test_a_real_disconnect_stops_the_study(user, db, monkeypatch):
    """Over uvicorn, as in production: closing the connection mid-analysis cancels the study."""
    cancelled = threading.Event()

    async def stalled(website, description, locale):
        try:
            yield PageRead('https://acme.it')
            yield Drafting()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(study_stream, 'study_company', stalled)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=0, lifespan='off', log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with httpx.stream('POST', f'http://127.0.0.1:{port}/v1/onboarding/profile', json={'website': 'acme.it'}) as res:
            for line in res.iter_lines():
                if line == 'event: drafting':
                    break
        assert cancelled.wait(5), 'the study kept running after the client left'
        assert _saved(db, user) is None
    finally:
        server.should_exit = True
        thread.join(5)
