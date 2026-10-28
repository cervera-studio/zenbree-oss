CREATE TABLE billing_profiles (
    organization_id UUID PRIMARY KEY REFERENCES organization(id) ON DELETE CASCADE,
    profile JSONB NOT NULL,
    sync_status TEXT NOT NULL CHECK (sync_status IN ('local', 'pending', 'synced')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
