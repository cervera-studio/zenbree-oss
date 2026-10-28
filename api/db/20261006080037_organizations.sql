-- Organizations, invisible (#305). Every user becomes the admin of a one-person organization, and the rows
-- an organization shares (projects, chats, lists, campaigns, API keys) gain organization_id; user_id/owner_id
-- stays as who created them. Credits, billing, profile, exclusions, senders, inbox, saved searches, webhooks,
-- notifications and memory stay per user until #306/#307/#308 move them.

-- The organization of a user who has none yet is created here, so the backfill, the signup hook
-- (web/lib/auth.ts) and fill_organization_id below make the same one. Slug = the creator's id: unique and stable.
-- A user with a membership gets their oldest one.
CREATE FUNCTION default_organization(p_user_id UUID) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE
    org UUID;
BEGIN
    SELECT "organizationId" INTO org FROM member WHERE "userId" = p_user_id ORDER BY "createdAt", id LIMIT 1;
    IF org IS NOT NULL THEN
        RETURN org;
    END IF;
    INSERT INTO organization (name, slug, "createdAt")
    SELECT coalesce(nullif(btrim(u.name), ''), u.email), u.id::text, u."createdAt" FROM "user" u WHERE u.id = p_user_id
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO org;
    IF org IS NULL THEN
        -- No such user, or a concurrent call created it first (the insert waited for it to commit). Never
        -- re-adds a user to an organization that exists without them: they were removed from it.
        SELECT "organizationId" INTO org FROM member WHERE "userId" = p_user_id ORDER BY "createdAt", id LIMIT 1;
        RETURN org;
    END IF;
    INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (org, p_user_id, 'admin', now());
    RETURN org;
END;
$$;

-- No signup lands between the backfill and NOT NULL below.
LOCK TABLE "user" IN SHARE MODE;

SELECT default_organization(id) FROM "user" ORDER BY "createdAt", id;

-- Sessions open at deploy carry their org into the next JWT (web/lib/auth.ts definePayload).
UPDATE session SET "activeOrganizationId" = default_organization("userId")::text WHERE "activeOrganizationId" IS NULL;

-- An insert that names no organization (the previous image during a deploy or a rollback, which runs on this
-- schema) takes its creator's. ponytail: right while a user has one org; #308 (several orgs per user) must
-- make every insert name it, then drop these triggers.
CREATE FUNCTION fill_organization_id() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.organization_id := default_organization((to_jsonb(NEW) ->> TG_ARGV[0])::uuid);
    RETURN NEW;
END;
$$;

ALTER TABLE projects ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE projects SET organization_id = default_organization(user_id);
ALTER TABLE projects ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_projects_org ON projects(organization_id, created_at);
CREATE TRIGGER fill_organization_id BEFORE INSERT ON projects
    FOR EACH ROW WHEN (NEW.organization_id IS NULL) EXECUTE FUNCTION fill_organization_id('user_id');

ALTER TABLE chat_sessions ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE chat_sessions SET organization_id = default_organization(user_id);
ALTER TABLE chat_sessions ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_chat_sessions_org ON chat_sessions(organization_id, updated_at DESC);
CREATE TRIGGER fill_organization_id BEFORE INSERT ON chat_sessions
    FOR EACH ROW WHEN (NEW.organization_id IS NULL) EXECUTE FUNCTION fill_organization_id('user_id');

ALTER TABLE lists ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE lists SET organization_id = default_organization(user_id);
ALTER TABLE lists ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_lists_org ON lists(organization_id, created_at DESC);
CREATE TRIGGER fill_organization_id BEFORE INSERT ON lists
    FOR EACH ROW WHEN (NEW.organization_id IS NULL) EXECUTE FUNCTION fill_organization_id('user_id');

ALTER TABLE outreach_campaigns ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE outreach_campaigns SET organization_id = default_organization(owner_id);
ALTER TABLE outreach_campaigns ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_outreach_campaigns_org ON outreach_campaigns(organization_id, created_at DESC);
CREATE TRIGGER fill_organization_id BEFORE INSERT ON outreach_campaigns
    FOR EACH ROW WHEN (NEW.organization_id IS NULL) EXECUTE FUNCTION fill_organization_id('owner_id');

-- A key acts for its creator (credits, per user until #306) inside its organization.
ALTER TABLE user_api_keys ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE user_api_keys SET organization_id = default_organization(user_id);
ALTER TABLE user_api_keys ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_user_api_keys_org ON user_api_keys(organization_id);
CREATE TRIGGER fill_organization_id BEFORE INSERT ON user_api_keys
    FOR EACH ROW WHEN (NEW.organization_id IS NULL) EXECUTE FUNCTION fill_organization_id('user_id');
