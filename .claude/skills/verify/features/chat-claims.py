"""#659: the rows an enrich call searches read «In ricerca» while its vendors answer (no LLM or vendor key).

Run inside the worker: `docker compose -p <project> exec -T worker python - <email> [seconds] < features/chat-claims.py`.
It makes a chat with a person list of five people who have no email and no phone, then runs one turn with two
calls through the real handlers: `enrich_email` on Ada, Bo and Cy, then `enrich_phone` on Di. Only the reveal chains
are stand-ins, and each waits `seconds` (default 40) before it answers, so a browser can look at the chat panel and the
list page meanwhile. Ed is in no call: he must never pulse. It prints the session, the list and the job first, then
the rows' state after the turn."""

import asyncio
import json
import sys

import psycopg

from app.agent.loop import run_job
from app.agent.tools.handlers import enrich_email, enrich_phone
from app.core.config import settings
from app.core.db import get_conn
from app.core.scope import acting_as
from app.core.errors import err, ok
from app.lists.rows import add_rows, create_list
from app.llm.base import LLMEvent, LLMResponse, ToolCall
from app.people.apollo_client import ApolloClient
from app.people.ports.contact_enrichment import ContactFacts, ContactSource

EMAIL = sys.argv[1]
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 40
NAMES = ['Ada', 'Bo', 'Cy', 'Di', 'Ed']


class Chain:
    """Waits, then finds the email of Ada and Cy and the phone of Di; Bo is not found."""

    async def reveal(self, hints):
        await asyncio.sleep(SECONDS)
        if hints.first_name == 'Bo':
            return err('CONTACTS.NOT_FOUND', 'stand-in: nobody')
        found = {'Ada': 'ada@verify.test', 'Cy': 'cy@verify.test'}.get(hints.first_name)
        return ok(
            ContactFacts(
                id=hints.first_name, source=ContactSource.apollo, email=found, phone='+39333000111' if not found else None
            )
        )


async def verdict(email):
    return 'verified'


class Keyed(ApolloClient):
    is_configured = True


for handler in (enrich_email, enrich_phone):
    handler.build_reveal_chain = lambda *a, **k: Chain()
    handler.ApolloClient = Keyed
enrich_email.email_status = verdict


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
                " VALUES (default_organization(%s), %s, %s, 'Verify claims') RETURNING id",
                (uid, pid, uid),
            ).fetchone()['id']
        )
        lid = create_list(c, user_id=uid, session_id=sid, name='Verify claims', entity='person')
        rows = [
            {
                'first_name': name,
                'last_name': 'Verify',
                'full_name': f'{name} Verify',
                'company_name': 'Acme',
                'title': 'CEO',
                'linkedin_url': f'https://linkedin.com/in/{name.lower()}-verify',
            }
            for name in NAMES
        ]
        ids = dict(zip(NAMES, add_rows(c, lid, 'person', rows), strict=True))
        # A list gets its columns from the keys it has seen; these people have no email or phone yet.
        for position, (key, label) in enumerate((('email', 'Email'), ('phone', 'Telefono')), start=10):
            c.execute(
                "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, %s, %s, 'static', %s)",
                (lid, key, label, position),
            )
        jid = str(
            c.execute(
                'INSERT INTO jobs (user_id, chat_session_id) VALUES (%s, %s) RETURNING id', (uid, sid)
            ).fetchone()['id']
        )
        c.execute(
            "INSERT INTO chat_messages (session_id, user_id, role, content) VALUES (%s, %s, 'user', %s)",
            (sid, uid, 'Trova le email di Ada, Bo e Cy e il telefono di Di'),
        )
    return sid, lid, jid, ids


async def main():
    sid, lid, jid, ids = setup()
    print(json.dumps({'session': sid, 'list': lid, 'job': jid, 'rows': ids}), flush=True)
    turns = [
        ('', [ToolCall('e1', 'enrich_email', {'row_ids': [ids[n] for n in ('Ada', 'Bo', 'Cy')]})]),
        ('', [ToolCall('p1', 'enrich_phone', {'row_ids': [ids['Di']]})]),
        ('Fatto.', []),
    ]
    await run_job(jid, provider=Scripted(turns))
    with get_conn() as c:
        job = c.execute('SELECT status, credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()
        rows = c.execute(
            "SELECT cells->>'full_name' AS name, cells->'email' AS email, cells->'phone' AS phone"
            ' FROM list_rows r WHERE list_id = %s ORDER BY seq',
            (lid,),
        ).fetchall()
        claims = c.execute('SELECT count(*) AS n FROM lookup_claims').fetchone()['n']
    print(json.dumps({'status': job['status'], 'credits_spent': job['credits_spent'], 'claims_left': claims}))
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
