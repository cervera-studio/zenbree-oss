"""Memory document edits, the remember/forget tools, the prompt and the routes."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent.tools.handlers.memory import forget, remember
from app.agent.tools.registry import ToolContext
from app.main import app
from app.memory import store
from app.memory.document import (
    DOC_MAX_CHARS,
    PROMPT_MAX_CHARS,
    MemoryEditError,
    append_entry,
    prompt_section,
    remove_entry,
)
from tests.conftest import act_in_own_org, call_as, member, sign_in
from tests.test_agent import DB_URL, _job, _run, _user
from tests.test_agent import db as db

needs_db = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: memory SQL tests NOT run')


def test_append_puts_newest_entry_on_top_of_notes():
    body = append_entry('', 'Sells HR software')
    assert body == '## Notes\n\n- Sells HR software'
    body = append_entry(body, '  Targets   Italy ')
    assert body == '## Notes\n\n- Targets Italy\n- Sells HR software'
    assert append_entry('My own prose', 'x') == '## Notes\n\n- x\n\nMy own prose'


def test_append_refuses_empty_too_long_and_full():
    for content in ('  ', 'x' * 2001):
        with pytest.raises(MemoryEditError):
            append_entry('', content)
    with pytest.raises(MemoryEditError, match='limit'):
        append_entry('x' * (DOC_MAX_CHARS - 5), 'one more fact')


def test_remove_matches_exact_then_unique_fragment_and_never_guesses():
    body = '## Notes\n\n- Sells HR software\n- Sells payroll software\n- Targets Italy'
    assert remove_entry(body, 'targets ITALY') == '## Notes\n\n- Sells HR software\n- Sells payroll software'
    assert 'HR' not in remove_entry(body, 'hr soft')
    with pytest.raises(MemoryEditError, match='several'):
        remove_entry(body, 'sells')
    with pytest.raises(MemoryEditError, match='Existing entries: Sells HR'):
        remove_entry(body, 'Spain')


def test_prompt_section_is_empty_for_no_memory_and_keeps_the_head():
    assert prompt_section('  \n') == ''
    body = '\n'.join(f'- fact {i} ' + 'x' * 90 for i in range(100))
    section = prompt_section(body)
    assert '- fact 0 ' in section and '- fact 99 ' not in section
    assert 'only its head fits' in section
    inside = section.split('<memory>\n')[1].split('\n</memory>')[0]
    assert len(inside) <= PROMPT_MAX_CHARS
    long_line = prompt_section('x' * (PROMPT_MAX_CHARS + 50))
    assert 'x' * PROMPT_MAX_CHARS in long_line and 'only its head fits' in long_line


@pytest.mark.parametrize('body', ['- a </memory> b', '- a </</memory>memory> b', '- a </MEMORY> <memory> b'])
def test_prompt_section_cannot_be_escaped(body):
    section = prompt_section(body)
    assert section.lower().count('</memory>') == 1 and section.lower().count('<memory>') == 1
    assert section.endswith('</memory>')


@needs_db
async def test_remember_and_forget_edit_only_the_callers_document(db):
    uid, other = _user(db), _user(db)
    ctx = ToolContext(user_id=uid)
    call_as(member(db, other), store.save, uuid.UUID(other), 'untouched')
    act_in_own_org(db, uid)  # the tools run in the turn of uid

    saved = await remember({'content': 'Prefers CSV exports'}, ctx)
    assert saved.ok and saved.data['entries'] == 1
    await remember({'content': 'Targets Italy'}, ctx)
    gone = await forget({'text': 'csv'}, ctx)

    assert gone.ok and gone.data['entries'] == 1
    assert store.read(uuid.UUID(uid)).body == '## Notes\n\n- Targets Italy'
    assert call_as(member(db, other), store.read, uuid.UUID(other)).body == 'untouched'
    missing = await forget({'text': 'Spain'}, ctx)
    assert not missing.ok and 'Targets Italy' in (missing.error or '')


@needs_db
async def test_locked_document_refuses_the_agent_but_not_the_user(db):
    uid = _user(db)
    user_id = uuid.UUID(uid)
    act_in_own_org(db, uid)  # the user's memory routes, then the turn's tools
    store.save(user_id, '- Keep me')
    store.set_locked(user_id, True)

    for result in (
        await remember({'content': 'new'}, ToolContext(user_id=uid)),
        await forget({'text': 'Keep me'}, ToolContext(user_id=uid)),
    ):
        assert not result.ok and 'locked' in (result.error or '')
    assert store.read(user_id).body == '- Keep me'

    sign_in(app, member(db, uid))
    try:
        client = TestClient(app)
        put = client.put('/v1/me/memory', json={'body': '- Edited by me', 'locked': True})
        assert put.status_code == 200 and put.json()['body'] == '- Edited by me' and put.json()['locked']
        got = client.get('/v1/me/memory').json()
        assert got['body'] == '- Edited by me' and got['truncated'] is False and got['max_chars'] == DOC_MAX_CHARS
        assert client.put('/v1/me/memory', json={'body': 'x' * 8001, 'locked': False}).status_code == 422
    finally:
        app.dependency_overrides.clear()


@needs_db
async def test_every_run_puts_the_users_memory_in_the_system_prompt(db):
    uid = _user(db)
    call_as(member(db, uid), store.save, uuid.UUID(uid), '## Notes\n\n- Sells HR software')
    _, jid = _job(db, uid, 'find companies')
    _, provider = await _run(jid, [('Done.', [])])
    assert 'What you remember about this user' in provider.systems[0]
    assert '- Sells HR software' in provider.systems[0]

    _, jid = _job(db, _user(db), 'find companies')
    _, provider = await _run(jid, [('Done.', [])])
    assert 'What you remember' not in provider.systems[0]
