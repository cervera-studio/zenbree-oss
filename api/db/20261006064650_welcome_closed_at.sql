-- The first-access Home (#210) is over for good: dismissed, or every step done.
-- IF NOT EXISTS: local databases that loaded the pre-migration schema_onboarding.sql of this branch have it already.
ALTER TABLE business_profiles ADD COLUMN IF NOT EXISTS welcome_closed_at TIMESTAMPTZ;
