-- E13.1 HubSpot read gate (#120). Widens the baseline's provider check on user_oauth_connections.

-- A HubSpot connection is one user_oauth_connections row: the Fernet-encrypted refresh token,
-- the portal (hub_id) in provider_account_id, its domain in account_label.
ALTER TABLE user_oauth_connections DROP CONSTRAINT IF EXISTS user_oauth_connections_provider_check;
ALTER TABLE user_oauth_connections ADD CONSTRAINT user_oauth_connections_provider_check
    CHECK (provider IN ('google', 'microsoft', 'bullhorn', 'unipile', 'hubspot'));

-- The gate's last HubSpot verdict per person (app.crm.gate), keyed like the dispatcher matches a
-- target: lower(btrim(email)). ``reason`` NULL = clear; ``hold_until`` = a temporary block (cooldown)
-- that lapses on its own. The dispatcher claims nothing for a person of a HubSpot-connected owner
-- without a fresh, unblocked row here.
CREATE TABLE IF NOT EXISTS crm_gate_checks (
    owner_id   UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    email      TEXT NOT NULL CHECK (email = lower(btrim(email)) AND email <> ''),
    reason     TEXT CHECK (reason IS NULL OR char_length(reason) <= 64),
    hold_until TIMESTAMPTZ,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, email)
);

-- The connected user's own HubSpot owner id (owners API, by the granting user's email), so the gate
-- blocks only accounts owned by someone else. NULL: the user owns nothing in that portal.
ALTER TABLE user_oauth_connections ADD COLUMN IF NOT EXISTS provider_owner_id TEXT
    CHECK (provider_owner_id IS NULL OR char_length(provider_owner_id) <= 500);

-- HubSpot record ids behind a verdict: webhooks name records by id, not by email.
ALTER TABLE crm_gate_checks ADD COLUMN IF NOT EXISTS contact_id TEXT;
ALTER TABLE crm_gate_checks ADD COLUMN IF NOT EXISTS company_id TEXT;
CREATE INDEX IF NOT EXISTS crm_gate_checks_contact_idx ON crm_gate_checks(owner_id, contact_id)
    WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_gate_checks_company_idx ON crm_gate_checks(owner_id, company_id)
    WHERE company_id IS NOT NULL;

-- One HubSpot connection per user (hubspot._store upserts on it): #307 dropped the (user_id, provider) key
-- so that a member may connect several mailboxes.
CREATE UNIQUE INDEX IF NOT EXISTS user_oauth_connections_hubspot_user_key
    ON user_oauth_connections(user_id) WHERE provider = 'hubspot';
-- #307's one-row-per-account rule is for senders. A HubSpot grant is each user's own (their owner id decides
-- what the gate blocks), so two colleagues of an organization may both connect its portal.
DROP INDEX IF EXISTS user_oauth_connections_org_account_key;
CREATE UNIQUE INDEX user_oauth_connections_org_account_key
    ON user_oauth_connections(organization_id, provider, provider_account_id)
    WHERE provider_account_id IS NOT NULL AND provider <> 'hubspot';
