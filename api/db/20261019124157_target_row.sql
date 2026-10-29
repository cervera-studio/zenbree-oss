ALTER TABLE outreach_campaign_targets
    ADD COLUMN row_id UUID,
    ADD CONSTRAINT outreach_campaign_targets_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE SET NULL (row_id);
CREATE INDEX outreach_campaign_targets_row ON outreach_campaign_targets (row_id) WHERE row_id IS NOT NULL;
