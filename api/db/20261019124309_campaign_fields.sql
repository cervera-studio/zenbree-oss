ALTER TABLE outreach_campaigns
    ADD COLUMN fields JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(fields) = 'array');
