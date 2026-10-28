"""#659: one email lookup of each state in a real turn, with a scripted model and a stand-in contact source (no LLM or
vendor key).

Run inside the worker: `docker compose -p <project> exec -T worker python - <email> < features/chat-lookups.py`.
It makes a chat with a person list of four people whose `title` column is required, then runs one turn whose
`enrich_email` call searches all four through the real handler. Only the reveal chain and the MX verdict are stand-ins:
Ada is found (verified), Bo is not found, the source fails for Cy, and Di is found (catch_all) but has no title,
so the turn refunds her email. It prints the session, the list and the job, then the job's credits and each row's
email cells."""

import asyncio
import json
import sys

import psycopg

from app.agent.loop import run_job
from app.agent.tools.handlers import enrich_email
from app.core.config import settings
from app.core.db import get_conn
from app.core.scope import acting_as
from app.core.errors import err, ok
from app.lists.rows import add_rows, create_list
from app.llm.base import LLMEvent, LLMResponse, ToolCall
from app.people.apollo_client import ApolloClient
from app.people.ports.contact_enrichment import ContactFacts, ContactSource

EMAIL = sys.argv[1]
PEOPLE = {
    'Ada': ('ada@verify.test', 'verified'),
    'Bo': None,
    'Cy': 'fail',
    'Di': ('di@verify.test', 'catch_all'),
}


class Chain:
    async def reveal(self, hints):
        answer = PEOPLE[hints.first_name]
        if answer == 'fail':
            return err('CONTACTS.UPSTREAM_ERROR', 'stand-in source down')
        email = answer[0] if answer else None
        return ok(ContactFacts(id=hints.first_name, source=ContactSource.apollo, email=email))


async def verdict(email):
    return next(a[1] for a in PEOPLE.values() if isinstance(a, tuple) and a[0] == email)


class Keyed(ApolloClient):
    is_configured = True


enrich_email.build_reveal_chain = lambda *a, **k: Chain()
enrich_email.email_status = verdict
enrich_email.ApolloClient = Keyed


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


def setup():
    with get_conn() as c:
        uid = str(c.execute('SELECT id FROM "user" WHERE email = %s', (EMAIL,)).fetchone()['id'])
        pid = c.execute(
            'SELECT id FROM projects WHERE organization_id = default_organization(%s) ORDER BY created_at LIMIT 1',
            (uid,),
        ).fetchone()['id']
        sid = str(
            c.execute(
                'INSERT INTO chat_sessions (organization_id, project_id, user_id, title)'
                " VALUES (default_organization(%s), %s, %s, 'Verify 659') RETURNING id",
                (uid, pid, uid),
            ).fetchone()['id']
        )
        lid = create_list(c, user_id=uid, session_id=sid, name='Verify 659', entity='person')
        rows = [
            {
                'first_name': name,
                'last_name': 'Verify',
                'full_name': f'{name} Verify',
                'company_name': 'Acme',
                'title': None if name == 'Di' else 'CEO',
                'linkedin_url': f'https://linkedin.com/in/{name.lower()}-verify',
            }
            for name in PEOPLE
        ]
        ids = add_rows(c, lid, 'person', rows)
        c.execute(
            "UPDATE list_columns SET config = config || '{\"required\": true}' WHERE list_id = %s AND key = 'title'",
            (lid,),
        )
        jid = str(
            c.execute(
                'INSERT INTO jobs (organization_id, user_id, chat_session_id) VALUES (default_organization(%s), %s, %s)'
                ' RETURNING id', (uid, uid, sid)
            ).fetchone()['id']
        )
        c.execute(
            "INSERT INTO chat_messages (session_id, user_id, role, content) VALUES (%s, %s, 'user', %s)",
            (sid, uid, 'Trova le email di Ada, Bo, Cy e Di'),
        )
    return sid, lid, jid, ids


async def main():
    sid, lid, jid, ids = setup()
    print(json.dumps({'session': sid, 'list': lid, 'job': jid}), flush=True)
    turns = [('', [ToolCall('e1', 'enrich_email', {'row_ids': ids})]), ('Ho cercato le email di 4 persone.', [])]
    await run_job(jid, provider=Scripted(turns))
    with get_conn() as c:
        job = c.execute('SELECT status, credits_spent, result FROM jobs WHERE id = %s', (jid,)).fetchone()
        rows = c.execute(
            "SELECT cells->>'full_name' AS name, cells->'email' AS email, cells->'email_status' AS status,"
            ' (SELECT c.state FROM row_charges c WHERE c.row_id = r.id ORDER BY c.created_at DESC LIMIT 1) AS charge'
            ' FROM list_rows r WHERE list_id = %s ORDER BY seq',
            (lid,),
        ).fetchall()
    print(json.dumps({'status': job['status'], 'credits_spent': job['credits_spent'], 'result': job['result']}))
    for r in rows:
        print(json.dumps(r))


# The worker runs a turn as the member who started it; with no scope a checkout raises ScopeMissing. Who that is
# comes from a raw connection, read before the scope exists.
with psycopg.connect(settings.database_url) as raw:
    USER, ORG = raw.execute(
        'SELECT id::text, default_organization(id)::text FROM "user" WHERE email = %s', (EMAIL,)
    ).fetchone()
with acting_as(ORG, USER):
    asyncio.run(main())
