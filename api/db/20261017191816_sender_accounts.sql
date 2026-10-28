-- Several mailbox accounts per client, rotated per campaign (#935): the schema and the backfill.

-- UNIQUE (project_id, provider) stays until every reader keys a client's sender by id.
ALTER TABLE project_senders DROP CONSTRAINT project_senders_pkey;
ALTER TABLE project_senders DROP CONSTRAINT project_senders_project_id_sender_id_key;
ALTER TABLE project_senders ADD PRIMARY KEY (project_id, sender_id);
ALTER TABLE project_senders ADD CONSTRAINT project_senders_project_provider_key UNIQUE (project_id, provider);
ALTER TABLE project_senders ADD CONSTRAINT project_senders_link_key UNIQUE (project_id, sender_id, organization_id);

ALTER TABLE outreach_campaigns ADD CONSTRAINT outreach_campaigns_id_project_org_key
    UNIQUE (id, project_id, organization_id);

ALTER TABLE outreach_campaign_targets
    ADD COLUMN sender_id uuid,
    ADD COLUMN sender_pinned_at timestamptz,
    ADD CONSTRAINT outreach_campaign_targets_sender_id_org_fkey FOREIGN KEY (sender_id, organization_id)
        REFERENCES user_oauth_connections (id, organization_id) ON DELETE SET NULL (sender_id),
    ADD CONSTRAINT outreach_campaign_targets_sender_pinned_check
        CHECK (sender_id IS NULL OR sender_pinned_at IS NOT NULL);
CREATE INDEX outreach_campaign_targets_sender_idx ON outreach_campaign_targets(sender_id, status)
    WHERE sender_id IS NOT NULL;

-- A Gmail or Outlook thread lives in one mailbox, so a thread moved to another mailbox loses it.
-- ON DELETE SET NULL (sender_id) runs as an UPDATE that fires this trigger, so a NULL account passes.
CREATE FUNCTION outreach_target_pin_is_final() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'outreach target %: its conversation on account % since % is final, not account % since %',
        OLD.id, OLD.sender_id, OLD.sender_pinned_at, NEW.sender_id, NEW.sender_pinned_at
        USING ERRCODE = 'integrity_constraint_violation';
END
$$;
CREATE TRIGGER outreach_target_pin_is_final BEFORE UPDATE OF sender_id, sender_pinned_at ON outreach_campaign_targets
    FOR EACH ROW WHEN (OLD.sender_pinned_at IS NOT NULL AND (
        NEW.sender_pinned_at IS DISTINCT FROM OLD.sender_pinned_at
        OR (NEW.sender_id IS NOT NULL AND NEW.sender_id IS DISTINCT FROM OLD.sender_id)))
    EXECUTE FUNCTION outreach_target_pin_is_final();

-- A conversation started when a claim took an email send. A rate limit gives the attempt back but keeps attempted_at
-- and sender_id, so it counts. The pin is the account the person heard from last; with none named, the client's link
-- of the provider, which the next send would have used. tests/pin_invariant.py holds the same rule.
-- home_touch is off so that the backfill sends no home notification for each pinned row.
ALTER TABLE outreach_campaign_targets DISABLE TRIGGER home_touch;
WITH started AS (
    SELECT s.target_id, s.sender_id, s.sender_provider, s.step,
           coalesce(s.attempted_at, s.delivered_at, s.created_at) AS at
    FROM outreach_campaign_sends s
    WHERE s.channel = 'email' AND s.target_id IS NOT NULL
      AND (s.attempted_at IS NOT NULL OR s.status IN ('sending', 'sent') OR s.attempts > 0)
), thread AS (
    SELECT target_id, min(at) AS pinned_at,
           (array_agg(sender_id ORDER BY at DESC, step DESC) FILTER (WHERE sender_id IS NOT NULL))[1] AS last_sender,
           (array_agg(sender_provider ORDER BY at DESC, step DESC)
                FILTER (WHERE sender_provider IS NOT NULL))[1] AS last_provider
    FROM started GROUP BY target_id
)
UPDATE outreach_campaign_targets t
SET sender_pinned_at = th.pinned_at,
    sender_id = coalesce(th.last_sender, (
        SELECT ps.sender_id FROM project_senders ps JOIN outreach_campaigns c ON c.project_id = ps.project_id
        WHERE c.id = t.campaign_id AND ps.provider = coalesce(t.sender_provider, th.last_provider)))
FROM thread th
WHERE th.target_id = t.id AND t.sender_pinned_at IS NULL;
ALTER TABLE outreach_campaign_targets ENABLE TRIGGER home_touch;

CREATE TABLE outreach_campaign_senders (
    campaign_id     uuid NOT NULL,
    sender_id       uuid NOT NULL,
    project_id      uuid NOT NULL,
    provider        text NOT NULL CHECK (provider IN ('google', 'microsoft')),
    organization_id uuid NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, sender_id),
    CONSTRAINT outreach_campaign_senders_campaign_fkey FOREIGN KEY (campaign_id, project_id, organization_id)
        REFERENCES outreach_campaigns (id, project_id, organization_id) ON DELETE CASCADE,
    -- ON UPDATE CASCADE: until a client links several accounts of one provider, attach and link replace the account
    -- by updating project_senders.sender_id, and the pool follows it as the provider pool did.
    CONSTRAINT outreach_campaign_senders_link_fkey FOREIGN KEY (project_id, sender_id, organization_id)
        REFERENCES project_senders (project_id, sender_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT outreach_campaign_senders_sender_fkey FOREIGN KEY (sender_id, provider, organization_id)
        REFERENCES user_oauth_connections (id, provider, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_outreach_campaign_senders_link ON outreach_campaign_senders(project_id, sender_id);
CREATE INDEX idx_outreach_campaign_senders_sender ON outreach_campaign_senders(sender_id, campaign_id);
CREATE INDEX idx_outreach_campaign_senders_organization_id ON outreach_campaign_senders(organization_id);

-- Any status: a disconnect keeps the link and a reconnect keeps the id, so the conversations resume.
INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)
WITH live_thread_accounts AS (
    SELECT t.campaign_id, t.sender_id, CASE WHEN t.sender_pinned_at IS NULL THEN t.sender_provider END AS provider
    FROM outreach_campaign_targets t
    WHERE t.status IN ('active', 'paused')
)
SELECT c.id, ps.sender_id, ps.project_id, ps.provider, ps.organization_id
FROM outreach_campaigns c
JOIN project_senders ps ON ps.project_id = c.project_id AND ps.provider IN ('google', 'microsoft')
WHERE ps.provider = ANY(c.sender_pool)
   OR EXISTS (SELECT 1 FROM live_thread_accounts l
              WHERE l.campaign_id = c.id AND (l.sender_id = ps.sender_id OR l.provider = ps.provider));
