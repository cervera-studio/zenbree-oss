-- The person a target names, as stored columns with an index each (#1127). Under row-level security the planner never
-- makes lower(btrim(...)) or regexp_replace an index condition, because they are not leakproof: a query that matched
-- targets on those expressions read every target of the organization or of each campaign. An equality on a stored
-- column is an index condition.
-- email_key is NULL for a blank email. linkedin_key is NULL for a blank URL, and is the normalization of the targets'
-- unique index (outreach_campaign_targets_linkedin_key) otherwise. Adding them rewrites the table once.
ALTER TABLE outreach_campaign_targets
    ADD COLUMN email_key text GENERATED ALWAYS AS (nullif(lower(btrim(email)), '')) STORED,
    ADD COLUMN linkedin_key text GENERATED ALWAYS AS (
        CASE WHEN btrim(linkedin_url) <> ''
             THEN lower(regexp_replace(split_part(split_part(btrim(linkedin_url), '?', 1), '#', 1),
                                       '^https?://(www\.)?|^www\.|/+$', '', 'gi')) END) STORED;

CREATE INDEX outreach_campaign_targets_email_key_idx ON outreach_campaign_targets (email_key, organization_id)
    WHERE email_key IS NOT NULL;
CREATE INDEX outreach_campaign_targets_linkedin_key_idx ON outreach_campaign_targets (linkedin_key, organization_id)
    WHERE linkedin_key IS NOT NULL;
