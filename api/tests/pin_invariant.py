"""The targets that break the pin rule of 20261017191816_sender_accounts.sql. The send claim must keep the same rule,
so the dispatcher test runs this query too (#935). A pin that moved from a legacy row to the identified account of the
same address (20261019093817_legacy_mailbox_pins.sql) keeps it: the last send names the legacy row."""

PIN_INVARIANT_SQL = """
WITH started AS (
    SELECT s.target_id, s.sender_id, s.step, coalesce(s.attempted_at, s.delivered_at, s.created_at) AS at
    FROM outreach_campaign_sends s
    WHERE s.channel = 'email' AND s.target_id IS NOT NULL
      AND (s.attempted_at IS NOT NULL OR s.status IN ('sending', 'sent') OR s.attempts > 0)
), thread AS (
    SELECT target_id,
           (array_agg(sender_id ORDER BY at DESC, step DESC) FILTER (WHERE sender_id IS NOT NULL))[1] AS last_sender
    FROM started GROUP BY target_id
)
SELECT t.id, t.sender_id, t.sender_pinned_at, th.last_sender
FROM outreach_campaign_targets t
LEFT JOIN thread th ON th.target_id = t.id
WHERE (th.target_id IS NULL) = (t.sender_pinned_at IS NOT NULL)
   OR (th.last_sender IS NOT NULL AND th.last_sender IS DISTINCT FROM t.sender_id AND NOT EXISTS (
       SELECT 1 FROM user_oauth_connections l JOIN user_oauth_connections a
         ON a.organization_id = l.organization_id AND a.provider = l.provider
       WHERE l.id = th.last_sender AND a.id = t.sender_id
         AND l.provider_account_id IS NULL AND a.provider_account_id IS NOT NULL
         AND lower(btrim(coalesce(l.account_label, ''))) <> ''
         AND lower(btrim(a.account_label)) = lower(btrim(l.account_label))))
"""
