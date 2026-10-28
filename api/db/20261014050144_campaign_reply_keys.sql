-- A deleted campaign leaves the key its reply address routes by (#764 review, B1).
--
-- A campaign's mail names the campaign in its Reply-To and List-Unsubscribe mailto (app/core/reply_address.py).
-- Deleting a client deletes its campaigns, sent ones included (DELETE /v1/projects/{id}), and an opt-out mailed to
-- such an address must still stop the person everywhere (#460). email_scope (app/outreach/reply_targets.py) reads
-- this row when the campaign is gone and matches the person as the campaign's old owner-form address would.
--
-- reply_key is coalesce(owner_id, former_owner_id, project_id), the key email_scope reads from a live campaign: the
-- creator, else the creator who deleted their account since (#333), both of which outlive a client delete. The
-- client is the last resort and resolves nobody once it is gone, as its old address would not.
--
-- Rows are never removed: an opt-out mailto has no expiry, and a row is one per campaign deleted. An
-- organization delete removes its rows. A draft keeps one too: the ranking can file a reply under a draft,
-- and the inbox answer then mints that draft's campaign address (#764 review, B2).
-- Nothing to backfill: before this migration no sent mail named a campaign, so a campaign deleted earlier left no
-- address that needs one.
CREATE TABLE outreach_campaign_reply_keys (
    campaign_id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    reply_key UUID NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_outreach_campaign_reply_keys_org ON outreach_campaign_reply_keys(organization_id);

-- In an organization delete the organization row is gone before its campaigns: a row naming it would fail the
-- foreign key at the end of the statement and with it the delete.
CREATE FUNCTION keep_campaign_reply_key() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO outreach_campaign_reply_keys (campaign_id, organization_id, reply_key)
    SELECT OLD.id, OLD.organization_id, coalesce(OLD.owner_id, OLD.former_owner_id, OLD.project_id)
    WHERE EXISTS (SELECT 1 FROM organization WHERE id = OLD.organization_id);
    RETURN OLD;
END $$;

CREATE TRIGGER keep_reply_key AFTER DELETE ON outreach_campaigns
    FOR EACH ROW EXECUTE FUNCTION keep_campaign_reply_key();
