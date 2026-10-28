-- Projects carry the client (#307). The business profile and exclusions move from the user to the project;
-- campaigns and inbound messages gain project_id. user_id stays as who created a row.
-- A sender (mailbox, LinkedIn account) stays ONE physical row per account, now of an organization, keyed by the
-- provider's verified identity of the account (provider_account_id): one refresh token (Microsoft rotates it), one
-- daily cap, one inbound subscription. A member may hold several accounts of one provider (one per client).
-- Projects use them through project_senders. Backfill: the profile, exclusions and senders were a user's, in their own
-- organization (own_organization(), #306), so they go ONLY to that organization's projects the user created:
-- profile and exclusions copied, each sender linked, so nothing stops sending. Never into a team organization the
-- user joined (#308): its members would send from a personal mailbox and read a personal profile.
-- Forward-only: the previous image's upserts (ON CONFLICT (user_id, ...)) fail on this schema.

-- A user with a profile, exclusions or a sender who created no project yet gets the default one in their own
-- organization (own_organization(), #306: the one that held their sender and wallet), as default_project()
-- (app/projects/routes.py) makes on first use.
INSERT INTO projects (organization_id, user_id, name)
SELECT own_organization(u.id), u.id, 'Il mio progetto' FROM "user" u
WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.user_id = u.id AND p.organization_id = own_organization(u.id))
  AND (EXISTS (SELECT 1 FROM user_oauth_connections c WHERE c.user_id = u.id)
       OR EXISTS (SELECT 1 FROM business_profiles b WHERE b.user_id = u.id)
       OR EXISTS (SELECT 1 FROM exclusions e WHERE e.user_id = u.id));
INSERT INTO projects (organization_id, user_id, name)
SELECT DISTINCT ON (r.organization_id) r.organization_id, r.user_id, 'Il mio progetto' FROM (
    SELECT organization_id, owner_id AS user_id, created_at FROM outreach_campaigns
    UNION ALL SELECT organization_id, user_id, created_at FROM chat_sessions WHERE project_id IS NULL
    UNION ALL SELECT organization_id, user_id, created_at FROM lists WHERE project_id IS NULL
) r
WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.organization_id = r.organization_id)
ORDER BY r.organization_id, r.created_at;

-- ── chats and lists always have a project now (#307: its exclusions and cap apply; a NULL would skip them) ──
UPDATE chat_sessions s SET project_id = (
    SELECT p.id FROM projects p WHERE p.organization_id = s.organization_id
    ORDER BY p.user_id = s.user_id DESC, p.created_at LIMIT 1)
WHERE s.project_id IS NULL;
UPDATE lists l SET project_id = coalesce(
    (SELECT s.project_id FROM chat_sessions s WHERE s.id = l.chat_session_id AND s.organization_id = l.organization_id),
    (SELECT p.id FROM projects p WHERE p.organization_id = l.organization_id
      ORDER BY p.user_id = l.user_id DESC, p.created_at LIMIT 1))
WHERE l.project_id IS NULL;
ALTER TABLE chat_sessions ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE lists ALTER COLUMN project_id SET NOT NULL;

-- ── campaigns: the project of their list, else of their owner in their organization ──
ALTER TABLE outreach_campaigns ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
UPDATE outreach_campaigns c SET project_id = coalesce(
    (SELECT l.project_id FROM lists l WHERE l.id = c.list_id AND l.organization_id = c.organization_id),
    (SELECT p.id FROM projects p WHERE p.organization_id = c.organization_id
      ORDER BY p.user_id = c.owner_id DESC, p.created_at LIMIT 1));
ALTER TABLE outreach_campaigns ALTER COLUMN project_id SET NOT NULL;
CREATE INDEX idx_outreach_campaigns_project ON outreach_campaigns(project_id, created_at DESC);

-- ── business profile: one per project ──
ALTER TABLE business_profiles DROP CONSTRAINT business_profiles_pkey;
ALTER TABLE business_profiles ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
INSERT INTO business_profiles (project_id, user_id, website, description, profile, segments, completed_at,
                               created_at, updated_at, memory_entry, exclusion_lines, welcome_closed_at)
SELECT p.id, b.user_id, b.website, b.description, b.profile, b.segments, b.completed_at,
       b.created_at, b.updated_at, b.memory_entry, b.exclusion_lines, b.welcome_closed_at
FROM business_profiles b JOIN projects p ON p.user_id = b.user_id AND p.organization_id = own_organization(b.user_id)
WHERE b.project_id IS NULL;
DELETE FROM business_profiles WHERE project_id IS NULL;
-- A user from before onboarding had no profile and skipped it by having a chat: their projects stay onboarded.
INSERT INTO business_profiles (project_id, user_id, completed_at, welcome_closed_at)
SELECT p.id, p.user_id, now(), now() FROM projects p
WHERE NOT EXISTS (SELECT 1 FROM business_profiles b WHERE b.project_id = p.id)
  AND EXISTS (SELECT 1 FROM chat_sessions s WHERE s.user_id = p.user_id);
ALTER TABLE business_profiles ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE business_profiles ADD PRIMARY KEY (project_id);
CREATE INDEX idx_business_profiles_user ON business_profiles(user_id);

-- ── exclusions: per project ──
ALTER TABLE exclusions DROP CONSTRAINT exclusions_user_id_kind_value_key;
ALTER TABLE exclusions ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
INSERT INTO exclusions (project_id, user_id, kind, value, source, created_at)
SELECT p.id, e.user_id, e.kind, e.value, e.source, e.created_at
FROM exclusions e JOIN projects p ON p.user_id = e.user_id AND p.organization_id = own_organization(e.user_id)
WHERE e.project_id IS NULL;
DELETE FROM exclusions WHERE project_id IS NULL;
ALTER TABLE exclusions ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE exclusions ADD CONSTRAINT exclusions_project_kind_value_key UNIQUE (project_id, kind, value);

-- ── senders: one physical row per account in an organization; projects link to it ──
ALTER TABLE user_oauth_connections ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE user_oauth_connections SET organization_id = own_organization(user_id);
ALTER TABLE user_oauth_connections ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE user_oauth_connections
    ADD CONSTRAINT user_oauth_connections_id_provider_org_key UNIQUE (id, provider, organization_id);
ALTER TABLE projects ADD CONSTRAINT projects_id_org_key UNIQUE (id, organization_id);

-- The account a send or an inbox answer went out through: the daily and LinkedIn limits count by it. Derived here
-- while a user still had one connection per provider (the campaign's or message's owner's); rows it cannot name
-- (a connection deleted since) stay NULL and count for no account, as they are older than any limit window.
ALTER TABLE outreach_campaign_sends ADD COLUMN sender_id UUID REFERENCES user_oauth_connections(id) ON DELETE SET NULL;
ALTER TABLE outreach_inbox_replies ADD COLUMN sender_id UUID REFERENCES user_oauth_connections(id) ON DELETE SET NULL;
UPDATE outreach_campaign_sends s SET sender_id = u.id FROM user_oauth_connections u
WHERE u.user_id = s.owner_id
  AND u.provider = CASE WHEN s.channel = 'email' THEN s.sender_provider ELSE 'unipile' END;
UPDATE outreach_inbox_replies r SET sender_id = u.id FROM user_oauth_connections u
WHERE u.user_id = r.owner_id AND u.provider = r.sender_provider;
CREATE INDEX outreach_campaign_sends_sender_idx ON outreach_campaign_sends(sender_id, attempted_at)
    WHERE status IN ('sending', 'sent');
CREATE INDEX outreach_inbox_replies_sender_idx ON outreach_inbox_replies(sender_id, created_at)
    WHERE status IN ('sending', 'sent');
-- One row per connector and provider no longer: a member may connect several accounts of a provider.
ALTER TABLE user_oauth_connections DROP CONSTRAINT IF EXISTS user_oauth_connections_user_id_provider_key;

-- At most one sender per provider per project (a target is pinned to a provider). The composite keys keep a
-- link inside one organization and its provider equal to the sender's.
CREATE TABLE project_senders (
    project_id      UUID NOT NULL,
    sender_id       UUID NOT NULL,
    provider        TEXT NOT NULL,
    organization_id UUID NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, provider),
    UNIQUE (project_id, sender_id),
    FOREIGN KEY (project_id, organization_id) REFERENCES projects(id, organization_id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id, provider, organization_id)
        REFERENCES user_oauth_connections(id, provider, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_project_senders_sender ON project_senders(sender_id);
INSERT INTO project_senders (project_id, sender_id, provider, organization_id)
SELECT DISTINCT ON (p.id, u.provider) p.id, u.id, u.provider, u.organization_id
FROM user_oauth_connections u JOIN projects p ON p.user_id = u.user_id AND p.organization_id = u.organization_id
ORDER BY p.id, u.provider, u.connected_at DESC NULLS LAST, u.created_at DESC, u.id;

-- An account is its provider-verified identity: Google `sub`, Microsoft `id` (oid), the Unipile account id. A
-- mailbox connected before #307 stored none (NULL until its next connect, which records it); a LinkedIn account
-- always had its Unipile id. Two rows of one account in an organization (two connectors, possible only without
-- #317's index) merge into the most recently connected one: it keeps its token and connector, takes over the
-- other rows' project links and sends, and the others go.
CREATE TEMP TABLE sender_merge ON COMMIT DROP AS
SELECT id, first_value(id) OVER (
    PARTITION BY organization_id, provider, provider_account_id
    ORDER BY connected_at DESC NULLS LAST, created_at DESC, id
) AS keeper
FROM user_oauth_connections WHERE provider_account_id IS NOT NULL;
DELETE FROM sender_merge WHERE id = keeper;
UPDATE project_senders ps SET sender_id = m.keeper FROM sender_merge m
WHERE ps.sender_id = m.id
  AND NOT EXISTS (SELECT 1 FROM project_senders k WHERE k.project_id = ps.project_id AND k.sender_id = m.keeper);
UPDATE outreach_campaign_sends s SET sender_id = m.keeper FROM sender_merge m WHERE s.sender_id = m.id;
UPDATE outreach_inbox_replies r SET sender_id = m.keeper FROM sender_merge m WHERE r.sender_id = m.id;
DELETE FROM user_oauth_connections u USING sender_merge m WHERE u.id = m.id;
CREATE UNIQUE INDEX user_oauth_connections_org_account_key
    ON user_oauth_connections(organization_id, provider, provider_account_id)
    WHERE provider_account_id IS NOT NULL;
-- #317: Unipile webhooks route by the account id alone, so a LinkedIn account stays one row across organizations.
-- A mailbox may serve two organizations (each its own row, token and cap).
DROP INDEX IF EXISTS idx_user_oauth_connections_provider_account;
CREATE UNIQUE INDEX idx_user_oauth_connections_provider_account
    ON user_oauth_connections(provider, provider_account_id)
    WHERE provider_account_id IS NOT NULL AND provider = 'unipile';

-- ── inbound messages: their campaign's project, else their owner's sender's, else the owner's oldest ──
ALTER TABLE outreach_inbound_messages ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
UPDATE outreach_inbound_messages m SET project_id = coalesce(
    (SELECT c.project_id FROM outreach_campaigns c WHERE c.id = m.campaign_id),
    (SELECT ps.project_id FROM user_oauth_connections u JOIN project_senders ps ON ps.sender_id = u.id
      JOIN projects p ON p.id = ps.project_id
      WHERE u.user_id = m.owner_id AND (u.provider = 'unipile') = (m.channel = 'linkedin')
      ORDER BY p.created_at LIMIT 1),
    (SELECT p.id FROM projects p WHERE p.organization_id = own_organization(m.owner_id)
      ORDER BY p.user_id = m.owner_id DESC, p.created_at LIMIT 1));
ALTER TABLE outreach_inbound_messages ALTER COLUMN project_id SET NOT NULL;
CREATE INDEX outreach_inbound_messages_project_idx ON outreach_inbound_messages(project_id, received_at DESC);

-- ── monthly credit cap per project: `hard` refuses a hold past it, `soft` only reports it ──
ALTER TABLE projects
    ADD COLUMN credit_cap INT CHECK (credit_cap > 0),
    ADD COLUMN credit_cap_mode TEXT CHECK (credit_cap_mode IN ('hard', 'soft')),
    ADD CONSTRAINT projects_credit_cap_mode CHECK ((credit_cap IS NULL) = (credit_cap_mode IS NULL));
-- The project a hold spends for. NULL: older holds, and spend outside a project (admin adjustments).
ALTER TABLE credit_transactions ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE SET NULL;
CREATE INDEX idx_credit_tx_project_created ON credit_transactions(project_id, created_at) WHERE project_id IS NOT NULL;

-- Credits a project spent this calendar month (UTC): its holds still open count whole, a released one nothing, a
-- settled one what it kept. ponytail: UTC month; per-org billing periods or time zones if customers ask.
CREATE FUNCTION project_month_spend(p_project_id UUID) RETURNS INT
LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(CASE WHEN status = 'released' THEN 0
                             WHEN status IN ('reserved', 'settling') THEN amount
                             ELSE coalesce(settlement_net_credits, amount) END), 0)::int
    FROM credit_transactions
    WHERE project_id = p_project_id AND type IN ('reservation', 'deduction')
      AND created_at >= date_trunc('month', now());
$$;

-- What a hard cap still allows this month; NULL without a hard cap.
CREATE FUNCTION project_cap_left(p_project_id UUID) RETURNS INT
LANGUAGE sql STABLE AS $$
    SELECT greatest(credit_cap - project_month_spend(id), 0) FROM projects
    WHERE id = p_project_id AND credit_cap_mode = 'hard';
$$;

-- reserve_credits for a project's spend: NULL (nothing held) past a hard cap or for a project of another
-- organization. The project row lock makes concurrent holds of one project take turns, so two cannot both pass
-- the cap. A NULL project is a plain hold.
CREATE FUNCTION reserve_project_credits(
    p_project_id      UUID,
    p_organization_id UUID,
    p_user_id         UUID,
    p_amount          INT,
    p_reference_type  TEXT DEFAULT NULL,
    p_reference_id    TEXT DEFAULT NULL,
    p_tool_name       TEXT DEFAULT NULL,
    p_session_id      UUID DEFAULT NULL,
    p_description     TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    cap       INT;
    cap_mode  TEXT;
    rid       UUID;
BEGIN
    IF p_project_id IS NOT NULL THEN
        SELECT credit_cap, credit_cap_mode INTO cap, cap_mode FROM projects
            WHERE id = p_project_id AND organization_id = p_organization_id FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF cap_mode = 'hard' AND project_month_spend(p_project_id) + p_amount > cap THEN
            RETURN NULL;
        END IF;
    END IF;
    rid := reserve_credits(p_organization_id, p_user_id, p_amount, p_reference_type, p_reference_id,
                           p_tool_name, p_session_id, p_description);
    IF rid IS NOT NULL AND p_project_id IS NOT NULL THEN
        UPDATE credit_transactions SET project_id = p_project_id WHERE id = rid;
    END IF;
    RETURN rid;
END;
$$;
