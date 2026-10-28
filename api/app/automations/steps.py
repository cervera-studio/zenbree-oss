"""The steps that find, score, look up and enroll people (#939). The HubSpot step is ``hubspot.py``.

Each step has ``fetch`` (vendor calls, nothing written, safe to repeat) and ``apply`` (one transaction from
``run.guarded``: the cells, the charge and the step's own record). ``apply`` bills only what it stores: a found row that
landed, at the price of its tool, inside the credits the hold still has.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from app.agent.tools.handlers import enrich_email, search_people
from app.agent.tools.handlers._rows import DISABLED
from app.agent.tools.registry import ToolContext, ToolResult
from app.audience.follow import with_aliases
from app.automations import gate, qualify
from app.automations.models import AddToCampaign, FindEmails, FindPeople, Qualify
from app.automations.run import Outcome, Run, StepFailed
from app.billing.price import Price, Quote
from app.billing.row_charges import finalize
from app.core.db import get_conn
from app.crm import gate as crm_gate
from app.exclusions.rules import is_excluded, load_rules
from app.lists import delivery
from app.lists.lookups import deliverable, email_lookup
from app.lists.rows import add_rows
from app.outreach.drafts import MAX_TARGETS, enroll_rows, enrolled_status
from app.scheduling.runner import enrollment_context

# Accounts a run looks at: each is one free search, and the task has 15 minutes.
ACCOUNTS_PER_RUN = 10
CONCURRENCY = 3
QUALIFIED = Price(1, (), lambda cells: True)  # one credit for each person the model scored
_NO_RETRY = ('not configured', DISABLED)


def _failed(result: ToolResult) -> StepFailed:
    """A handler that answered not ok did nothing that costs: its error says whether trying again can help."""
    permanent = result.error_code in ('disabled', 'not_connected') or any(w in (result.error or '') for w in _NO_RETRY)
    return StepFailed('vendor_disabled' if permanent else 'vendor_failed', retry=not permanent)


# ── find people ─────────────────────────────────────────────────────────────


@dataclass
class Found:
    rows: list[dict] = field(default_factory=list)
    accounts: int = 0
    no_domain: int = 0
    excluded_accounts: int = 0
    last_seq: int = 0
    exhausted: bool = False


async def fetch_people(run: Run, step: FindPeople, position: int) -> Found:
    with get_conn() as conn:
        if run.target_account_id:  # a signal run: its own account, and the rotation stays where it is
            accounts = conn.execute(
                'SELECT seq, cells FROM list_rows WHERE id = %s AND list_id = %s AND NOT excluded',
                (run.target_account_id, step.accounts_list_id),
            ).fetchall()
        else:
            after = conn.execute('SELECT accounts_seq FROM automations WHERE id = %s', (run.automation_id,)).fetchone()
            accounts = conn.execute(
                'SELECT seq, cells FROM list_rows WHERE list_id = %s AND NOT excluded AND seq > %s ORDER BY seq'
                ' LIMIT %s',
                (step.accounts_list_id, after['accounts_seq'], ACCOUNTS_PER_RUN),
            ).fetchall()
        rules = load_rules(conn, run.project_id)
    found = Found(accounts=len(accounts), exhausted=len(accounts) < ACCOUNTS_PER_RUN)
    found.last_seq = accounts[-1]['seq'] if accounts else 0
    domains = []
    for account in accounts:
        domain = str(account['cells'].get('domain') or '').strip()
        if is_excluded(rules, account['cells']):
            found.excluded_accounts += 1
        elif not domain:
            found.no_domain += 1
        else:
            domains.append(domain)
    gate_ = asyncio.Semaphore(CONCURRENCY)

    async def people_of(domain: str) -> ToolResult:
        async with gate_:
            args = {'company_domains': [domain], 'titles': step.titles, 'source': 'apollo', 'page': 1}
            if step.seniorities:
                args['seniorities'] = step.seniorities
            return await search_people.handle(args, ToolContext(user_id=run.user_id, project_id=run.project_id))

    for domain, result in zip(domains, await asyncio.gather(*(people_of(d) for d in domains)), strict=True):
        if not result.ok:
            raise _failed(result)
        rows = result.rows[: step.per_account]
        if run.target_account_id:
            # Apollo's search leaves the company domain out of its rows: the domain searched is the person's company,
            # and it is what matches the person to the account (``signal_trigger.people_at_account``).
            rows = [{**row, 'company_domain': row.get('company_domain') or domain} for row in rows]
        found.rows += rows
    return found


def apply_people(conn, tab, run: Run, step: FindPeople, position: int, found: Found) -> Outcome:
    rules = load_rules(conn, run.project_id)
    allowed = [r for r in found.rows if not is_excluded(rules, r)]
    added = add_rows(conn, run.list_id, 'person', allowed)
    if not run.target_account_id:
        conn.execute(
            'UPDATE automations SET accounts_seq = %s WHERE id = %s',
            (0 if found.exhausted else found.last_seq, run.automation_id),
        )
    return Outcome(
        f'looked at {found.accounts} accounts, added {len(added)} people',
        {
            'accounts': found.accounts,
            'accounts_without_domain': found.no_domain,
            'accounts_excluded': found.excluded_accounts,
            'people_found': len(found.rows),
            'people_excluded': len(found.rows) - len(allowed),
            'people_added': len(added),
        },
    )


# ── qualify ─────────────────────────────────────────────────────────────────


@dataclass
class Scored:
    scores: dict[str, int]
    left: list[str]  # not scored for want of credits


def _scores(conn, run: Run, ids: list[str]) -> dict[str, int]:
    """What this version of the automation already scored, by row id."""
    rows = conn.execute(
        'SELECT row_id, score FROM automation_scores WHERE automation_id = %s AND version = %s AND row_id = ANY(%s)',
        (run.automation_id, run.version, ids),
    )
    return {str(r['row_id']): r['score'] for r in rows}


def _store_scores(conn, run: Run, scores: dict[str, int]) -> list[str]:
    """Store the scores and return the people newly scored: only those are billed, so a person is billed once."""
    stored = conn.execute(
        'INSERT INTO automation_scores (automation_id, version, row_id, organization_id, score)'
        ' SELECT %s, %s, id, %s, (%s::jsonb ->> id::text)::smallint FROM unnest(%s::uuid[]) id'
        ' ON CONFLICT DO NOTHING RETURNING row_id',
        (run.automation_id, run.version, run.org_id, Jsonb(scores), list(scores)),
    ).fetchall()
    return [str(r['row_id']) for r in stored]


async def fetch_qualify(run: Run, step: Qualify, position: int) -> Scored:
    with get_conn() as conn:
        people = gate.load(conn, run.list_id, run.row_ids)
        scored = _scores(conn, run, [str(p['id']) for p in people])
    todo = [p for p in people if str(p['id']) not in scored]
    reached, left = todo[: run.remaining], todo[run.remaining :]
    scores = await qualify.score(run.user_id, step.brief, reached) if reached else {}
    return Scored(scores, [str(p['id']) for p in left])


def apply_qualify(conn, tab, run: Run, step: Qualify, position: int, scored: Scored) -> Outcome:
    new = _store_scores(conn, run, scored.scores)
    credits = tab.record(conn, run.call_id(position), run.list_id, new, QUALIFIED) if tab else 0
    people = gate.load(conn, run.list_id, run.row_ids)
    marks = _scores(conn, run, [str(p['id']) for p in people])
    unreached = set(scored.left) | {str(p['id']) for p in people if str(p['id']) not in marks}
    keep = [str(p['id']) for p in people if str(p['id']) not in unreached and marks[str(p['id'])] >= step.min_score]
    return Outcome(
        f'scored {len(scored.scores)}, {len(keep)} reach {step.min_score}',
        {
            'scored': len(scored.scores),
            'kept': len(keep),
            'below_minimum': len(people) - len(unreached) - len(keep),
            'not_reached': len(unreached),
        },
        credits,
        keep,
        sorted(unreached),
    )


# ── find emails ─────────────────────────────────────────────────────────────


@dataclass
class Looked:
    result: ToolResult | None
    left: list[str] = field(default_factory=list)  # not searched for want of credits


def _wants_email(person: dict) -> bool:
    cells = person['cells']
    return not cells.get('email') and cells.get('email_status') != 'not_found'


async def fetch_emails(run: Run, step: FindEmails, position: int) -> Looked:
    with get_conn() as conn:
        people = [p for p in gate.load(conn, run.list_id, run.row_ids) if _wants_email(p)]
    # Each person found costs a credit: never search more of them than the hold has credits left.
    reached, left = people[: run.remaining], people[run.remaining :]
    if not reached:
        return Looked(None, [str(p['id']) for p in left])
    ctx = ToolContext(user_id=run.user_id, list_id=run.list_id, project_id=run.project_id, max_units=run.remaining)
    result = await enrich_email.handle({'row_ids': [str(p['id']) for p in reached]}, ctx)
    if not result.ok and result.error_code != 'skipped':  # skipped: the rules or the list took everyone out
        raise _failed(result)
    return Looked(result, [str(p['id']) for p in left])


def apply_emails(conn, tab, run: Run, step: FindEmails, position: int, looked: Looked) -> Outcome:
    people = {str(p['id']): p for p in gate.load(conn, run.list_id, run.row_ids)}
    updates = looked.result.row_updates if looked.result and looked.result.ok else []
    campaign = gate.open_campaign(conn, run.definition)
    owners = {run.user_id} | ({str(campaign['owner_id'])} if campaign and campaign['owner_id'] else set())
    opted_out = with_aliases(
        conn, run.project_id, frozenset().union(*(crm_gate.opted_out_rules(conn, o) for o in owners))
    )
    rules = load_rules(conn, run.project_id) | opted_out
    # An address a rule refuses is neither stored nor billed: the exclusion is checked before the credit is spent.
    allowed = [
        u
        for u in updates
        if (person := people.get(str(u['row_id']))) and not is_excluded(rules, {**person['cells'], **u['cells']})
    ]
    delivered = delivery.deliver(
        conn, tab, Quote(enrich_email.PRICE, run.remaining), run.call_id(position), allowed, run.list_id, 'person'
    )
    after = gate.load(conn, run.list_id, run.row_ids)
    failed = {str(p['id']) for p in after if p['cells'].get('email_status') == 'failed'}
    # The found address tells who the person is: a rule that refuses it refuses their LinkedIn profile too.
    refused = {str(u['row_id']) for u in updates} - {str(u['row_id']) for u in allowed}
    keep = [
        str(p['id'])
        for p in after
        if str(p['id']) not in failed | refused
        and (p['cells'].get('linkedin_url') or deliverable(email_lookup(p['cells'], {})))
    ]
    return Outcome(
        f'searched {len(updates)}, found {delivered.credits // enrich_email.PRICE.credits} emails',
        {
            'searched': len(updates),
            'found': delivered.credits // enrich_email.PRICE.credits,
            'refused_by_rules': len(updates) - len(allowed),
            'vendor_did_not_answer': len(failed),
            'not_reached': len(looked.left),
            'with_contact': len(keep),
        },
        delivered.credits,
        keep,
        sorted(set(looked.left) | failed),
    )


# ── campaign ────────────────────────────────────────────────────────────────


async def fetch_nothing(run: Run, step: object, position: int) -> None:
    return None


def apply_campaign(conn, tab, run: Run, step: AddToCampaign, position: int, _: None) -> Outcome:
    # Same lock order as enrollment and refill: the project first.
    campaign = conn.execute('SELECT project_id FROM outreach_campaigns WHERE id = %s', (step.campaign_id,)).fetchone()
    if campaign:
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (campaign['project_id'],))
    campaign = conn.execute(
        "SELECT * FROM outreach_campaigns WHERE id = %s AND status NOT IN ('stopped', 'completed') FOR UPDATE",
        (step.campaign_id,),
    ).fetchone()
    if not campaign:
        raise StepFailed('campaign_ended', retry=False)
    # The first run of a version is reviewed: its people wait as drafts. The campaign's status is never changed here.
    status = 'draft' if run.review else enrolled_status(campaign)
    have = conn.execute(
        'SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id = %s', (campaign['id'],)
    ).fetchone()['n']
    people = gate.load(conn, run.list_id, run.row_ids)
    targets: list[str] = []
    enrolled, left_out = enroll_rows(
        conn,
        campaign,
        campaign['owner_id'],
        people,
        enrollment_context(conn, campaign),
        room=MAX_TARGETS - have,
        status=status,
        collect=targets,
    )
    finalize(conn, enrolled)
    if targets:
        conn.execute(
            'INSERT INTO automation_enrollments (run_id, target_id, organization_id)'
            ' SELECT %s, id, %s FROM unnest(%s::uuid[]) id',
            (run.id, run.org_id, targets),
        )
    return Outcome(
        f'added {len(enrolled)} people as {status}',
        {'enrolled': len(enrolled), 'status': status, 'left_out': dict(Counter(left_out))},
    )
