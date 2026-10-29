"""Who is owed a campaign step now, and the HubSpot read gate's side of that question (#120).

One query answers "due" for the gate's candidates and for the plan phase, so the two never disagree; the
gate fragments below plug into it and into the send queues. The dispatcher runs them (``app.outreach.dispatcher``).
"""

from datetime import datetime

from app.auth.middleware import creator_not_banned
from app.crm import gate
from app.outreach import holds, sending

SENDABLE_CHANNELS = ('email', 'linkedin_connect', 'linkedin_message', 'hubspot_task')
PLAN_BATCH = 500
# #389: a banned owner's active campaign sends nothing, and goes on by itself on unban or when the ban expires.
_OWNER_NOT_BANNED = creator_not_banned('c.owner_id')
_NOT_HELD = holds.not_held('c.project_id', "c.steps -> (t.next_step - 1) ->> 'channel'", 't.email', 't.linkedin_url')

# The people owed a step now. ``{select}`` and ``{gate}`` (one ``now`` parameter) differ between the
# gate's candidates and the plan phase; everything else is shared so the two never disagree on "due".
# #1141: the subquery ``d`` keeps the people owed a step by now, and OFFSET 0 keeps the hold, the duplicate check and
# the gate out of it. They read only ``t`` and ``c``, so the planner ran them on every active person of the
# organization, before the step's own send or its anchor had filtered anyone: 3 times the due ones on the bench.
DUE_SQL = """SELECT {select}
   FROM (SELECT t.id, prev.status AS prev_status, prev.delivered_at AS prev_delivered_at,
                prev.created_at AS prev_created_at
           FROM outreach_campaign_targets t
           JOIN outreach_campaigns c ON c.id = t.campaign_id AND c.owner_id IS NOT DISTINCT FROM t.owner_id
           LEFT JOIN outreach_campaign_sends prev
                  ON prev.campaign_id = t.campaign_id AND prev.target_id = t.id AND prev.step = t.next_step - 1
           CROSS JOIN LATERAL (SELECT CASE WHEN t.next_step = 1 THEN c.activated_at
                                           WHEN prev.status IN ('sent', 'skipped')
                                           THEN coalesce(prev.delivered_at, prev.created_at) END AS at) anchor
          WHERE c.status = 'active' AND t.status = 'active' AND t.next_step <= jsonb_array_length(c.steps)
            AND {not_banned}
            AND c.steps -> (t.next_step - 1) ->> 'channel' = ANY(%s)
            AND NOT EXISTS (SELECT 1 FROM outreach_campaign_sends s
                            WHERE s.campaign_id = t.campaign_id AND s.target_id = t.id AND s.step = t.next_step)
            -- Only the due ones (step_due_at, in SQL), so people waiting days for a follow-up
            -- never fill the batch ahead of people who are owed a message now.
            AND anchor.at IS NOT NULL
            AND greatest(anchor.at, CASE WHEN c.steps -> (t.next_step - 1) ->> 'channel' = 'linkedin_message'
                                         THEN t.linkedin_accepted_at END)
                + make_interval(days => (c.steps -> (t.next_step - 1) ->> 'send_after_days')::int
                     - CASE WHEN t.next_step > 1
                            THEN (c.steps -> (t.next_step - 2) ->> 'send_after_days')::int ELSE 0 END) <= %s
         OFFSET 0) d
   JOIN outreach_campaign_targets t ON t.id = d.id
   JOIN outreach_campaigns c ON c.id = t.campaign_id
   LEFT JOIN LATERAL (SELECT max(i.delivered_at) AS invited_at FROM outreach_campaign_sends i
                      WHERE i.campaign_id = t.campaign_id AND i.target_id = t.id
                        AND i.channel = 'linkedin_connect' AND i.status = 'sent') inv ON true
   WHERE {held}
     -- #83 block_active_duplicates: hold (no claim, every step) a person another active campaign
     -- of this project (#307: same senders) is still writing to. First armed wins, so two campaigns
     -- never hold each other. A person paused by an out-of-office (#346) is still held: the earlier
     -- campaign writes to them again on return. Each check reads the targets by a stored key (#1127): row-level
     -- security keeps the planner from any index on lower() or btrim(), and a read by campaign grew with the square
     -- of the client's size.
     AND NOT (c.block_active_duplicates AND (
         EXISTS (
             SELECT 1 FROM outreach_campaign_targets o
             JOIN outreach_campaigns oc ON oc.id = o.campaign_id AND o.owner_id IS NOT DISTINCT FROM oc.owner_id
             WHERE o.email_key = t.email_key AND oc.project_id = c.project_id
               AND o.campaign_id <> t.campaign_id AND o.status IN ('active', 'paused')
               AND o.next_step <= jsonb_array_length(oc.steps)
               AND oc.status = 'active' AND (oc.activated_at, oc.id) < (c.activated_at, c.id))
         OR EXISTS (
             SELECT 1 FROM outreach_campaign_targets o
             JOIN outreach_campaigns oc ON oc.id = o.campaign_id AND o.owner_id IS NOT DISTINCT FROM oc.owner_id
             WHERE o.linkedin_key = t.linkedin_key AND oc.project_id = c.project_id
               AND o.campaign_id <> t.campaign_id AND o.status IN ('active', 'paused')
               AND o.next_step <= jsonb_array_length(oc.steps)
               AND oc.status = 'active' AND (oc.activated_at, oc.id) < (c.activated_at, c.id))))
     {gate}
     -- A DM waiting for its acceptance (_plan_linkedin) is not due either.
     AND (c.steps -> (t.next_step - 1) ->> 'channel' <> 'linkedin_message'
          OR t.linkedin_accepted_at IS NOT NULL OR inv.invited_at IS NULL OR inv.invited_at <= %s)
   ORDER BY t.created_at LIMIT %s"""

PLAN_SELECT = """t.*, c.steps, c.activated_at, c.approve_before_sending, c.project_id,
          d.prev_status, d.prev_delivered_at, d.prev_created_at, inv.invited_at"""

# The owner's grant counts only while they are a live member of the target's organization: one who left it has their
# HubSpot neither read nor obeyed for its people (the beat's ForOrg scope hides the grant too; this says it in SQL),
# except a stored opt-out (exclude_opted_out_of_departed_owners).
_HUBSPOT_OWNER = """EXISTS (SELECT 1 FROM user_oauth_connections h
                 WHERE h.user_id = t.owner_id AND h.provider = 'hubspot' AND h.status {status}
                   AND (h.user_id, t.organization_id) IN (SELECT user_id, organization_id FROM zb.live_members))"""

# #123: a person whose HubSpot record belongs to a colleague is held from every send, but a task step is how the
# campaign hands them to that colleague. The standing verdict is enough: a webhook keeps it current.
TASK_FOR_OWNER = "(g.reason = 'HUBSPOT_OWNED' AND {channel} = 'hubspot_task')"

# #120 HubSpot read gate, plan side: an owner with HubSpot connected (or in error: the grant cannot be
# read) claims nothing for a person with an email unless app.crm.gate read them clear in this very
# beat. No verdict, a failed read or a block all hold the person (fail-closed); nothing is skipped.
# An opt-out of the person's other addresses (#1121) needs no arm here: dispatcher.plan runs exclude_opted_out first.
# An opt-out or an alias that commits between those two statements can still leave a claim, which GATE_SEND holds and
# the next beat's exclusion ends.
GATE_PLAN = (
    "AND NOT (coalesce(btrim(t.email), '') <> '' AND "
    + _HUBSPOT_OWNER.format(status="IN ('connected', 'error')")
    + """
         AND NOT EXISTS (SELECT 1 FROM crm_gate_checks g
                         WHERE g.owner_id = t.owner_id AND g.email = t.email_key
                           AND ((g.reason IS NULL AND g.checked_at >= %s) OR """
    + TASK_FOR_OWNER.format(channel="c.steps -> (t.next_step - 1) ->> 'channel'")
    + ')))'
)

# Gate side: whom to read in HubSpot before this beat's plan. A person a verdict already blocks is
# not read again: a cooldown leaves the block when ``hold_until`` passes, the others when a HubSpot
# webhook clears the verdict.
GATE_CANDIDATES = (
    "AND coalesce(btrim(t.email), '') <> '' AND "
    + _HUBSPOT_OWNER.format(status="= 'connected'")
    + """
     AND NOT EXISTS (SELECT 1 FROM crm_gate_checks g
                     WHERE g.owner_id = t.owner_id AND g.email = t.email_key
                       AND g.reason IS NOT NULL AND (g.hold_until IS NULL OR g.hold_until > %s))"""
)


# Send side: a claim prepared earlier (waiting for approval, or for its mailbox's turn) is not sent
# while a verdict blocks the person; a webhook may have landed the block after the claim. Our own
# table only, no HubSpot call. The claim stays queued and goes out if the block lifts.
# An opt-out of any address of the person holds the claim too (#1121), outside the address guard: a target with only a
# LinkedIn profile is the person's as well.
GATE_SEND = (
    'AND NOT ('
    + _HUBSPOT_OWNER.format(status="IN ('connected', 'error')")
    + """
         AND ((coalesce(btrim(t.email), '') <> ''
               AND EXISTS (SELECT 1 FROM crm_gate_checks g
                           WHERE g.owner_id = t.owner_id AND g.email = t.email_key
                             AND g.reason IS NOT NULL AND (g.hold_until IS NULL OR g.hold_until > %s)
                             AND NOT """
    + TASK_FOR_OWNER.format(channel='s.channel')
    + f"""))
              OR EXISTS (SELECT 1 FROM outreach_person_emails(c.project_id, t.email, t.linkedin_url) e
                         JOIN crm_gate_checks g ON g.owner_id = t.owner_id AND g.email = e.email
                         WHERE g.reason = '{gate.OPTED_OUT_REASON}')))"""
)

# The targets an opted-out verdict reaches (#1121), as enrollment reads the person (app.audience.follow.with_aliases):
# the named address in every campaign of its owner, and, in each client where a person identity holds that address,
# every target that one of the identity's addresses or LinkedIn profiles names. ``{verdicts}`` defines the CTE
# ``verdicts`` (owner_id, email, plus whatever ``{same}`` reads). Both callers run every beat, so the cost must follow
# the verdicts, not the targets. Each probe is a LATERAL subquery with OFFSET 0, which the planner cannot turn into a
# join, and the update reads the ids as one array: the planner misjudges a CTE's rows by 25 times and then hashes
# every target and alias of the database (1.4 s a beat as System for 11k opt-outs, bench/opt_outs.py).
_OPTED_OUT_REACH = """WITH {verdicts},
    aliases AS MATERIALIZED (
        SELECT v.*, k.project_id, k.kind, k.value FROM verdicts v
        CROSS JOIN LATERAL (
            SELECT k.project_id, k.kind, k.value FROM audience_aliases a
            JOIN audience_aliases k
              ON k.identity_id = a.identity_id AND k.entity = 'person' AND k.namespace = ''
             AND k.kind IN ('email', 'linkedin_url')
            WHERE a.value = v.email AND a.entity = 'person' AND a.kind = 'email' AND a.namespace = ''
            OFFSET 0) k),
    reached AS (
        SELECT r.id FROM verdicts v CROSS JOIN LATERAL (
            SELECT t.id FROM outreach_campaign_targets t
            WHERE t.email_key = v.email AND t.owner_id = v.owner_id {same}
            OFFSET 0) r
        UNION
        SELECT r.id FROM aliases v CROSS JOIN LATERAL (
            SELECT t.id FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id
            WHERE v.kind = 'email' AND t.email_key = v.value AND t.owner_id = v.owner_id {same}
              AND c.project_id = v.project_id
            OFFSET 0) r
        UNION
        SELECT r.id FROM aliases v CROSS JOIN LATERAL (
            SELECT t.id FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id
            WHERE v.kind = 'linkedin_url' AND t.linkedin_alias = v.value AND t.owner_id = v.owner_id {same}
              AND c.project_id = v.project_id
            OFFSET 0) r)
    UPDATE outreach_campaign_targets t SET status = 'excluded', paused_until = NULL
    WHERE t.id = ANY (ARRAY(SELECT id FROM reached)) AND t.status IN ('active', 'draft', 'paused')"""


def exclude_opted_out(conn) -> int:
    """An opt-out in HubSpot is for good: the person leaves every campaign of that owner, like our
    own exclusions, instead of waiting forever as a held target."""
    # The reason is a literal: the partial index's predicate must match a generic plan of a prepared statement too.
    verdicts = f"""verdicts AS MATERIALIZED (
        SELECT g.owner_id, g.email FROM crm_gate_checks g WHERE g.reason = '{gate.OPTED_OUT_REASON}')"""
    return conn.execute(
        _OPTED_OUT_REACH.format(verdicts=verdicts, same='')
        + ' AND '
        + _HUBSPOT_OWNER.format(status="IN ('connected', 'error')")
    ).rowcount


def exclude_opted_out_of_departed_owners(conn) -> int:
    """``exclude_opted_out`` for an owner who is no longer a live member of the target's organization. Holding a
    customer or a deal stops with the owner; an opt-out is the person's wish and keeps its effect. The verdict is the
    one already stored: the gate does not read a departed owner's HubSpot for this organization's people.
    Runs as ``System``: the organization's scope does not show a departed owner's verdicts (crm_gate_checks is
    zb:personal), and their grant may be disconnected by the removal.

    It runs every beat, so it starts from the few rows: the departed (owner, organization) pairs of the campaigns (a
    target has its campaign's owner and organization), then their opted-out verdicts (crm_gate_checks_opted_out_idx),
    then the targets that each verdict reaches by stored key (``_OPTED_OUT_REACH``), never a scan of every target.
    ponytail: the pairs come from a scan of every campaign, a table hundreds of times smaller than the targets."""
    # paused_until goes with the pause: the table's check refuses a pause date on any other status, and one such row
    # would fail this write, and so the whole beat, every minute.
    # The reason is a literal: the partial index's predicate must match a generic plan of a prepared statement too.
    verdicts = f"""departed AS MATERIALIZED (
            SELECT DISTINCT c.owner_id, c.organization_id FROM outreach_campaigns c
            WHERE c.owner_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM zb.live_members m
                WHERE m.user_id = c.owner_id AND m.organization_id = c.organization_id)),
        verdicts AS MATERIALIZED (
            SELECT d.owner_id, d.organization_id, g.email
            FROM departed d JOIN crm_gate_checks g ON g.owner_id = d.owner_id AND g.reason = '{gate.OPTED_OUT_REASON}')"""
    return conn.execute(
        _OPTED_OUT_REACH.format(verdicts=verdicts, same='AND t.organization_id = v.organization_id')
    ).rowcount


def due(conn, now: datetime, *, select: str, gate_sql: str) -> list[dict]:
    return conn.execute(
        DUE_SQL.format(select=select, gate=gate_sql, not_banned=_OWNER_NOT_BANNED, held=_NOT_HELD),
        # In the order of the text: the channels and the due time in ``d``, the gate's ``now``, the DM's wait, the batch.
        (list(SENDABLE_CHANNELS), now, now, now - sending.LINKEDIN_ACCEPT_WAIT, PLAN_BATCH),
    ).fetchall()
