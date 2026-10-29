-- #747 review: the Message-ID the provider gave a send (Gmail and Exchange may replace ours), the one its DSN quotes.
ALTER TABLE outreach_campaign_sends ADD COLUMN provider_message_id text CHECK (char_length(provider_message_id) <= 500);

-- A hard bounce's credit back is the project's: its monthly cap counts the email as not spent. Never below 0: a
-- refund this month of a charge made last month lowers this month's spend to nothing, not under it.
CREATE OR REPLACE FUNCTION project_month_spend(p_project_id UUID) RETURNS INT
LANGUAGE sql STABLE AS $$
    SELECT greatest(coalesce(sum(CASE WHEN type = 'refund' THEN -amount
                             WHEN status = 'released' THEN 0
                             WHEN status IN ('reserved', 'settling') THEN amount
                             ELSE coalesce(settlement_net_credits, amount) END), 0), 0)::int
    FROM credit_transactions
    WHERE project_id = p_project_id
      AND (type IN ('reservation', 'deduction') OR (type = 'refund' AND reference_type = 'email_bounce'))
      AND created_at >= date_trunc('month', now());
$$;
