-- E13.3b (#648): a watchlist can name a campaign and a score; when an account's signals reach it, the account's
-- people already in a person list of the project enter the campaign as draft people, once per signal.

ALTER TABLE signals ADD CONSTRAINT signals_id_org_key UNIQUE (id, organization_id);
-- Set in the transaction that adds the account's people: a retry or a later run finds the signal consumed.
ALTER TABLE signals ADD COLUMN enrolled_at TIMESTAMPTZ;

ALTER TABLE watchlists
    ADD COLUMN campaign_id UUID,
    ADD COLUMN campaign_threshold REAL CHECK (campaign_threshold > 0),
    ADD CONSTRAINT watchlists_campaign_id_org_fkey FOREIGN KEY (campaign_id, organization_id)
        REFERENCES outreach_campaigns (id, organization_id) ON DELETE SET NULL (campaign_id),
    ADD CONSTRAINT watchlists_campaign_needs_threshold CHECK (campaign_id IS NULL OR campaign_threshold IS NOT NULL);
CREATE INDEX watchlists_campaign ON watchlists (campaign_id) WHERE campaign_id IS NOT NULL;

ALTER TABLE watchlist_runs
    ADD COLUMN people_enrolled INTEGER NOT NULL DEFAULT 0,
    -- Accounts that reached the threshold and have no known person in a list of the project: they add nobody.
    ADD COLUMN accounts_without_people INTEGER NOT NULL DEFAULT 0,
    -- People of those accounts that a rule, the 1000-person limit or another campaign kept out.
    ADD COLUMN people_left_out INTEGER NOT NULL DEFAULT 0;

ALTER TABLE outreach_campaign_targets DROP CONSTRAINT outreach_campaign_targets_source_check,
    ADD CONSTRAINT outreach_campaign_targets_source_check
        CHECK (source IN ('onboarding', 'manual', 'csv', 'refill', 'signal')),
    ADD COLUMN signal_id UUID,
    -- The signal that brought the person in: the campaign shows it as the reason.
    ADD CONSTRAINT outreach_campaign_targets_signal_id_org_fkey FOREIGN KEY (signal_id, organization_id)
        REFERENCES signals (id, organization_id) ON DELETE SET NULL (signal_id);
CREATE INDEX outreach_campaign_targets_signal ON outreach_campaign_targets (signal_id) WHERE signal_id IS NOT NULL;
