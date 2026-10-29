"""What an automation would do, read before it is saved or enabled (#940): every missing prerequisite, the people and
accounts it would work on today, and the most a run may spend. Reads only the database: no vendor, no model, no row
written. The one vendor read of a preview, the Instantly campaign, is ``instantly_step.preview``'s.

The counts follow the run: the people of the list it has not taken in yet (``gate``), the accounts of the company list,
and for a signal trigger the accounts its recent signals would have started a run for (``signal_trigger.preview``).
The exclusion and campaign rules that ``gate.take`` applies per person are not counted here: they cost nothing, and
the run's summary reports them.
"""

from __future__ import annotations

from app.agent.tools.handlers import enrich_email
from app.auth.middleware import CurrentUser
from app.automations import definition as checks
from app.automations import gate, service, signal_trigger, steps
from app.automations.models import AddToCampaign, AutomationPreviewIn, FindPeople
from app.automations.triggers import ManualTrigger, ScheduleTrigger, SignalTrigger, Trigger, WebhookTrigger, of_row
from app.core.db import get_conn
from app.lists.lookups import deliverable_email_sql

_CREDITS_EACH = {'qualify': steps.QUALIFIED.credits, 'find_emails': enrich_email.PRICE.credits}

_PEOPLE_SQL = f"""
SELECT count(*) AS people,
       count(*) FILTER (WHERE {deliverable_email_sql('r.cells')}) AS with_email,
       count(*) FILTER (WHERE {deliverable_email_sql('r.cells')}
                        OR btrim(coalesce(r.cells->>'linkedin_url', '')) <> '') AS with_contact
FROM list_rows r
WHERE r.list_id = %s AND NOT r.excluded
  AND NOT EXISTS (SELECT 1 FROM automation_rows a WHERE a.automation_id = %s AND a.row_id = r.id)"""


# A watchlist that is gone or paused starts no run, so the preview counts none.
_WATCHLIST_PROBLEMS = frozenset({'AUTOMATION.WATCHLIST_NOT_FOUND', 'AUTOMATION.WATCHLIST_PAUSED'})

# A draft that names only its kind. Its cadence changes nothing a preview shows.
_OF_KIND: dict[str, Trigger] = {
    'manual': ManualTrigger(),
    'schedule': ScheduleTrigger(kind='schedule', cadence='daily'),
    'webhook': WebhookTrigger(kind='webhook'),
}


def _per_run(trigger: Trigger) -> tuple[int, int]:
    """The people and the accounts one run works on, at most. A signal run takes up to ``gate.BATCH`` people at its
    account: the ones its find-people step adds and the ones the list already holds there."""
    if trigger.kind == 'webhook':
        return 1, steps.ACCOUNTS_PER_RUN
    if trigger.kind == 'signal':
        return gate.BATCH, 1
    return gate.BATCH, steps.ACCOUNTS_PER_RUN


def preview(user: CurrentUser, project_id: str, body: AutomationPreviewIn) -> dict:
    definition = body.definition
    with get_conn() as conn:
        creator: str | None = user.id
        automation_id = None
        trigger: Trigger = ManualTrigger()
        if body.automation_id:
            # Any member may read an automation, so any member may preview it.
            saved = service.owned(conn, user, project_id, body.automation_id, manage=False)
            creator = str(saved['user_id']) if saved['user_id'] else None
            stored = conn.execute('SELECT * FROM automations WHERE id = %s', (saved['id'],)).fetchone()
            automation_id, trigger = saved['id'], of_row(stored)
        if body.trigger is not None:
            trigger = body.trigger
        elif body.trigger_kind not in (None, trigger.kind):
            trigger = _OF_KIND[body.trigger_kind]
        problems = checks.problems(conn, user.org_id, project_id, creator, definition, trigger)
        missing = {p.code for p in problems}
        people = None
        if 'AUTOMATION.LIST_NOT_FOUND' not in missing:
            people = conn.execute(_PEOPLE_SQL, (definition.list_id, automation_id)).fetchone()
        find = definition.step('find_people')
        accounts = None
        if isinstance(find, FindPeople) and 'AUTOMATION.ACCOUNTS_NOT_FOUND' not in missing:
            accounts = conn.execute(
                'SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND NOT excluded', (find.accounts_list_id,)
            ).fetchone()['n']
        add = definition.step('add_to_campaign')
        campaign = None
        if isinstance(add, AddToCampaign) and 'AUTOMATION.CAMPAIGN_NOT_FOUND' not in missing:
            campaign = conn.execute(
                # Also here: a campaign that ended since the check has no status the answer can carry.
                "SELECT id, name, status FROM outreach_campaigns WHERE id = %s AND status NOT IN ('stopped', 'completed')",
                (add.campaign_id,),
            ).fetchone()
        signals = None
        if isinstance(trigger, SignalTrigger) and missing.isdisjoint(_WATCHLIST_PROBLEMS):
            signals = signal_trigger.preview(conn, trigger, automation_id)
    contact = people if people and (add or definition.step('add_to_instantly')) else None
    each = sum(_CREDITS_EACH.get(s.type, 0) for s in definition.steps)
    per_run, accounts_per_run = _per_run(trigger)
    return {
        'problems': [{'code': p.code, 'message': p.message} for p in problems],
        'steps': [{'type': s.type, 'credits_each': _CREDITS_EACH.get(s.type, 0)} for s in definition.steps],
        'people': people['people'] if people else None,
        'with_email': contact['with_email'] if contact else None,
        'with_contact': contact['with_contact'] if contact else None,
        'accounts': accounts,
        'campaign': campaign,
        'instantly': None,
        'signals': signals,
        'people_per_run': per_run,
        'accounts_per_run': accounts_per_run,
        # Each paid step bills a person once, inside a hold of at most the cap.
        'max_credits': min(body.credit_cap, per_run * each),
    }
