"""The campaign list/detail query (``GET /v1/campaigns``, #1128).

The listed campaigns are picked first, then their sends are read once, grouped, instead of one lateral per
campaign: each lateral made the planner intersect the campaign's index with the whole organization's
(``organization_id = zb.org()`` from row-level security), a cost that grows with the agency, not the campaign.
"""

from app.outreach import sending


def _terminal_failure(alias: str = '') -> str:
    a = alias + '.' if alias else ''
    return f"({a}status = 'refused' OR ({a}status = 'failed' AND {a}attempts >= {sending.MAX_ATTEMPTS}))"


_TERMINAL_FAILURE = _terminal_failure()

_SQL = f"""
WITH picked AS MATERIALIZED (
    SELECT * FROM outreach_campaigns c WHERE c.organization_id = %s{{where}} ORDER BY c.created_at DESC LIMIT %s
), sd AS MATERIALIZED (
    SELECT campaign_id, target_id, step, status, attempts, bounce_kind
    FROM outreach_campaign_sends WHERE campaign_id IN (SELECT id FROM picked)
), sent AS (
    SELECT campaign_id,
           count(*) FILTER (WHERE status = 'sent') AS sent,
           -- Terminal only: a `failed` row with attempts left is still retried by the dispatcher. A hard bounce (#747) too.
           count(*) FILTER (WHERE {_TERMINAL_FAILURE} OR bounce_kind = 'hard') AS failed
    FROM sd GROUP BY campaign_id
), cs AS (
    SELECT DISTINCT campaign_id, target_id FROM sd WHERE status = 'sent' AND target_id IS NOT NULL
), contacted AS (
    SELECT campaign_id, count(*) AS contacted FROM cs GROUP BY campaign_id
), td AS MATERIALIZED (
    SELECT t.campaign_id, t.id, t.status, t.replied_at, t.next_step, jsonb_array_length(c.steps) AS step_count
    FROM picked c JOIN outreach_campaign_targets t ON t.campaign_id = c.id
), base AS (
    -- «Avanzamento» is how far the sequences got, not delivery: a step is done once the person is past it
    -- (sent or skipped), or it ended in a terminal failure, which leaves the person on it for good.
    -- Planned: every step for a person still in the sequence, only the done ones for one who left it.
    SELECT campaign_id, count(*) AS target_count,
           count(*) FILTER (WHERE status = 'replied' OR replied_at IS NOT NULL) AS replied,
           sum(CASE WHEN status IN ('replied', 'excluded') THEN done ELSE step_count END) AS planned_sends,
           sum(done) AS steps_done
    FROM (SELECT *, least(next_step - 1, step_count) AS done FROM td) d GROUP BY campaign_id
), stuck AS (
    -- A person whose current step ended in a terminal failure counts that step done and has left the sequence.
    SELECT t.campaign_id, count(*) AS stuck,
           sum(least(t.next_step - 1, t.step_count) + 1
               - CASE WHEN t.status IN ('replied', 'excluded') THEN least(t.next_step - 1, t.step_count) ELSE t.step_count END
           ) AS planned_delta
    FROM td t JOIN sd ON sd.campaign_id = t.campaign_id AND sd.target_id = t.id AND sd.step = t.next_step
    WHERE {_terminal_failure('sd')}
    GROUP BY t.campaign_id
), replied_contacted AS (
    -- A reply marks matching targets in one organization (an opt-out, in every one): count only contacted.
    SELECT t.campaign_id, count(*) AS replied_contacted
    FROM td t JOIN cs ON cs.campaign_id = t.campaign_id AND cs.target_id = t.id
    WHERE t.status = 'replied' OR t.replied_at IS NOT NULL GROUP BY t.campaign_id
)
SELECT c.id, c.project_id, c.owner_id, c.name, c.status, c.steps, c.fields, c.approve_before_sending, c.block_active_duplicates, c.created_at, c.activated_at,
       coalesce(b.target_count, 0) AS target_count, coalesce(b.replied, 0) AS replied,
       coalesce(rc.replied_contacted, 0) AS replied_contacted,
       coalesce(s.sent, 0) AS sent, coalesce(s.failed, 0) AS failed, coalesce(k.contacted, 0) AS contacted,
       l.name AS list_name, l.id AS list_id, l.chat_session_id,
       ARRAY(SELECT e ->> 'channel' FROM jsonb_array_elements(c.steps) e) AS step_channels,
       coalesce(b.planned_sends, 0)::int + coalesce(u.planned_delta, 0)::int AS planned_sends,
       coalesce(b.steps_done, 0)::int + coalesce(u.stuck, 0)::int AS steps_done
FROM picked c
LEFT JOIN lists l ON l.id = c.list_id AND l.organization_id = c.organization_id AND l.deleted_at IS NULL
LEFT JOIN sent s ON s.campaign_id = c.id
LEFT JOIN contacted k ON k.campaign_id = c.id
LEFT JOIN base b ON b.campaign_id = c.id
LEFT JOIN stuck u ON u.campaign_id = c.id
LEFT JOIN replied_contacted rc ON rc.campaign_id = c.id
ORDER BY c.created_at DESC"""


def campaign_query(org_id: str, *, where: str = '', params: tuple = (), limit: int = 200) -> tuple[str, tuple]:
    """The SQL and its parameters; ``where`` is an ``AND c.…`` condition on the campaign with its ``params``."""
    return _SQL.format(where=' ' + where if where else ''), (org_id, *params, limit)
