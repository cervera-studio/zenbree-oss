"""Do the resources a definition names exist, belong to the project, and may the user use them? Is the user, the
creator as whom runs spend and write, still a member of the organization? (#939, #1023)

Asked when an automation is made, edited or enabled, and again when a run is claimed: a list or a campaign can go away,
and a user can turn HubSpot write-back off, between the two. Every ``(status, code, message)`` that fails, in the
order of the definition; the preview shows them all, a write refuses on the first.
"""

from __future__ import annotations

from typing import NamedTuple

from app.automations import instantly_step
from app.automations.models import AddToCampaign, AddToInstantly, Definition, FindPeople
from app.automations.triggers import SignalTrigger, Trigger
from app.integrations import instantly_store
from app.integrations.providers import HUBSPOT_CONTACTS_WRITE


class Problem(NamedTuple):
    status: int
    code: str
    message: str


def _list(conn, org_id: str, project_id: str, list_id, entity: str) -> bool:
    return bool(
        conn.execute(
            'SELECT 1 FROM lists WHERE id = %s AND organization_id = %s AND project_id = %s AND entity = %s'
            ' AND deleted_at IS NULL',
            (list_id, org_id, project_id, entity),
        ).fetchone()
    )


def _campaign_open(conn, org_id: str, project_id: str, campaign_id) -> bool:
    return bool(
        conn.execute(
            'SELECT 1 FROM outreach_campaigns WHERE id = %s AND organization_id = %s AND project_id = %s'
            " AND status NOT IN ('stopped', 'completed')",
            (campaign_id, org_id, project_id),
        ).fetchone()
    )


def _creator_live(conn, org_id: str, user_id: str | None) -> bool:
    return conn.execute('SELECT zb.is_live_member(%s, %s) AS ok', (user_id, org_id)).fetchone()['ok']


def _hubspot_writes(conn, user_id: str) -> bool:
    row = conn.execute(
        'SELECT crm_write_back AND %s = ANY(granted_scopes) AS ok FROM user_oauth_connections'
        " WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'",
        (HUBSPOT_CONTACTS_WRITE, user_id),
    ).fetchone()
    return bool(row and row['ok'])


def _watchlist(conn, org_id: str, project_id: str, watchlist_id) -> dict | None:
    return conn.execute(
        'SELECT w.enabled, w.list_id FROM watchlists w JOIN lists l ON l.id = w.list_id'
        ' WHERE w.id = %s AND w.organization_id = %s AND l.project_id = %s AND l.deleted_at IS NULL',
        (watchlist_id, org_id, project_id),
    ).fetchone()


# Refused by a create and by an enable, which check every problem, but not by ``trigger_problems``: an edit of a
# disabled automation may name a paused watchlist, as the automation starts nothing until both run again.
WATCHLIST_PAUSED = Problem(422, 'AUTOMATION.WATCHLIST_PAUSED', 'resume the watchlist first: it finds no signal')


def _signal_problems(conn, org_id: str, project_id: str, trigger: SignalTrigger, definition: Definition):
    watchlist = _watchlist(conn, org_id, project_id, trigger.watchlist_id) if trigger.watchlist_id else None
    if watchlist is None:
        return [Problem(422, 'AUTOMATION.WATCHLIST_NOT_FOUND', 'pick a watchlist of this project')]
    found = []
    find = definition.step('find_people')
    # A signal run looks for people at its own account only, which is a row of the watchlist's list.
    if isinstance(find, FindPeople) and find.accounts_list_id != watchlist['list_id']:
        found.append(
            Problem(
                422, 'AUTOMATION.SIGNAL_ACCOUNTS_LIST', "find people at the accounts of the watchlist's company list"
            )
        )
    if not watchlist['enabled']:
        found.append(WATCHLIST_PAUSED)
    return found


def _trigger_problems(conn, org_id: str, project_id: str, trigger: Trigger, definition: Definition) -> list[Problem]:
    if trigger.kind == 'webhook' and definition.step('find_people'):
        # A call names its one person: a find-people step would add rows to the list and move on in the accounts.
        return [
            Problem(422, 'AUTOMATION.WEBHOOK_FIND_PEOPLE', 'a webhook names its person: remove the find-people step')
        ]
    if isinstance(trigger, SignalTrigger):
        return _signal_problems(conn, org_id, project_id, trigger, definition)
    return []


def trigger_problems(conn, org_id: str, project_id: str, trigger: Trigger, definition: Definition) -> list[Problem]:
    """What the trigger and the definition cannot do together, or a watchlist the trigger may not name: no save
    stores these, enabled or not."""
    return [p for p in _trigger_problems(conn, org_id, project_id, trigger, definition) if p is not WATCHLIST_PAUSED]


def problems(
    conn, org_id: str, project_id: str, user_id: str | None, definition: Definition, trigger: Trigger
) -> list[Problem]:
    """``user_id`` is the creator, ``None`` once they deleted their account: runs spend and write as them."""
    found = _trigger_problems(conn, org_id, project_id, trigger, definition)
    creator_live = _creator_live(conn, org_id, user_id)
    if not creator_live:
        # The beat skips the automation and a run is refused at its claim: only a new creator makes it run.
        found.append(
            Problem(422, 'AUTOMATION.CREATOR_GONE', 'its creator is no longer an active member: make a new automation')
        )
    if not _list(conn, org_id, project_id, definition.list_id, 'person'):
        found.append(Problem(422, 'AUTOMATION.LIST_NOT_FOUND', 'pick a person list of this project'))
    for step in definition.steps:
        if isinstance(step, FindPeople) and not _list(conn, org_id, project_id, step.accounts_list_id, 'company'):
            found.append(
                Problem(422, 'AUTOMATION.ACCOUNTS_NOT_FOUND', 'pick a company list of this project to find people at')
            )
        if isinstance(step, AddToCampaign) and not _campaign_open(conn, org_id, project_id, step.campaign_id):
            found.append(
                Problem(422, 'AUTOMATION.CAMPAIGN_NOT_FOUND', 'pick a campaign of this project that has not ended')
            )
        if isinstance(step, AddToInstantly) and not instantly_step.connected(instantly_store.row(conn, project_id)):
            # Only the database: whether the campaign still exists is a read of Instantly (the preview, the run).
            found.append(Problem(422, 'AUTOMATION.INSTANTLY_NOT_CONNECTED', 'connect Instantly for this project first'))
        # The grant of a creator who is gone is moot: CREATOR_GONE already names the fix.
        if step.type == 'write_hubspot' and creator_live and not _hubspot_writes(conn, str(user_id)):
            found.append(Problem(422, 'AUTOMATION.HUBSPOT_OFF', 'connect HubSpot and turn on write-back first'))
    return found
