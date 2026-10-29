"""Who a run may work on, decided before any credit is spent (#939).

The people of the list that this automation has not taken in yet, the best fit first, a batch at a time. Each is checked
for free against the project's exclusions, the HubSpot opt-outs and, when the definition adds to a campaign, the
hold (a stop of the person in any state, or Instantly owning the email, #931) and the campaign's own rule (replied,
contacted before, in another campaign). When it adds to Instantly, the #929 rule without the CRM read
(``instantly_step.free_refusals``). A person it refuses is taken in at once, with the reason, and costs nothing.
``enroll_rows`` and the Instantly batch ask the whole enrollment question again under the project lock, so this check
only keeps credits off people who would never enter.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from app.audience.follow import with_aliases
from app.automations import instantly_step, signal_trigger
from app.automations.models import AddToCampaign, Definition
from app.automations.run import Run
from app.crm import gate
from app.exclusions.rules import is_excluded, load_rules
from app.lists.lookups import deliverable, email_lookup
from app.outreach import holds
from app.scheduling.runner import enrollment_context, enrollment_verdict, identity

# People a run works on, at most: each may cost two credits, and the task has 15 minutes.
BATCH = 25
_UNTAKEN_SQL = """
SELECT r.id, r.cells FROM list_rows r
WHERE r.list_id = %(list)s AND NOT r.excluded
  AND NOT EXISTS (SELECT 1 FROM automation_rows a WHERE a.automation_id = %(automation)s AND a.row_id = r.id){target}
ORDER BY r.relevance_score DESC NULLS LAST, r.seq"""
# A webhook run takes the one person its call named: a row that left the list leaves the run with nobody.
_BEST = _UNTAKEN_SQL.format(target='') + f' LIMIT {BATCH}'
_TARGET = _UNTAKEN_SQL.format(target='\n  AND r.id = %(row)s') + f' LIMIT {BATCH}'
# A signal run's people: the rows whose company domain contains the account's host, or whose company name is the
# account's (``PeopleIndex`` narrows them). Postgres' lower() and btrim() agree with Python's lower() and strip() on
# ASCII text only (not on İ, a final Σ or a no-break space), so a cell with any other character is always read.
_NEAR_ACCOUNT = _UNTAKEN_SQL.format(
    target="""
  AND (strpos(lower(r.cells ->> 'company_domain'), %(host)s) > 0
    OR lower(btrim(r.cells ->> 'company_name', %(space)s)) = %(name)s
    OR octet_length(r.cells ->> 'company_domain') > length(r.cells ->> 'company_domain')
    OR octet_length(r.cells ->> 'company_name') > length(r.cells ->> 'company_name'))"""
)
_ASCII_SPACE = ''.join(c for c in map(chr, range(128)) if c.isspace())  # what str.strip() takes off ASCII text


def open_campaign(conn, definition: Definition) -> dict | None:
    step = definition.step('add_to_campaign')
    if not isinstance(step, AddToCampaign):
        return None
    return conn.execute(
        "SELECT * FROM outreach_campaigns WHERE id = %s AND status NOT IN ('stopped', 'completed')",
        (step.campaign_id,),
    ).fetchone()


def _reason(conn, rules, campaign, context, cells: dict) -> str | None:
    if is_excluded(rules, cells):
        return 'excluded'
    email, linkedin = identity(cells.get('email'), cells.get('linkedin_url'))
    if campaign and (email or linkedin) and (held := holds.for_campaign(conn, campaign, email, linkedin)):
        return holds.SKIP_REASON[held]
    # A person with no contact yet is judged after the email step; one that has a contact is judged now.
    if campaign and (linkedin or (email and deliverable(email_lookup(cells, {})))):
        verdict = enrollment_verdict(cells, campaign, context)[0]
        return {'skip': 'already_handled', 'hold': 'in_other_campaign'}.get(verdict)
    return None


def rescreen(conn, project_id: str, user_id: str, definition: Definition) -> Callable[[dict], str | None]:
    """``take``'s free check again, for people taken some time ago: exclusions, opt-outs, a reply, a contact since, a
    place in another campaign. Their own place in this automation's campaign never refuses them."""
    campaign = open_campaign(conn, definition)
    if not campaign:
        rules = load_rules(conn, project_id) | with_aliases(conn, project_id, gate.opted_out_rules(conn, user_id))
        return lambda cells: _reason(conn, rules, None, ([], [], frozenset(), {}), cells)
    targets, _, rules, ids = enrollment_context(conn, campaign)
    others = [t for t in targets if t['campaign_id'] != campaign['id']]
    sent = (
        conn.execute(
            'SELECT s.recipient, s.channel FROM outreach_campaign_sends s JOIN outreach_campaigns c'
            " ON c.id = s.campaign_id WHERE c.project_id = %s AND c.id <> %s AND s.status = 'sent'",
            (campaign['project_id'], campaign['id']),
        ).fetchall()
        if campaign['block_prior_contacts']
        else []
    )
    return lambda cells: _reason(conn, rules, campaign, (others, sent, rules, ids), cells)


def _untaken(conn, run: Run) -> tuple[list[dict], str | None]:
    untaken = {'list': run.list_id, 'automation': run.automation_id}
    if run.target_account_id:
        at, nobody = signal_trigger.people_at_account(
            conn,
            run.target_account_id,
            lambda host, name: conn.execute(
                _NEAR_ACCOUNT, untaken | {'host': host, 'name': name, 'space': _ASCII_SPACE}
            ).fetchall(),
        )
        return at[:BATCH], nobody
    if run.target_row_id:
        return conn.execute(_TARGET, untaken | {'row': run.target_row_id}).fetchall(), None
    return conn.execute(_BEST, untaken).fetchall(), None


def take(conn, run: Run) -> tuple[list[str], Counter, str | None]:
    """The run's people, written to the run and to ``automation_rows`` as refused when they are. ``(ids, refused,
    nobody)``: why a signal run found nobody at its account (``signal_trigger.people_at_account``)."""
    campaign = open_campaign(conn, run.definition)
    if campaign:
        context = enrollment_context(conn, campaign)
        rules = context[2]
    else:
        context = ([], [], frozenset(), {})
        opted_out = with_aliases(conn, run.project_id, gate.opted_out_rules(conn, run.user_id))
        rules = load_rules(conn, run.project_id) | opted_out
    kept: list[dict] = []
    refused: Counter = Counter()
    refused_ids: list[str] = []
    untaken, nobody = _untaken(conn, run)
    for row in untaken:
        why = _reason(conn, rules, campaign, context, row['cells'])
        if why:
            refused[why] += 1
            refused_ids.append(str(row['id']))
        else:
            kept.append(row)
    late = instantly_step.free_refusals(conn, run, kept)
    for row in kept:
        if why := late.get(str(row['id'])):
            refused[why] += 1
            refused_ids.append(str(row['id']))
    if refused_ids:
        conn.execute(
            'INSERT INTO automation_rows (automation_id, row_id, organization_id, run_id)'
            ' SELECT %s, id, %s, %s FROM unnest(%s::uuid[]) id ON CONFLICT DO NOTHING',
            (run.automation_id, run.org_id, run.id, refused_ids),
        )
    ids = [str(row['id']) for row in kept if str(row['id']) not in late]
    conn.execute('UPDATE automation_runs SET row_ids = %s::uuid[] WHERE id = %s', (ids, run.id))
    if run.target_account_id:
        signal_trigger.took(conn, run.id)
    return ids, refused, nobody


def load(conn, list_id: str, row_ids: list[str]) -> list[dict]:
    """The people of ``row_ids`` still in the list (``id``, ``cells``), in the order they were taken."""
    rows = {
        str(r['id']): r
        for r in conn.execute(
            'SELECT id, cells FROM list_rows WHERE id = ANY(%s::uuid[]) AND list_id = %s',
            (row_ids, list_id),
        ).fetchall()
    }
    return [rows[i] for i in row_ids if i in rows]
