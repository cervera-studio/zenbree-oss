"""First-access Home (#210): the pure decision, then the SQL facts and routes."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.core.locale import LOCALES
from app.core.segment_key import segment_key
from app.home.welcome import STEP_ORDER, Facts, decide
from app.llm.base import ToolCall
from app.main import app
from app.onboarding.study import Profile, first_list_brief, first_list_message
from tests.conftest import member, project, sender, sign_in
from tests.test_agent import DB_URL, _run, _user
from tests.test_agent import db as db
from tests.test_onboarding import SEGMENT

needs_db = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: welcome SQL tests NOT run')

PROFILE = {'company_name': 'Acme', 'summary': 'CRM per agenzie'}


def facts(**over) -> Facts:
    base = {
        'profile': None,
        'segments': None,
        'website': None,
        'description': None,
        'has_memory': False,
        'has_list': False,
        'has_mailbox': False,
    }
    return Facts(**(base | over))


def test_decide_mailbox_alone_does_not_close_the_screen():
    w = decide(facts(profile=PROFILE, has_mailbox=True), 'it')
    assert w is not None
    assert [(s.kind, s.done) for s in w.steps] == [('profile', True), ('list', False), ('mailbox', True)]


def test_decide_profile_done_by_profile_or_memory():
    assert decide(facts(profile=PROFILE), 'it').steps[0].done
    assert decide(facts(has_memory=True), 'it').steps[0].done


def test_decide_card_carries_key_message_titles_and_range():
    raw = SEGMENT.model_dump()
    for locale in LOCALES:
        w = decide(facts(profile=PROFILE, website='acme.it', segments=[raw]), locale)
        [card] = w.segments
        assert card.key == segment_key(raw)
        assert card.message == first_list_message(SEGMENT, locale)
        assert card.titles == ['Founder', 'CEO'] and card.employee_range == '11–50'
        assert w.knows_from == 'website' and w.company_name == 'Acme'


def test_decide_card_names_the_language_of_its_segment():
    italian, legacy = SEGMENT.model_dump() | {'locale': 'it'}, SEGMENT.model_dump() | {'locale': None}
    w = decide(facts(profile=PROFILE, segments=[italian, legacy]), 'en')
    assert [c.locale for c in w.segments] == ['it', None]
    # #657: a card's line is in its segment's language; a segment with none follows the page.
    assert [c.message for c in w.segments] == [
        'Crea la mia prima lista di contatti per il segmento "Agenzie".',
        'Build my first contact list for the "Agenzie" segment.',
    ]


def test_decide_caps_titles_and_drops_invalid_segments():
    seg = SEGMENT.model_dump() | {'criteria': {'titles': ['a', 'b', 'c'], 'employee_ranges': []}}
    w = decide(facts(profile=PROFILE, description='x', segments=[seg, {'name': 5}]), 'it')
    assert [c.titles for c in w.segments] == [['a', 'b']] and w.segments[0].employee_range is None
    assert w.knows_from == 'description'


# ── DB ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db):
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        yield TestClient(app), uid
    finally:
        app.dependency_overrides.clear()


def _profile(db, uid, completed=True, profile=None, segments=None):
    from psycopg.types.json import Jsonb

    db.execute(
        'INSERT INTO business_profiles (project_id, user_id, website, profile, segments, completed_at)'
        ' VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)',
        (
            project(db, uid),
            uid,
            'acme.it' if profile else None,
            Jsonb(profile) if profile else None,
            Jsonb(segments or []),
            completed,
        ),
    )


def _list(db, uid, rows=1):
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity)'
        " VALUES (default_organization(%s), %s, %s, 'L', 'person') RETURNING id",
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    for i in range(rows):
        db.execute("INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, '{}', %s)", (lid, f'k{i}'))


def _mailbox(db, uid, status='connected', provider='google'):
    sender(db, uid, provider, status=status)


def _get(c, locale='it'):
    r = c.get('/v1/home/welcome', params={'locale': locale})
    assert r.status_code == 200
    return r.json()


@needs_db
def test_old_account_without_row_gets_none(client):
    c, _ = client
    assert _get(c) is None


@needs_db
def test_not_completed_gets_none(client, db):
    c, uid = client
    _profile(db, uid, completed=False)
    assert _get(c) is None


@needs_db
def test_skip_user_gets_three_steps_and_no_segments(client, db):
    c, uid = client
    _profile(db, uid)
    body = _get(c)
    assert body['segments'] == [] and body['knows_from'] == 'none'
    assert [(s['kind'], s['done']) for s in body['steps']] == [(k, False) for k in STEP_ORDER]


@needs_db
def test_cards_match_the_onboarding_line_per_locale(client, db):
    c, uid = client
    _profile(db, uid, profile=PROFILE, segments=[SEGMENT.model_dump()])
    for locale in LOCALES:
        [card] = _get(c, locale)['segments']
        assert card['message'] == first_list_message(SEGMENT, locale) and 'brief' not in card
        assert card['employee_range'] == '11–50'


@needs_db
def test_a_card_opens_a_chat_that_shows_one_line_and_gives_the_agent_the_brief(client, db, monkeypatch):
    from app.agent import events as ev
    from app.agent import routes

    c, uid = client
    _profile(db, uid, profile=PROFILE, segments=[{'name': 5}, SEGMENT.model_dump()])
    [card] = _get(c, 'en')['segments']  # the stored segment that does not parse has no card
    queued: list[str] = []
    monkeypatch.setattr(routes, '_enqueue', queued.append)

    async def no_frames(job_id, after=0):
        return
        yield

    monkeypatch.setattr(ev, 'tail', no_frames)
    # a segment and a message of the client's own never come together
    assert c.post('/v1/chat', json={'message': 'find CTOs', 'segment': card['key']}).status_code == 422
    assert c.post('/v1/chat', json={'segment': card['key'], 'locale': 'en'}).status_code == 200
    [job_id] = queued
    sid = db.execute('SELECT chat_session_id FROM jobs WHERE id = %s', (job_id,)).fetchone()['chat_session_id']
    session = db.execute('SELECT title, brief FROM chat_sessions WHERE id = %s', (sid,)).fetchone()
    brief = first_list_brief(Profile.model_validate(PROFILE), SEGMENT, 'en')
    assert session == {'title': 'Agenzie', 'brief': brief}
    shown = db.execute('SELECT content FROM chat_messages WHERE session_id = %s', (sid,)).fetchone()['content']
    assert shown == card['message'] == 'Build my first contact list for the "Agenzie" segment.'

    _, provider = asyncio.run(_run(job_id, [('Searching.', [ToolCall('t1', 'fake_companies', {})]), ('Done.', [])]))
    first = provider.histories[0][0]['content']
    assert first.startswith(brief) and '- titles: Founder, CEO' in first

    db.execute("UPDATE jobs SET status = 'succeeded'")
    # a later turn cannot carry a segment: it would be dropped with its filters
    later = c.post('/v1/chat', json={'session_id': str(sid), 'segment': card['key']})
    assert later.status_code == 422 and len(queued) == 1
    # segments proposed again after the page loaded: the old key opens none of them, not the one now in its place
    renamed = SEGMENT.model_dump() | {'name': 'Agenzie creative'}
    db.execute('UPDATE business_profiles SET segments = %s', (Jsonb([renamed, {'name': 5}]),))
    for gone in (card['key'], segment_key({'name': 5})):  # a changed segment, and one that does not parse
        res = c.post('/v1/chat', json={'segment': gone})
        assert res.status_code == 422 and res.json()['detail']['code'] == 'CHAT.SEGMENT_GONE'
    assert len(queued) == 1


@needs_db
def test_profile_step_by_memory_but_not_whitespace(client, db):
    c, uid = client
    _profile(db, uid)
    db.execute("INSERT INTO user_memory_doc (user_id, body) VALUES (%s, '  \n ')", (uid,))
    assert _get(c)['steps'][0]['done'] is False
    db.execute("UPDATE user_memory_doc SET body = 'We sell CRM' WHERE user_id = %s", (uid,))
    assert _get(c)['steps'][0]['done'] is True


@needs_db
def test_list_step_counts_only_own_lists(client, db):
    c, uid = client
    _profile(db, uid)
    _list(db, _user(db))
    assert _get(c)['steps'][1]['done'] is False
    _list(db, uid)
    assert _get(c)['steps'][1]['done'] is True


@needs_db
def test_list_step_needs_a_row_not_the_empty_list_of_a_failed_search(client, db):
    c, uid = client
    _profile(db, uid)
    _list(db, uid, rows=0)
    assert _get(c)['steps'][1]['done'] is False
    _list(db, uid)
    assert _get(c)['steps'][1]['done'] is True


@needs_db
def test_mailbox_step_needs_connected_mail_provider(client, db):
    c, uid = client
    _profile(db, uid)
    _mailbox(db, uid, status='error')
    _mailbox(db, uid, provider='unipile')
    assert _get(c)['steps'][2]['done'] is False
    _mailbox(db, uid, provider='microsoft')
    assert _get(c)['steps'][2]['done'] is True


@needs_db
def test_profile_and_list_close_for_good(client, db):
    c, uid = client
    _profile(db, uid, profile=PROFILE)
    _list(db, uid)
    assert _get(c) is None
    assert db.execute('SELECT welcome_closed_at FROM business_profiles WHERE user_id = %s', (uid,)).fetchone()[
        'welcome_closed_at'
    ]
    db.execute('DELETE FROM lists WHERE user_id = %s', (uid,))
    assert _get(c) is None


@needs_db
def test_dismiss_is_idempotent_and_user_scoped(client, db):
    c, uid = client
    other = _user(db)
    _profile(db, uid)
    _profile(db, other)
    assert c.post('/v1/home/welcome/dismiss').status_code == 204
    first = db.execute('SELECT welcome_closed_at FROM business_profiles WHERE user_id = %s', (uid,)).fetchone()
    assert c.post('/v1/home/welcome/dismiss').status_code == 204
    again = db.execute('SELECT welcome_closed_at FROM business_profiles WHERE user_id = %s', (uid,)).fetchone()
    assert first['welcome_closed_at'] == again['welcome_closed_at'] and first['welcome_closed_at']
    assert _get(c) is None
    row = db.execute('SELECT welcome_closed_at FROM business_profiles WHERE user_id = %s', (other,)).fetchone()
    assert row['welcome_closed_at'] is None


@needs_db
def test_dismiss_without_row_is_a_noop(client):
    c, _ = client
    assert c.post('/v1/home/welcome/dismiss').status_code == 204
