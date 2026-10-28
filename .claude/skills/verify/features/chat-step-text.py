"""#567: the text a model writes beside a tool call, in the real worker with a scripted model (no LLM key).

Run inside the worker: `docker compose -p <project> exec -T worker python - <email> <scenario> < features/chat-step-text.py`.
Scenario `preamble`: a step writes a line and calls a search; the next step answers.
Scenario `answer`: the model answers (over 200 characters) beside its last tool call, then ends with an empty step.
It prints the session and job, then the stored answer."""

import asyncio
import json
import sys

import psycopg

from app.agent.loop import run_job
from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.core.config import settings
from app.core.db import get_conn
from app.core.scope import acting_as
from app.llm.base import LLMEvent, LLMResponse, ToolCall

EMAIL, SCENARIO = sys.argv[1], sys.argv[2]
# Over the 200 characters the loop holds back (#572): a shorter text beside a tool call is dropped for good.
ANSWER = (
    'Ricevuto: vendi agli ospedali. Cerco strutture sanitarie pubbliche e private con un referente acquisti, '
    'poi le ordino per numero di posti letto e per regione, e ti mostro prima quelle con un sito che descrive i reparti.'
)


class Scripted:
    def __init__(self, turns):
        self.turns = list(turns)

    async def chat_stream(self, *, system, messages, tools=None, **kw):
        text, calls = self.turns.pop(0)
        if text:
            yield LLMEvent(type='text_delta', text=text)
        for c in calls:
            yield LLMEvent(type='tool_end', tool_call=c)
        yield LLMEvent(type='done')

    async def chat(self, *, system, messages, **kw):
        return LLMResponse(text=json.dumps({'scores': []}))


async def companies(args, ctx):
    rows = [
        {'name': 'Verify Alpha', 'domain': 'alpha.verify.test'},
        {'name': 'Verify Beta', 'domain': 'beta.verify.test'},
    ]
    return ToolResult(ok=True, rows=rows, entity='company', data={'found': 2})


async def note(args, ctx):
    return ToolResult(ok=True, data={'saved': True})


registry.register(ToolDef(name='verify_note', description='x', input_schema={'type': 'object'}, handler=note))
registry.register(
    ToolDef(
        name='verify_companies',
        description='x',
        input_schema={'type': 'object'},
        handler=companies,
        found_noun='company',
    )
)

TURNS = {
    'preamble': [
        ("I'll look them up in your list first.", [ToolCall('c1', 'verify_companies', {})]),
        ('Ho trovato 2 aziende.', []),
    ],
    'answer': [
        (ANSWER, [ToolCall('n1', 'verify_note', {})]),
        ('', []),
    ],
}


def job(title):
    with get_conn() as c:
        uid = c.execute('SELECT id FROM "user" WHERE email = %s', (EMAIL,)).fetchone()['id']
        pid = c.execute(
            'SELECT id FROM projects WHERE organization_id = default_organization(%s) ORDER BY created_at LIMIT 1',
            (uid,),
        ).fetchone()['id']
        sid = c.execute(
            'INSERT INTO chat_sessions (organization_id, project_id, user_id, title)'
            ' VALUES (default_organization(%s), %s, %s, %s) RETURNING id',
            (uid, pid, uid, title),
        ).fetchone()['id']
        jid = c.execute(
            'INSERT INTO jobs (organization_id, user_id, chat_session_id) VALUES (default_organization(%s), %s, %s)'
                ' RETURNING id', (uid, uid, sid)
        ).fetchone()['id']
        c.execute(
            "INSERT INTO chat_messages (session_id, user_id, role, content) VALUES (%s, %s, 'user', %s)",
            (sid, uid, title),
        )
    return str(sid), str(jid)


async def main():
    sid, jid = job(f'verify {SCENARIO}')
    print(json.dumps({'session': sid, 'job': jid}), flush=True)
    await run_job(jid, provider=Scripted(TURNS[SCENARIO]))
    with get_conn() as c:
        status = c.execute('SELECT status FROM jobs WHERE id = %s', (jid,)).fetchone()['status']
        content = c.execute(
            "SELECT content FROM chat_messages WHERE session_id = %s AND role = 'assistant'", (sid,)
        ).fetchone()['content']
    print(json.dumps({'status': status, 'content': content}), flush=True)


# The worker runs a turn as the member who started it; with no scope a checkout raises ScopeMissing. Who that is
# comes from a raw connection, read before the scope exists.
with psycopg.connect(settings.database_url) as raw:
    USER, ORG = raw.execute(
        'SELECT id::text, default_organization(id)::text FROM "user" WHERE email = %s', (EMAIL,)
    ).fetchone()
with acting_as(ORG, USER):
    asyncio.run(main())
