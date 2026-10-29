ALTER TABLE instantly_leads ADD COLUMN payload JSONB CHECK (jsonb_typeof(payload) = 'object');
