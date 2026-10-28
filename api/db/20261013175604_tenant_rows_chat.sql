-- Every tenant table stores its own organization_id (4 of 4: chat and profiles).
-- See the first file of this change for the reason, the trigger and the lock note.

ALTER TABLE chat_messages ADD COLUMN organization_id uuid;
UPDATE chat_messages SET organization_id = p.organization_id FROM chat_sessions p WHERE p.id = chat_messages.session_id;
ALTER TABLE business_profiles ADD COLUMN organization_id uuid;
UPDATE business_profiles SET organization_id = p.organization_id FROM projects p WHERE p.id = business_profiles.project_id;
ALTER TABLE exclusions ADD COLUMN organization_id uuid;
UPDATE exclusions SET organization_id = p.organization_id FROM projects p WHERE p.id = exclusions.project_id;

ALTER TABLE chat_messages ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_chat_messages_organization_id ON chat_messages(organization_id);
CREATE TRIGGER chat_messages_fill_organization_id BEFORE INSERT OR UPDATE OF session_id ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('chat_sessions', 'session_id');
ALTER TABLE business_profiles ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_business_profiles_organization_id ON business_profiles(organization_id);
CREATE TRIGGER business_profiles_fill_organization_id BEFORE INSERT OR UPDATE OF project_id ON business_profiles
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('projects', 'project_id');
ALTER TABLE exclusions ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_exclusions_organization_id ON exclusions(organization_id);
CREATE TRIGGER exclusions_fill_organization_id BEFORE INSERT OR UPDATE OF project_id ON exclusions
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('projects', 'project_id');

ALTER TABLE chat_messages DROP CONSTRAINT chat_messages_session_id_fkey,
    ADD CONSTRAINT chat_messages_session_id_org_fkey FOREIGN KEY (session_id, organization_id)
    REFERENCES chat_sessions (id, organization_id) ON DELETE CASCADE;
ALTER TABLE business_profiles DROP CONSTRAINT business_profiles_project_id_fkey,
    ADD CONSTRAINT business_profiles_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE CASCADE;
ALTER TABLE exclusions DROP CONSTRAINT exclusions_project_id_fkey,
    ADD CONSTRAINT exclusions_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE CASCADE;
