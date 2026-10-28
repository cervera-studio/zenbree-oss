"""#408 #465: the plan auto-approval gate in the real worker, with a scripted model (no LLM key).

Run inside the worker: `docker compose -p <project> exec -T worker python - <email> < features/chat-plan-gate.py`.
It prints one JSON line per scenario: the job, its plans and the plan card it left."""

import asyncio
import json
import sys

import psycopg

from app.agent.loop import run_job
from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.price import Price
from app.core.config import settings
from app.core.db import get_conn
from app.core.scope import acting_as
from app.llm.base import LLMEvent, LLMResponse, ToolCall

EMAIL = sys.argv[1]


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


PAID_ROWS = 90


async def paid(args, ctx):
    # The store bills found row updates only (#727), so the call finds a cell on each row of the session's list.
    with get_conn() as c:
        ids = [r['id'] for r in c.execute('SELECT id FROM list_rows WHERE list_id = %s', (ctx.list_id,))]
    updates = [{'row_id': str(i), 'cells': {'verify_note': 'found'}} for i in ids]
    return ToolResult(ok=True, data={'note': 'verify-only paid call'}, row_updates=updates)


registry.register(
    ToolDef(
        name='verify_paid',
        description='x',
        input_schema={'type': 'object'},
        handler=paid,
        # A call that names no row quotes `batch` rows: 90 credits, within the turn's 100-credit hold.
        price=Price(1, ('verify_note',), lambda cells: bool(cells.get('verify_note')), batch=PAID_ROWS),
    )
)


def research(label, question, rows):
    return {
        'mode': 'build',
        'entity': 'person',
        'target_rows': rows,
        'list_name': 'Plan gate',
        'columns': [{'key': 'full_name'}, {'label': label, 'research': question}],
        'stages': [],
    }


def job(title, rows=0):
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
        if rows:  # a person list on the session, which the turn loads as its current list
            lid = c.execute(
                'INSERT INTO lists (organization_id, user_id, project_id, chat_session_id, name, entity)'
                " VALUES (default_organization(%s), %s, %s, %s, 'Plan gate', 'person') RETURNING id",
                (uid, uid, pid, sid),
            ).fetchone()['id']
            c.execute(
                "INSERT INTO list_rows (list_id, cells, dedupe_key) SELECT %s, jsonb_build_object('full_name',"
                " 'Persona ' || g), 'p' || g FROM generate_series(1, %s) g",
                (lid, rows),
            )
    return str(sid), str(jid)


async def scenario(title, turns, rows=0):
    sid, jid = job(title, rows)
    provider = Scripted(turns)
    await run_job(jid, provider=provider)
    with get_conn() as c:
        j = c.execute('SELECT status, credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()
        records = c.execute(
            'SELECT count(*) AS n, coalesce(sum(credits), 0) AS credits FROM row_charges WHERE job_id = %s', (jid,)
        ).fetchone()
        plans = c.execute(
            "SELECT p.version, p.plan->>'estimated_credits' AS est, p.approved_job_id IS NOT NULL AS approved"
            ' FROM list_plans p JOIN lists l ON l.id = p.list_id WHERE l.chat_session_id = %s ORDER BY version',
            (sid,),
        ).fetchall()
        q = c.execute(
            "SELECT question->'plan'->>'version' AS v, question->'plan'->>'needs_approval' AS na FROM chat_messages"
            " WHERE session_id = %s AND role = 'assistant' ORDER BY created_at DESC LIMIT 1",
            (sid,),
        ).fetchone()
    print(
        json.dumps(
            {
                'title': title,
                'session': sid,
                'job': {**j},
                'records': {**records},
                'unused_model_turns': len(provider.turns),
                'plans': [dict(p) for p in plans],
                'card': dict(q) if q else None,
            }
        )
    )


async def main():
    await scenario(
        '408 A: two research plans in one turn',
        [
            ('', [ToolCall('p1', 'propose_plan', research('Vende a ospedali', 'Vende a ospedali?', 60))]),
            ('', [ToolCall('p2', 'propose_plan', research('Usa SAP', 'Usa SAP?', 30))]),
            ('unused', []),
        ],
    )
    await scenario(
        '408 B: 90 credits spent, then a 20-credit plan',
        [
            ('', [ToolCall('v1', 'verify_paid', {})]),
            ('', [ToolCall('p1', 'propose_plan', research('Usa SAP', 'Usa SAP?', 20))]),
            ('unused', []),
        ],
        rows=PAID_ROWS,
    )
    await scenario(
        '465 C: a 150-credit plan and a 50-credit plan in one step',
        [
            (
                '',
                [
                    ToolCall('p1', 'propose_plan', research('Vende a ospedali', 'Vende a ospedali?', 150)),
                    ToolCall('p2', 'propose_plan', research('Usa SAP', 'Usa SAP?', 50)),
                ],
            ),
            ('unused', []),
        ],
    )


# The worker runs a turn as the member who started it; with no scope a checkout raises ScopeMissing. Who that is
# comes from a raw connection, read before the scope exists.
with psycopg.connect(settings.database_url) as raw:
    USER, ORG = raw.execute(
        'SELECT id::text, default_organization(id)::text FROM "user" WHERE email = %s', (EMAIL,)
    ).fetchone()
with acting_as(ORG, USER):
    asyncio.run(main())
