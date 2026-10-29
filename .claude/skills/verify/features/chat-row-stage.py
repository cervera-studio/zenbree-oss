"""#568: a per-row plan stage when the search delivers fewer rows than approved, in the real worker with a scripted
model (no LLM or vendor key).

Run inside the worker: `docker compose -p <project> exec -T worker python - <email> <scenario> < features/chat-row-stage.py`.
Both scenarios auto-approve a plan for 5 people whose search finds 2, then one email call searches both.
Scenario `short` ends there. Scenario `more` then finds 2 more people and ends without searching them.
It prints the session and job, then the job's status, stages as [tool, units, done] and progress."""

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
from app.lists.lookup_outcome import searched
from app.llm.base import LLMEvent, LLMResponse, ToolCall

EMAIL, SCENARIO = sys.argv[1], sys.argv[2]


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


def person(name, title):
    return {'full_name': f'{name} Verify', 'title': title, 'linkedin_url': f'https://linkedin.com/in/{name.lower()}-verify'}


async def people(args, ctx):
    return ToolResult(ok=True, rows=[person('Ada', 'CTO'), person('Bo', 'CEO')], entity='person', data={'found': 2})


async def more_people(args, ctx):
    return ToolResult(ok=True, rows=[person('Cy', 'CFO'), person('Di', 'COO')], entity='person', data={'found': 2})


async def emails(args, ctx):
    with get_conn() as c:
        ids = [str(r['id']) for r in c.execute('SELECT id FROM list_rows WHERE list_id = %s ORDER BY seq', (ctx.list_id,))]
    updates = [{'row_id': r, 'cells': {'email': f'p{i}@verify.test', 'email_status': 'verified'}} for i, r in enumerate(ids)]
    # A result that writes email_status needs the outcome the registry checks (#713); `searched` builds it from the updates.
    outcome = searched('email', updates).outcome
    return ToolResult(
        ok=True, row_updates=updates, entity='person', data={'searched': len(ids), 'found': len(ids)}, outcome=outcome
    )


rows = {'type': 'object', 'properties': {'row_ids': {'type': 'array'}}}
for name, handler, noun in (('verify_people', people, 'person'), ('verify_emails', emails, 'email')):
    registry.register(ToolDef(name=name, description='x', input_schema=rows, handler=handler, found_noun=noun))
registry.register(
    ToolDef(name='verify_more_people', description='x', input_schema={'type': 'object'}, handler=more_people)
)

PLAN = {
    'mode': 'build',
    'entity': 'person',
    'target_rows': 5,
    'list_name': 'Verify 568',
    'columns': [{'key': 'full_name', 'required': True}, {'key': 'email'}],
    'stages': [
        {'tool': 'verify_people', 'label': 'Trova persone', 'units': 1},
        {'tool': 'verify_emails', 'label': 'Trova email', 'units': 5, 'per_row': True},
    ],
}
SEARCH = [
    ('', [ToolCall('p1', 'propose_plan', PLAN)]),
    ('', [ToolCall('s1', 'verify_people', {})]),
    ('', [ToolCall('e1', 'verify_emails', {})]),
]
TURNS = {
    'short': [*SEARCH, ('Ho trovato 2 persone con email.', [])],
    'more': [*SEARCH, ('', [ToolCall('s2', 'verify_more_people', {})]), ('Ho trovato 4 persone, 2 con email.', [])],
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
        j = c.execute('SELECT status, stages, progress_done, progress_total FROM jobs WHERE id = %s', (jid,)).fetchone()
    stages = [[s['tool'], s['units'], s['done']] for s in j['stages'] or []]
    print(json.dumps({'status': j['status'], 'stages': stages, 'progress': [j['progress_done'], j['progress_total']]}))


# The worker runs a turn as the member who started it; with no scope a checkout raises ScopeMissing. Who that is
# comes from a raw connection, read before the scope exists.
with psycopg.connect(settings.database_url) as raw:
    USER, ORG = raw.execute(
        'SELECT id::text, default_organization(id)::text FROM "user" WHERE email = %s', (EMAIL,)
    ).fetchone()
with acting_as(ORG, USER):
    asyncio.run(main())
