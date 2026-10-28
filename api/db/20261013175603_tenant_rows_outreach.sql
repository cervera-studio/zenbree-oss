-- Every tenant table stores its own organization_id (3 of 4: outreach).
-- See the first file of this change for the reason, the trigger and the lock note.
--
-- Targets and sends copy the organization of their campaign; inbound messages of their project; replies and
-- reminders of their inbound message. A link that may be NULL (target, campaign, sender) into another organization is
-- cut (NULL). The keys that carry an owner (campaign_id, owner_id) carry the organization too, and keep the single
-- (campaign_id, organization_id) key beside them: an owner of NULL (the user is gone) skips the owner key, and the
-- delete of a campaign must still reach the row.

ALTER TABLE outreach_campaign_targets ADD COLUMN organization_id uuid;
ALTER TABLE outreach_campaign_targets DISABLE TRIGGER home_touch;
UPDATE outreach_campaign_targets SET organization_id = p.organization_id FROM outreach_campaigns p WHERE p.id = outreach_campaign_targets.campaign_id;
ALTER TABLE outreach_campaign_targets ENABLE TRIGGER home_touch;
ALTER TABLE outreach_campaign_sends ADD COLUMN organization_id uuid;
ALTER TABLE outreach_campaign_sends DISABLE TRIGGER home_touch;
UPDATE outreach_campaign_sends SET organization_id = p.organization_id FROM outreach_campaigns p WHERE p.id = outreach_campaign_sends.campaign_id;
ALTER TABLE outreach_campaign_sends ENABLE TRIGGER home_touch;
ALTER TABLE outreach_inbound_messages ADD COLUMN organization_id uuid;
UPDATE outreach_inbound_messages SET organization_id = p.organization_id FROM projects p WHERE p.id = outreach_inbound_messages.project_id;
ALTER TABLE outreach_inbox_replies ADD COLUMN organization_id uuid;
UPDATE outreach_inbox_replies SET organization_id = p.organization_id FROM outreach_inbound_messages p WHERE p.id = outreach_inbox_replies.inbound_id;
ALTER TABLE outreach_reminders ADD COLUMN organization_id uuid;
UPDATE outreach_reminders SET organization_id = p.organization_id FROM outreach_inbound_messages p WHERE p.id = outreach_reminders.message_id;

-- Links to a parent of another organization.
UPDATE outreach_campaign_sends SET target_id = NULL FROM outreach_campaign_targets p
    WHERE p.id = outreach_campaign_sends.target_id AND p.organization_id <> outreach_campaign_sends.organization_id;
UPDATE outreach_campaign_sends SET sender_id = NULL FROM user_oauth_connections p
    WHERE p.id = outreach_campaign_sends.sender_id AND p.organization_id <> outreach_campaign_sends.organization_id;
UPDATE outreach_inbound_messages SET campaign_id = NULL FROM outreach_campaigns p
    WHERE p.id = outreach_inbound_messages.campaign_id AND p.organization_id <> outreach_inbound_messages.organization_id;
UPDATE outreach_inbound_messages SET target_id = NULL FROM outreach_campaign_targets p
    WHERE p.id = outreach_inbound_messages.target_id AND p.organization_id <> outreach_inbound_messages.organization_id;
UPDATE outreach_inbox_replies SET sender_id = NULL FROM user_oauth_connections p
    WHERE p.id = outreach_inbox_replies.sender_id AND p.organization_id <> outreach_inbox_replies.organization_id;

ALTER TABLE outreach_campaign_targets ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_outreach_campaign_targets_organization_id ON outreach_campaign_targets(organization_id);
CREATE TRIGGER outreach_campaign_targets_fill_organization_id BEFORE INSERT OR UPDATE OF campaign_id ON outreach_campaign_targets
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('outreach_campaigns', 'campaign_id');
ALTER TABLE outreach_campaign_sends ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_outreach_campaign_sends_organization_id ON outreach_campaign_sends(organization_id);
CREATE TRIGGER outreach_campaign_sends_fill_organization_id BEFORE INSERT OR UPDATE OF campaign_id ON outreach_campaign_sends
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('outreach_campaigns', 'campaign_id');
ALTER TABLE outreach_inbound_messages ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_outreach_inbound_messages_organization_id ON outreach_inbound_messages(organization_id);
CREATE TRIGGER outreach_inbound_messages_fill_organization_id BEFORE INSERT OR UPDATE OF project_id ON outreach_inbound_messages
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('projects', 'project_id');
ALTER TABLE outreach_inbox_replies ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_outreach_inbox_replies_organization_id ON outreach_inbox_replies(organization_id);
CREATE TRIGGER outreach_inbox_replies_fill_organization_id BEFORE INSERT OR UPDATE OF inbound_id ON outreach_inbox_replies
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('outreach_inbound_messages', 'inbound_id');
ALTER TABLE outreach_reminders ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_outreach_reminders_organization_id ON outreach_reminders(organization_id);
CREATE TRIGGER outreach_reminders_fill_organization_id BEFORE INSERT OR UPDATE OF message_id ON outreach_reminders
    FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('outreach_inbound_messages', 'message_id');

-- The parents of the keys below.
ALTER TABLE outreach_campaign_targets ADD CONSTRAINT outreach_campaign_targets_id_org_key UNIQUE (id, organization_id);
ALTER TABLE outreach_campaign_targets ADD CONSTRAINT outreach_campaign_targets_id_campaign_owner_org_key UNIQUE (id, campaign_id, owner_id, organization_id);
ALTER TABLE outreach_inbound_messages ADD CONSTRAINT outreach_inbound_messages_id_org_key UNIQUE (id, organization_id);

-- The composite keys.
ALTER TABLE outreach_campaign_targets DROP CONSTRAINT outreach_campaign_targets_campaign_id_fkey,
    ADD CONSTRAINT outreach_campaign_targets_campaign_id_org_fkey FOREIGN KEY (campaign_id, organization_id)
    REFERENCES outreach_campaigns (id, organization_id) ON DELETE CASCADE;
ALTER TABLE outreach_campaign_targets DROP CONSTRAINT outreach_campaign_targets_campaign_id_owner_id_fkey,
    ADD CONSTRAINT outreach_campaign_targets_campaign_owner_org_fkey FOREIGN KEY (campaign_id, owner_id, organization_id)
    REFERENCES outreach_campaigns (id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE outreach_campaign_sends DROP CONSTRAINT outreach_campaign_sends_campaign_id_fkey,
    ADD CONSTRAINT outreach_campaign_sends_campaign_id_org_fkey FOREIGN KEY (campaign_id, organization_id)
    REFERENCES outreach_campaigns (id, organization_id) ON DELETE CASCADE;
ALTER TABLE outreach_campaign_sends DROP CONSTRAINT outreach_campaign_sends_campaign_id_owner_id_fkey,
    ADD CONSTRAINT outreach_campaign_sends_campaign_owner_org_fkey FOREIGN KEY (campaign_id, owner_id, organization_id)
    REFERENCES outreach_campaigns (id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE outreach_campaign_sends DROP CONSTRAINT outreach_campaign_sends_target_id_fkey,
    ADD CONSTRAINT outreach_campaign_sends_target_id_org_fkey FOREIGN KEY (target_id, organization_id)
    REFERENCES outreach_campaign_targets (id, organization_id) ON DELETE SET NULL (target_id);
ALTER TABLE outreach_campaign_sends DROP CONSTRAINT outreach_campaign_sends_target_id_campaign_id_owner_id_fkey,
    ADD CONSTRAINT outreach_campaign_sends_target_owner_org_fkey FOREIGN KEY (target_id, campaign_id, owner_id, organization_id)
    REFERENCES outreach_campaign_targets (id, campaign_id, owner_id, organization_id) ON UPDATE CASCADE ON DELETE SET NULL (target_id);
ALTER TABLE outreach_campaign_sends DROP CONSTRAINT outreach_campaign_sends_sender_id_fkey,
    ADD CONSTRAINT outreach_campaign_sends_sender_id_org_fkey FOREIGN KEY (sender_id, organization_id)
    REFERENCES user_oauth_connections (id, organization_id) ON DELETE SET NULL (sender_id);
ALTER TABLE outreach_inbound_messages DROP CONSTRAINT outreach_inbound_messages_project_id_fkey,
    ADD CONSTRAINT outreach_inbound_messages_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE CASCADE;
ALTER TABLE outreach_inbound_messages DROP CONSTRAINT outreach_inbound_messages_campaign_id_fkey,
    ADD CONSTRAINT outreach_inbound_messages_campaign_id_org_fkey FOREIGN KEY (campaign_id, organization_id)
    REFERENCES outreach_campaigns (id, organization_id) ON DELETE SET NULL (campaign_id);
ALTER TABLE outreach_inbound_messages DROP CONSTRAINT outreach_inbound_messages_target_id_fkey,
    ADD CONSTRAINT outreach_inbound_messages_target_id_org_fkey FOREIGN KEY (target_id, organization_id)
    REFERENCES outreach_campaign_targets (id, organization_id) ON DELETE SET NULL (target_id);
ALTER TABLE outreach_inbox_replies DROP CONSTRAINT outreach_inbox_replies_inbound_id_fkey,
    ADD CONSTRAINT outreach_inbox_replies_inbound_id_org_fkey FOREIGN KEY (inbound_id, organization_id)
    REFERENCES outreach_inbound_messages (id, organization_id) ON DELETE CASCADE;
ALTER TABLE outreach_inbox_replies DROP CONSTRAINT outreach_inbox_replies_sender_id_fkey,
    ADD CONSTRAINT outreach_inbox_replies_sender_id_org_fkey FOREIGN KEY (sender_id, organization_id)
    REFERENCES user_oauth_connections (id, organization_id) ON DELETE SET NULL (sender_id);
ALTER TABLE outreach_reminders DROP CONSTRAINT outreach_reminders_message_id_fkey,
    ADD CONSTRAINT outreach_reminders_message_id_org_fkey FOREIGN KEY (message_id, organization_id)
    REFERENCES outreach_inbound_messages (id, organization_id) ON DELETE CASCADE;

-- Nothing references the keys that carried the owner without the organization.
ALTER TABLE outreach_campaign_targets DROP CONSTRAINT outreach_campaign_targets_id_campaign_id_owner_id_key;
ALTER TABLE outreach_campaigns DROP CONSTRAINT outreach_campaigns_id_owner_id_key;
