"""Onboarding (E5): site reading, profile/segment shaping, the gate and the first-list brief."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.agent.language import language_instruction, language_reminder
from app.auth.middleware import CurrentUser
from app.core.db import get_conn
from app.core.locale import LOCALES
from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.exclusions.rules import profile_rule
from app.llm.base import LLMEvent, ProviderAPIError, ToolCall
from app.llm.json_output import check
from app.main import app
from app.onboarding import routes, study, study_stream, support
from app.onboarding.study import Criteria, Profile, Segment
from tests.conftest import act_in_own_org, member, project, sign_in
from tests.test_agent import DB_URL, _run, _user
from tests.test_agent import db as db

needs_db = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: onboarding SQL tests NOT run')

SEGMENT = Segment(
    name='Agenzie',
    who='Founder di agenzie digitali 11-50 dipendenti in Italia',
    problem='Pipeline irregolare',
    outcome='Più clienti',
    criteria=Criteria(titles=['Founder', 'CEO'], employee_ranges=['11,50'], locations=['Italy']),
)


class FakeLLM:
    def __init__(self, reply: str):
        self.reply, self.prompts, self.systems = reply, [], []

    async def chat(self, **kwargs):
        self.systems.append(kwargs['system'])
        self.prompts.append(kwargs['messages'][0]['content'])
        return type('R', (), {'text': self.reply})()

    async def chat_stream(self, **kwargs):
        self.systems.append(kwargs['system'])
        self.prompts.append(kwargs['messages'][0]['content'])
        for i in range(0, len(self.reply), 7):
            yield LLMEvent('text_delta', text=self.reply[i : i + 7])
        yield LLMEvent('done')


def run_study(website, description, locale='it') -> list[study.StudyEvent]:
    async def collect():
        return [e async for e in study.study_company(website, description, locale)]

    return asyncio.run(collect())


def fake_study(*events):
    async def fake(website, description, locale):
        for event in events:
            yield event

    return fake


def frames(res) -> list[tuple[str, dict]]:
    """The SSE body as (event, data) pairs."""
    out = []
    for block in res.text.split('\n\n'):
        if block:
            event, data = block.split('\n')
            out.append((event.removeprefix('event: '), json.loads(data.removeprefix('data: '))))
    return out


def test_extra_pages_stay_on_site_and_pick_about_services():
    md = (
        '[Chi siamo](/chi-siamo) [Blog](/blog/x) [Servizi](https://www.acme.it/servizi#top) '
        '[About](https://evil.test/about) [Again](/chi-siamo) [Products](/products)'
    )
    assert study._extra_pages('https://acme.it/', md) == ['https://acme.it/chi-siamo', 'https://www.acme.it/servizi']


def test_study_without_readable_site_or_description_fails(monkeypatch):
    monkeypatch.setattr(study, 'fetch_page', lambda url: {'success': False, 'error': 'blocked', 'url': url})
    with pytest.raises(study.SiteUnreadableError):
        run_study('http://127.0.0.1', None)


def test_study_falls_back_to_description_and_clips_reply(monkeypatch):
    monkeypatch.setattr(study, 'fetch_page', lambda url: {'success': False, 'error': 'blocked', 'url': url})
    llm = FakeLLM('{"company_name": "Acme", "personas": ["' + 'x' * 400 + '"], "markets": ["Italy"], "proof": 3}')
    monkeypatch.setattr(study, 'get_provider', lambda: llm)
    *_, done = run_study('acme.it', 'Vendiamo software alle agenzie')
    assert isinstance(done, study.Studied) and done.pages == []
    assert 'Vendiamo software alle agenzie' in llm.prompts[0]
    profile = done.profile
    assert profile.company_name == 'Acme' and profile.personas[0] == 'x' * 279 + '…' and profile.proof == ''


def test_a_long_profile_line_is_cut_between_words():
    """Demo QA settings-6: personas were cut mid-word ("dal nome dello stud") and saved that way."""
    persona = 'Titolare di uno studio di commercialisti con 5-50 dipendenti in Italia, ' * 5
    srl = 'Titolari di aziende manifatturiere organizzate come S.r.l. con dieci-cinquanta dipendenti in Lombardia. ' * 4
    filters = 'Italia ' * 50
    clipped = study._clip({'personas': [persona, srl], 'markets': [filters]}, study.Profile)
    cut, cut_srl = clipped['personas']
    assert len(cut) <= 280 and cut.endswith('…') and persona.startswith(cut[:-1].rstrip(',')) and cut[-2] != ' '
    assert persona[len(cut[:-1].rstrip(','))] in ' ,'
    assert len(cut_srl) >= 270 and cut_srl.endswith('…')
    assert clipped['markets'] == [filters[:280].strip()]


def test_study_accepts_list_for_text_fields(monkeypatch):
    # Prod 502: DeepSeek answered "proof" as a list and the router's schema check rejected the reply.
    reply = {'company_name': 'Acme', 'proof': ['Client A', ' ', 'Case B'], 'personas': ['CTO']}
    check(reply, study._PROFILE_SCHEMA | {'required': []})
    monkeypatch.setattr(study, 'get_provider', lambda: FakeLLM(json.dumps(reply)))
    *_, done = run_study(None, 'Vendiamo software')
    assert isinstance(done, study.Studied)
    profile = done.profile
    assert profile.proof == 'Client A; Case B' and profile.personas == ['CTO']


@pytest.mark.parametrize('locale', LOCALES)
def test_the_profile_is_written_in_the_users_language(monkeypatch, locale):
    llm = FakeLLM('{"company_name": "Acme"}')
    monkeypatch.setattr(study, 'get_provider', lambda: llm)
    run_study(None, 'We sell anchors', locale)
    assert language_instruction(locale) in llm.systems[0]
    # Also last in the prompt: a source in another language otherwise wins (#699).
    assert llm.prompts[0].endswith(language_reminder(locale))
    # The reminder comes last, so it must not override the rule that filters keep the vendors' form (#699).
    assert 'English country names and industries' in language_reminder(locale)


def test_segments_drop_nameless_and_keep_criteria(monkeypatch):
    llm = FakeLLM(
        '{"segments": [{"name": "A", "who": "CEO", "problem": "p", "outcome": "o",'
        ' "criteria": {"titles": ["CEO"], "bogus": ["x"]}}, {"name": "", "who": "x"}]}'
    )
    monkeypatch.setattr(study, 'get_provider', lambda: llm)
    [only] = asyncio.run(study.propose_segments(Profile(company_name='Acme'), 'it'))
    assert only.criteria.titles == ['CEO']


def test_segments_take_one_filter_value_as_a_string(monkeypatch):
    # #572: with the English instruction DeepSeek wrote "employee_ranges": "200,1000" in 6 of 11 runs, and the
    # router's local schema check rejected the whole reply: the user saw a 502 instead of segments.
    reply = {
        'segments': [
            {'name': 'A', 'who': 'CTO', 'problem': 'p', 'outcome': 'o', 'criteria': {'employee_ranges': '200,1000'}}
        ]
    }
    check(reply, study._SEGMENTS_SCHEMA)
    monkeypatch.setattr(study, 'get_provider', lambda: FakeLLM(json.dumps(reply)))
    [only] = asyncio.run(study.propose_segments(Profile(company_name='Acme'), 'en'))
    assert only.criteria.employee_ranges == ['200,1000']


def test_segments_record_their_language_and_ask_for_a_translated_name(monkeypatch):
    # #583: 3 of 27 Italian names stayed English ("Scientific computing Germany"). #587: the stored segments must
    # tell the page which language they were made in.
    reply = {'segments': [{'name': 'Calcolo scientifico Germania', 'who': 'CTO', 'problem': 'p', 'outcome': 'o'}]}
    llm = FakeLLM(json.dumps(reply))
    monkeypatch.setattr(study, 'get_provider', lambda: llm)
    [only] = asyncio.run(study.propose_segments(Profile(company_name='Acme'), 'it'))
    assert only.locale == 'it'
    assert 'translate every word except a brand or product name' in llm.prompts[0]


@pytest.mark.parametrize(
    ('sent', 'hint'),
    [
        ({'signal_hint': 'annunci di lavoro aperti'}, 'annunci di lavoro aperti'),
        ({'signal_hint': ['nuovo ufficio', 'round di finanziamento']}, 'nuovo ufficio; round di finanziamento'),
        ({'signal_hint': None}, ''),
    ],
)
def test_segments_carry_the_signal_hint_or_none(monkeypatch, sent, hint):
    reply = {'segments': [{'name': 'A', 'who': 'CTO', 'problem': 'p', 'outcome': 'o', 'criteria': {}, **sent}]}
    check(reply, study._SEGMENTS_SCHEMA)
    monkeypatch.setattr(study, 'get_provider', lambda: FakeLLM(json.dumps(reply)))
    [only] = asyncio.run(study.propose_segments(Profile(company_name='Acme'), 'it'))
    assert only.signal_hint == hint


def test_first_list_brief_carries_filters_and_exclusions():
    profile = Profile(company_name='Acme', summary='CRM per agenzie', exclusions=['Clienti attuali'])
    brief = study.first_list_brief(profile, SEGMENT, 'it')
    assert '"Agenzie"' in brief and 'Acme — CRM per agenzie' in brief
    assert '- ruoli: Founder, CEO' in brief and '- dipendenti: 11,50' in brief and '- paesi: Italy' in brief
    assert 'Da escludere: Clienti attuali' in brief and 'settori' not in brief
    assert study.first_list_brief(profile, SEGMENT, 'en').startswith('Build my first contact list')


def test_first_list_brief_leaves_out_the_signal_hint_so_the_first_turn_searches_nothing_for_it():
    hinted = SEGMENT.model_copy(update={'signal_hint': 'annunci di lavoro aperti'})
    assert study.first_list_brief(Profile(), hinted, 'it') == study.first_list_brief(Profile(), SEGMENT, 'it')


def test_first_list_brief_is_written_in_the_language_of_its_segment():
    # #657: Italian segments under English headers made the first chat message mix two languages.
    italian = SEGMENT.model_copy(update={'locale': 'it'})
    assert study.first_list_brief(Profile(), italian, 'en') == (
        'Crea la mia prima lista di contatti per il segmento "Agenzie".\n'
        'Chi cerco: Founder di agenzie digitali 11-50 dipendenti in Italia\n'
        'Il loro problema: Pipeline irregolare\n'
        'Cosa ottengono: Più clienti\n'
        'Filtri di ricerca persone:\n'
        '- ruoli: Founder, CEO\n'
        '- dipendenti: 11,50\n'
        '- paesi: Italy'
    )


def test_memory_entry_is_one_clipped_line():
    assert study.memory_entry(Profile()) == ''
    entry = study.memory_entry(
        Profile(company_name='Acme', summary='CRM per agenzie.', personas=['CEO', 'CTO'], exclusions=['Clienti'])
    )
    assert (
        entry == 'Business profile (from onboarding): Acme — CRM per agenzie. Buyers: CEO; CTO. Never contact: Clienti'
    )
    long = study.memory_entry(Profile(summary='x' * 1200, solution='y' * 1200))
    assert len(long) == 2000 and long.endswith('…')


def test_profile_prompt_is_escaped_data_and_empty_without_a_profile():
    block = study.profile_prompt({'company_name': 'Acme <b>', 'personas': ['CEO']})
    assert block.startswith('# The client this chat works for') and 'DATA' in block
    assert '<client>\nBusiness profile (from onboarding): Acme &lt;b&gt;. Buyers: CEO\n</client>' in block
    assert study.profile_prompt(None) == study.profile_prompt({}) == study.profile_prompt({'personas': 'x'}) == ''


def test_profile_rule_keeps_descriptions_out():
    assert profile_rule('HubSpot') == ('company_name', 'hubspot')
    assert profile_rule('Banca di Roma') == ('company_name', 'banca di roma')
    assert profile_rule('https://www.Rival.com/about') == ('domain', 'rival.com')
    assert profile_rule('ceo@rival.com') == ('email', 'ceo@rival.com')
    assert profile_rule('linkedin.com/company/rival') == ('linkedin_url', 'linkedin.com/company/rival')
    for line in ('Competitors', 'Clienti attuali', 'Current customers', 'Competitors: HubSpot, Pipedrive', ''):
        assert profile_rule(line) is None, line


# ── DB ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db):
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        yield TestClient(app), uid
    finally:
        app.dependency_overrides.clear()


@needs_db
def test_gate_new_user_needs_onboarding_until_skip(client):
    c, _ = client
    assert c.get('/v1/onboarding').json()['needs_onboarding'] is True
    assert c.post('/v1/onboarding/complete', json={}).json() == {'session_id': None}
    assert c.get('/v1/onboarding').json()['needs_onboarding'] is False


@needs_db
def test_gate_lets_existing_users_with_chats_through(client, db):
    c, uid = client
    db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (default_organization(%s), %s, %s)',
        (
            uid,
            project(db, uid),
            uid,
        ),
    )
    assert c.get('/v1/onboarding').json()['needs_onboarding'] is False


@needs_db
def test_profile_edit_is_per_project(client, db):
    """#307: the profile is the active project's. A new project of the same organization needs its own
    onboarding; another organization never sees it."""
    c, uid = client
    assert c.put('/v1/onboarding/profile', json={'company_name': 'Acme', 'personas': ['CEO']}).status_code == 200
    assert c.get('/v1/onboarding').json()['profile']['company_name'] == 'Acme'
    second = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Client B') RETURNING id::text",
        (member(db, uid).org_id, uid),
    ).fetchone()['id']
    b = c.get('/v1/onboarding', headers={'X-Project-Id': second}).json()
    assert b['profile'] is None and b['needs_onboarding'] is True
    assert (
        c.put('/v1/onboarding/profile', json={'company_name': 'Beta'}, headers={'X-Project-Id': second}).status_code
        == 200
    )
    assert c.get('/v1/onboarding').json()['profile']['company_name'] == 'Acme'  # the default project kept its own
    other = _user(db)
    sign_in(app, member(db, other))
    assert c.get('/v1/onboarding', headers={'X-Project-Id': second}).json()['profile'] is None  # not their project
    assert c.post('/v1/onboarding/segments').json()['detail']['code'] == 'ONBOARDING.NO_PROFILE'


@needs_db
@pytest.mark.parametrize(
    ('locale', 'placeholder'), [('en', 'My client'), ('it', 'Il mio cliente'), ('fr', 'Mon client')]
)
def test_profile_names_the_default_project_once(client, db, locale, placeholder):
    c, uid = client
    db.execute('UPDATE "user" SET locale = %s WHERE id = %s', (locale, uid))
    assert c.get('/v1/onboarding').status_code == 200  # creates the default project, named in the user's language
    pid = db.execute('SELECT id FROM projects WHERE user_id = %s', (uid,)).fetchone()['id']

    def name():
        return db.execute('SELECT name FROM projects WHERE id = %s', (pid,)).fetchone()['name']

    assert name() == placeholder
    assert c.put('/v1/onboarding/profile', json={'company_name': 'Fatture in Cloud'}).status_code == 200
    assert name() == 'Fatture in Cloud'
    db.execute("UPDATE projects SET name = 'Chosen' WHERE id = %s", (pid,))
    assert c.put('/v1/onboarding/profile', json={'company_name': 'Other'}).status_code == 200
    assert name() == 'Chosen'


@needs_db
def test_profile_keeps_a_name_the_user_typed_like_the_placeholder(client, db):
    c, uid = client
    assert c.get('/v1/onboarding').status_code == 200
    pid = db.execute('SELECT id FROM projects WHERE user_id = %s', (uid,)).fetchone()['id']
    assert c.patch(f'/v1/projects/{pid}', json={'name': 'Il mio cliente'}).status_code == 200
    assert c.put('/v1/onboarding/profile', json={'company_name': 'Other'}).status_code == 200
    assert db.execute('SELECT name FROM projects WHERE id = %s', (pid,)).fetchone()['name'] == 'Il mio cliente'


@needs_db
def test_study_requires_a_source_and_free_runs_are_capped(client, db, monkeypatch):
    c, uid = client
    assert c.post('/v1/onboarding/profile', json={'website': ' '}).status_code == 422

    monkeypatch.setattr(study_stream, 'study_company', fake_study(study.Studied(Profile(company_name='Acme'), [])))
    for _ in range(support.FREE_RUNS):
        assert c.post('/v1/onboarding/profile', json={'description': 'agency CRM'}).status_code == 200
    res = c.post('/v1/onboarding/profile', json={'description': 'agency CRM'})
    assert res.status_code == 429 and res.json()['detail']['code'] == 'ONBOARDING.LIMIT'
    used = db.execute('SELECT onboarding_free_used FROM user_credits WHERE user_id = %s', (uid,)).fetchone()
    assert used['onboarding_free_used'] == support.FREE_RUNS


@needs_db
def test_complete_with_segment_starts_first_list_turn(client, db, monkeypatch):
    c, _ = client
    queued = []
    monkeypatch.setattr(routes, '_enqueue', queued.append)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    res = c.post('/v1/onboarding/complete', json={'segment': SEGMENT.model_dump(), 'locale': 'en'})
    sid = res.json()['session_id']
    msg = db.execute('SELECT content, job_id FROM chat_messages WHERE session_id = %s', (sid,)).fetchone()
    # The user's bubble and the chat title stay short and readable, with no raw filter codes.
    assert msg['content'] == 'Build my first contact list for the "Agenzie" segment.'
    assert db.execute('SELECT title FROM chat_sessions WHERE id = %s', (sid,)).fetchone()['title'] == 'Agenzie'
    assert queued == [str(msg['job_id'])]
    assert c.get('/v1/onboarding').json()['needs_onboarding'] is False
    search = [ToolCall('t1', 'fake_companies', {})]
    _, provider = asyncio.run(_run(str(msg['job_id']), [('Searching.', search), ('Found 2.', [])]))
    first = provider.histories[0][0]['content']  # the agent still reads the whole brief, filters included
    assert first.startswith(study.first_list_brief(Profile(company_name='Acme'), SEGMENT, 'en'))
    assert '- titles: Founder, CEO' in first and '- employees: 11,50' in first
    # named after what the user asked, not after the brief's last filter line
    named = db.execute('SELECT name FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()['name']
    assert named == 'Build my first contact list for the "Agenzie" segment.'


@needs_db
def test_complete_the_queue_refuses_keeps_no_chat_and_no_completion(client, db, monkeypatch):
    from app import worker

    c, uid = client

    def down(job_id):
        raise ConnectionError('broker down')

    monkeypatch.setattr(worker.run_job, 'delay', down)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    res = c.post('/v1/onboarding/complete', json={'segment': SEGMENT.model_dump(), 'locale': 'en'})
    assert (res.status_code, res.json()['detail']['code']) == (503, 'CHAT.QUEUE_UNAVAILABLE')
    # the first list never ran: no chat is left of it, and onboarding asks again
    assert db.execute('SELECT 1 FROM chat_sessions WHERE user_id = %s', (uid,)).fetchall() == []
    assert c.get('/v1/onboarding').json()['needs_onboarding'] is True


@needs_db
def test_complete_with_an_unnamed_segment_names_the_chat_after_its_message(client, db, monkeypatch):
    c, _ = client
    monkeypatch.setattr(routes, '_enqueue', lambda job_id: None)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    res = c.post('/v1/onboarding/complete', json={'segment': SEGMENT.model_dump() | {'name': ''}, 'locale': 'en'})
    assert res.status_code == 200
    title = db.execute('SELECT title FROM chat_sessions WHERE id = %s', (res.json()['session_id'],)).fetchone()
    assert title['title'] == 'Build my first contact list for the "" segment.'


@needs_db
def test_segments_proposes_from_the_saved_profile(client, monkeypatch):
    c, _ = client
    assert c.post('/v1/onboarding/segments').json()['detail']['code'] == 'ONBOARDING.NO_PROFILE'

    async def fake_propose(profile, locale):
        return [SEGMENT]

    monkeypatch.setattr(routes, 'propose_segments', fake_propose)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    res = c.post('/v1/onboarding/segments')
    assert res.json() == {'segments': [SEGMENT.model_dump()]}
    assert c.get('/v1/onboarding').json()['segments'] == [SEGMENT.model_dump()]


@needs_db
def test_segments_stored_before_the_signal_hint_come_back_without_one(client, db):
    c, uid = client
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    old = SEGMENT.model_dump(exclude={'signal_hint', 'locale'})
    db.execute('UPDATE business_profiles SET segments = %s WHERE project_id = %s', (Jsonb([old]), project(db, uid)))
    [segment] = c.get('/v1/onboarding').json()['segments']
    assert segment['signal_hint'] == ''


def _saved_locale(db, uid: str) -> str:
    return db.execute('SELECT locale FROM "user" WHERE id = %s', (uid,)).fetchone()['locale']


@needs_db
def test_segments_follow_the_language_the_user_sees_and_save_nothing(client, db, monkeypatch):
    # #583: a user who switched language on the auth pages reaches onboarding before anything wrote "user".locale.
    c, uid = client
    asked = []

    async def fake_propose(profile, locale):
        asked.append(locale)
        return [SEGMENT]

    monkeypatch.setattr(routes, 'propose_segments', fake_propose)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    assert _saved_locale(db, uid) == 'en'
    assert c.post('/v1/onboarding/segments', json={'locale': 'it'}).status_code == 200
    assert asked == ['it'] and _saved_locale(db, uid) == 'en'  # #795: the call's language is not saved
    assert c.post('/v1/onboarding/segments').status_code == 200  # no locale: the saved one
    assert asked == ['it', 'en']


@needs_db
def test_completing_onboarding_leaves_the_saved_language_alone(client, db, monkeypatch):
    # #795: only an explicit choice writes "user".locale; the first turn writes in the call's language (jobs.locale).
    c, uid = client
    monkeypatch.setattr(routes, '_enqueue', lambda job_id: None)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    seen = []
    real_start_turn = routes._start_turn

    def start_turn(user, req, **opening):
        seen.append((_saved_locale(db, uid), req.message.splitlines()[0]))
        return real_start_turn(user, req, **opening)

    monkeypatch.setattr(routes, '_start_turn', start_turn)
    italian = SEGMENT.model_dump() | {'locale': 'it'}
    assert c.post('/v1/onboarding/complete', json={'segment': italian, 'locale': 'it'}).status_code == 200
    # #657: the brief stays in the segment's language; the saved language (English for a new user) is untouched.
    assert seen == [('en', 'Crea la mia prima lista di contatti per il segmento "Agenzie".')]
    assert db.execute('SELECT locale FROM jobs WHERE user_id = %s', (uid,)).fetchone()['locale'] == 'it'


@needs_db
def test_failed_runs_do_not_use_free_runs(client, db, monkeypatch):
    c, uid = client

    async def unreadable(website, description, locale):
        raise study.SiteUnreadableError
        yield

    async def llm_down(profile, locale):
        raise ProviderAPIError('provider down')

    monkeypatch.setattr(study_stream, 'study_company', unreadable)
    monkeypatch.setattr(routes, 'propose_segments', llm_down)
    res = c.post('/v1/onboarding/profile', json={'website': 'acme.it'})
    assert frames(res) == [
        (
            'error',
            {'code': 'ONBOARDING.SITE_UNREADABLE', 'message': 'The website could not be read; describe the business'},
        )
    ]
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    assert c.post('/v1/onboarding/segments').status_code == 502
    used = db.execute('SELECT onboarding_free_used FROM user_credits WHERE user_id = %s', (uid,)).fetchone()
    assert used['onboarding_free_used'] == 0


@needs_db
def test_a_billed_segments_reply_that_fails_keeps_the_free_run_used(client, db, monkeypatch):
    c, uid = client

    async def garbled(profile, locale):
        raise ValueError('reply did not parse')

    monkeypatch.setattr(routes, 'propose_segments', garbled)
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    assert c.post('/v1/onboarding/segments').status_code == 502
    used = db.execute('SELECT onboarding_free_used FROM user_credits WHERE user_id = %s', (uid,)).fetchone()
    assert used['onboarding_free_used'] == 1


def _rules(db, uid: str) -> set[tuple[str, str, str | None]]:
    rows = db.execute(
        'SELECT kind, value, source FROM exclusions WHERE project_id = %s', (project(db, uid),)
    ).fetchall()
    return {(r['kind'], r['value'], r['source']) for r in rows}


@needs_db
def test_profile_exclusions_sync_only_onboarding_rules(client, db):
    c, uid = client
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'mine.com')",
        (project(db, uid), uid),
    )
    put = lambda name, lines: c.put('/v1/onboarding/profile', json={'company_name': name, 'exclusions': lines})  # noqa: E731 reason: short test helper
    put('Acme', ['HubSpot', 'mine.com', 'Competitors'])
    put('Acme', ['HubSpot', 'mine.com', 'Competitors'])  # re-save: nothing doubles
    assert _rules(db, uid) == {('company_name', 'hubspot', 'onboarding'), ('domain', 'mine.com', None)}
    put('Acme', ['Competitors'])
    assert _rules(db, uid) == {('domain', 'mine.com', None)}


@needs_db
def test_a_deleted_onboarding_rule_stays_deleted_on_unrelated_saves(client, db):
    c, uid = client
    lines = ['HubSpot', 'rival.com']
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme', 'exclusions': lines})
    db.execute("DELETE FROM exclusions WHERE user_id = %s AND value = 'hubspot'", (uid,))
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme', 'exclusions': lines})
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme Srl', 'exclusions': lines})  # Settings rename
    assert _rules(db, uid) == {('domain', 'rival.com', 'onboarding')}


@needs_db
def test_user_and_opt_out_inserts_claim_onboarding_rules(client, db):
    from app.outreach.coordination import Person, Signal, SignalKind, apply

    c, uid = client
    sign_in(app, member(db, uid))
    c.put('/v1/onboarding/profile', json={'exclusions': ['rival.com', 'ceo@rival.com']})
    origins = lambda: {e['value']: e['origin'] for e in c.get('/v1/exclusions').json()['exclusions']}  # noqa: E731 reason: short test helper
    assert origins() == {'rival.com': 'profile', 'ceo@rival.com': 'profile'}
    assert c.post('/v1/exclusions', json={'items': [{'value': 'rival.com'}]}).json()['added'] == 0
    with system(SystemReason.OPT_OUT), get_conn() as conn:  # as replies.record files an opt-out
        opt_out = Signal(
            SignalKind.OPT_OUT,
            Person(None, email='ceo@rival.com'),
            'test',
            datetime.now(UTC),
            exclude=True,
            fallback_projects=[project(db, uid)],
        )
        assert apply(conn, opt_out).excluded == []
    assert origins() == {'rival.com': 'manual', 'ceo@rival.com': 'unsubscribed'}
    c.put('/v1/onboarding/profile', json={'exclusions': []})
    assert _rules(db, uid) == {('domain', 'rival.com', None), ('email', 'ceo@rival.com', None)}


@needs_db
def test_the_agent_reads_the_profile_of_its_chats_project(client, db):
    """#307: a chat's turn carries its project's profile (the client), never another project's or the user's."""
    from app.agent.loop import Turn

    c, uid = client
    second = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Client B') RETURNING id::text",
        (member(db, uid).org_id, uid),
    ).fetchone()['id']
    c.put('/v1/onboarding/profile', json={'company_name': 'Acme'})
    c.put(
        '/v1/onboarding/profile', json={'company_name': 'Beta', 'markets': ['Spain']}, headers={'X-Project-Id': second}
    )
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (%s, %s, %s) RETURNING id',
        (member(db, uid).org_id, second, uid),
    ).fetchone()['id']
    job = {'id': str(uuid.uuid4()), 'organization_id': member(db, uid).org_id, 'user_id': uid, 'chat_session_id': sid}
    act_in_own_org(db, uid)  # the turn's worker runs as the job's member
    turn = Turn(job, provider=None, emit=None)
    turn._load()
    system = turn._system()
    assert turn.project_id == second
    assert 'Business profile (from onboarding): Beta. Markets: Spain' in system and 'Acme' not in system


@needs_db
def test_free_runs_are_counted_per_organization_not_per_client(client, db, monkeypatch):
    """#307: a new client is no fresh batch of free runs; the organization's wallet counts them all."""
    c, uid = client
    monkeypatch.setattr(study_stream, 'study_company', fake_study(study.Studied(Profile(company_name='Acme'), [])))
    second = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Client B') RETURNING id::text",
        (member(db, uid).org_id, uid),
    ).fetchone()['id']
    for n in range(support.FREE_RUNS):
        headers = {'X-Project-Id': second} if n % 2 else {}
        assert c.post('/v1/onboarding/profile', json={'description': 'x'}, headers=headers).status_code == 200
    res = c.post('/v1/onboarding/profile', json={'description': 'x'}, headers={'X-Project-Id': second})
    assert res.status_code == 429 and res.json()['detail']['code'] == 'ONBOARDING.LIMIT'


@needs_db
def test_a_team_the_user_creates_shares_their_free_runs(client, db, monkeypatch):
    """#676: a created team (slug `<founder id>/<uuid>`) counts on its founder's own wallet, or each is ten more.
    Through the real membership read: row-level security hides that wallet from the team's scope, so the count acts
    for the wallet's organization (support.use_free_run)."""
    c, uid = client
    monkeypatch.setattr(study_stream, 'study_company', fake_study(study.Studied(Profile(company_name='Acme'), [])))
    team = db.execute(
        'INSERT INTO organization (name, slug, "createdAt") VALUES (\'Agenzia\', %s, now()) RETURNING id::text',
        (f'{uid}/{uuid.uuid4()}',),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (team, uid, 'admin'),
    )
    db.execute('INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (%s, %s, 0)', (team, uid))
    own = member(db, uid)
    in_team = CurrentUser(id=uid, org_id=team)
    for n in range(support.FREE_RUNS):
        sign_in(app, in_team if n % 2 else own)
        assert c.post('/v1/onboarding/profile', json={'description': 'x'}).status_code == 200
    sign_in(app, in_team)
    res = c.post('/v1/onboarding/profile', json={'description': 'x'})
    assert res.status_code == 429 and res.json()['detail']['code'] == 'ONBOARDING.LIMIT'


@needs_db
def test_a_teammate_who_is_not_in_the_founders_organization_counts_on_the_founders_wallet(client, db, monkeypatch):
    """#676: an invited teammate is a member of the team only, not of the founder's own organization, where the
    wallet is: the count acts for that organization with no person. Catches Member(wallet, caller), which refuses
    the teammate (401)."""
    c, founder = client
    monkeypatch.setattr(study_stream, 'study_company', fake_study(study.Studied(Profile(company_name='Acme'), [])))
    team = db.execute(
        'INSERT INTO organization (name, slug, "createdAt") VALUES (\'Agenzia\', %s, now()) RETURNING id::text',
        (f'{founder}/{uuid.uuid4()}',),
    ).fetchone()['id']
    mate = _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (team, mate, 'member'),
    )
    db.execute(
        'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (%s, %s, 0)', (team, mate)
    )
    sign_in(app, CurrentUser(id=mate, org_id=team))
    res = c.post('/v1/onboarding/profile', json={'description': 'x'})
    assert res.status_code == 200, res.text
    used = db.execute(
        'SELECT onboarding_free_used AS n FROM user_credits WHERE organization_id = %s', (member(db, founder).org_id,)
    ).fetchone()['n']
    assert used == 1


@needs_db
def test_the_website_domain_is_excluded_until_the_website_changes(client, db):
    from app.onboarding.own_domain import sync_own_domain

    c, uid = client
    sign_in(app, member(db, uid))
    pid = project(db, uid)
    origins = lambda: {e['value']: e['origin'] for e in c.get('/v1/exclusions').json()['exclusions']}  # noqa: E731 reason: short test helper
    act_in_own_org(db, uid)  # the profile route that calls it
    sync_own_domain(pid, uid, None, 'https://www.acme.it')
    sync_own_domain(pid, uid, None, 'https://www.acme.it')  # idempotent
    assert origins() == {'acme.it': 'own_domain'}
    rule = c.get('/v1/exclusions').json()['exclusions'][0]
    c.delete(f'/v1/exclusions/{rule["id"]}')
    sync_own_domain(pid, uid, 'https://www.acme.it', 'https://www.acme.it')  # unchanged: stays deleted
    assert origins() == {}
    sync_own_domain(pid, uid, 'https://www.acme.it', 'https://beta.it')
    sync_own_domain(pid, uid, 'https://beta.it', 'https://gamma.it')  # the old domain goes
    assert origins() == {'gamma.it': 'own_domain'}
    c.post('/v1/exclusions', json={'items': [{'value': 'gamma.it'}]})  # a manual add claims it
    sync_own_domain(pid, uid, 'https://gamma.it', 'https://delta.it')
    assert origins() == {'gamma.it': 'manual', 'delta.it': 'own_domain'}
    c.put('/v1/onboarding/profile', json={'exclusions': ['epsilon.it']})  # a profile line holding the domain
    sync_own_domain(pid, uid, 'https://delta.it', 'https://epsilon.it')
    assert origins()['epsilon.it'] == 'own_domain'
    c.put('/v1/onboarding/profile', json={'exclusions': []})  # dropping the line keeps the own domain
    assert origins()['epsilon.it'] == 'own_domain'
