"""#572: every prompt that writes text the user reads names the user's interface language ("user".locale)."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.agent import loop
from app.agent.chat_request import ChatRequest
from app.agent.language import language_reminder
from app.agent.loop import JobCancelled, Turn
from app.agent.step_text import FAILED_SEARCH_RULE
from app.core.locale import LOCALES
from app.llm.base import LLMEvent, ToolCall
from app.onboarding import routes, study
from app.onboarding.study import Profile
from app.projects.routes import default_project
from tests.conftest import act_in_own_org, member, project, start_turn
from tests.test_agent import DB_URL, _run, _user
from tests.test_agent import db as db
from tests.test_onboarding import SEGMENT, FakeLLM
from tests.test_onboarding import client as client

needs_db = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: language SQL tests NOT run')

LANGUAGES = {'en': 'English', 'it': 'Italian', 'es': 'Spanish', 'fr': 'French'}
assert set(LANGUAGES) == set(LOCALES), 'name the new language in LANGUAGES'
# Written out once, not built from app.agent.language: a change to the wording shows up here.
INSTRUCTION = {
    locale: (
        f"# Language\nThe user's interface is in {name}. Write all text the user reads in {name}: answers, "
        'questions and options, the brief shown beside the chat, and the names, labels and descriptions of lists, '
        f'columns, plan stages and segments. Keep to {name} whatever the language of the website, the business '
        'profile, the market or the tool results. Only search filter values follow the search vendor and the target '
        "market, not the user's language: the inputs of a search tool, a segment's criteria and a profile's markets "
        'take English country names and industries, and job titles as LinkedIn profiles in the target market write '
        'them: in its language and in English where they use it. In the sentences the user reads (answers, '
        f'questions, the brief, profile and segment text), write country names and industries in {name}, never '
        'copied from a filter.'
    )
    for locale, name in LANGUAGES.items()
}


def _turn(user_id: str, session_id: object) -> Turn:
    job = {'id': str(uuid.uuid4()), 'organization_id': uuid.uuid4(), 'user_id': user_id, 'chat_session_id': session_id}
    return Turn(job, provider=None, emit=None)


@pytest.mark.parametrize('locale', LANGUAGES)
def test_the_chat_prompt_names_the_users_language(monkeypatch, locale):
    """The chat's answers and the text above a plan card come from this one system prompt."""
    monkeypatch.setattr(loop.memory_store, 'read', lambda _user: SimpleNamespace(body=''))
    turn = _turn(str(uuid.uuid4()), uuid.uuid4())
    turn.locale = locale
    system = turn._system()
    assert INSTRUCTION[locale] in system
    assert not any(text in system for other, text in INSTRUCTION.items() if other != locale)
    assert 'latest message' not in system


@pytest.mark.parametrize('locale', LANGUAGES)
def test_the_segments_prompt_names_the_users_language(monkeypatch, locale):
    llm = FakeLLM('{"segments": []}')
    monkeypatch.setattr(study, 'get_provider', lambda: llm)
    asyncio.run(study.propose_segments(Profile(company_name='Python Software Foundation'), locale))
    [system] = llm.systems
    assert INSTRUCTION[locale] in system
    assert not any(text in system for other, text in INSTRUCTION.items() if other != locale)
    assert 'language of the profile' not in llm.prompts[0]
    assert llm.prompts[0].endswith(language_reminder(locale))
    assert f'Write the text in {LANGUAGES[locale]}' in language_reminder(locale)


@needs_db
def test_the_chat_reads_the_users_locale(db):
    uid = _user(db)
    db.execute('UPDATE "user" SET locale = %s WHERE id = %s', ('en', uid))
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (%s, %s, %s) RETURNING id',
        (member(db, uid).org_id, project(db, uid), uid),
    ).fetchone()['id']
    act_in_own_org(db, uid)  # the turn's worker runs as the job's member
    turn = _turn(uid, sid)
    turn._load()
    assert INSTRUCTION['en'] in turn._system()
    assert FAILED_SEARCH_RULE in turn._system()


@needs_db
async def test_a_turn_speaks_the_language_its_request_names_and_saves_nothing(db):
    # The interface is Italian (its cookie), the saved language English (switched while signed out,
    # on another device, or a failed Settings write). #795: opening the app never writes "user".locale.
    uid = _user(db)
    db.execute('UPDATE "user" SET locale = %s WHERE id = %s', ('en', uid))
    _, jid = start_turn(member(db, uid), ChatRequest(message='Trova i responsabili acquisti', locale='it'))
    _, provider = await _run(jid, [('Ecco', [])])
    assert INSTRUCTION['it'] in provider.systems[0] and INSTRUCTION['en'] not in provider.systems[0]
    assert db.execute('SELECT locale FROM "user" WHERE id = %s', (uid,)).fetchone()['locale'] == 'en'


@needs_db
def test_segments_are_proposed_in_the_users_locale(client, db, monkeypatch):
    c, uid = client
    db.execute('UPDATE "user" SET locale = %s WHERE id = %s', ('en', uid))
    asked = []

    async def fake_propose(profile, locale):
        asked.append(locale)
        return [SEGMENT]

    monkeypatch.setattr(routes, 'propose_segments', fake_propose)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    assert c.post('/v1/onboarding/segments').status_code == 200
    assert asked == ['en']


class StreamProvider:
    """Plays one model step as the provider streams it."""

    def __init__(self, *events):
        self.events = events

    async def chat_stream(self, **kwargs):
        for event in self.events:
            yield event


def _step(*events) -> tuple[str, list, list]:
    shown = []
    turn = Turn(
        {
            'id': str(uuid.uuid4()),
            'organization_id': uuid.uuid4(),
            'user_id': uuid.uuid4(),
            'chat_session_id': uuid.uuid4(),
        },
        provider=StreamProvider(*events, LLMEvent('done')),
        emit=lambda name, data: shown.append(data['content']) if name == 'text' else None,
    )
    text, calls = asyncio.run(turn._call_model('system', [], []))
    return text, calls, shown


def test_the_announcement_before_a_tool_call_never_reaches_the_user():
    """The system prompt forbids it, yet DeepSeek writes it in English for an Italian user (#572)."""
    call = ToolCall('c1', 'propose_plan', {'mode': 'build'})
    text, calls, shown = _step(
        LLMEvent('text_delta', text="I'll plan this list "),
        LLMEvent('text_delta', text='before spending anything.'),
        LLMEvent('tool_start', tool_call=ToolCall('c1', 'propose_plan', {})),
        LLMEvent('tool_end', tool_call=call),
    )
    assert (text, calls, shown) == ('', [call], [])


def test_an_answer_without_tool_calls_still_streams():
    answer = 'Ho trovato 12 aziende. ' * 20
    text, calls, shown = _step(*[LLMEvent('text_delta', text=answer[i : i + 7]) for i in range(0, len(answer), 7)])
    assert text == ''.join(shown) == answer
    assert calls == [] and len(shown) > 1


def test_a_stop_during_the_hold_keeps_the_text_the_model_wrote():
    shown = []

    class Stalls:
        async def chat_stream(self, **kwargs):
            yield LLMEvent('text_delta', text='Cerco le agenzie')
            turn.cancelled.set()
            await asyncio.Event().wait()

    turn = Turn(
        {
            'id': str(uuid.uuid4()),
            'organization_id': uuid.uuid4(),
            'user_id': uuid.uuid4(),
            'chat_session_id': uuid.uuid4(),
        },
        provider=Stalls(),
        emit=lambda name, data: shown.append(data['content']) if name == 'text' else None,
    )
    with pytest.raises(JobCancelled):
        asyncio.run(turn._call_model('system', [], []))
    assert turn.answer.parts == shown == ['Cerco le agenzie']


@needs_db
@pytest.mark.parametrize(('locale', 'name'), [('es', 'Mi cliente')])
def test_the_first_project_is_named_in_the_users_language(db, locale, name):
    uid = _user(db)
    db.execute('UPDATE "user" SET locale = %s WHERE id = %s', (locale, uid))
    project_id = default_project(db, member(db, uid))
    assert db.execute('SELECT name FROM projects WHERE id = %s', (project_id,)).fetchone()['name'] == name
