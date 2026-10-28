-- An organization's rows outlive the member who created them (#333; #331 did the same for the money tables).
-- Since #305-#308 clients, chats, lists, campaigns, the inbox, saved searches and webhooks belong to the
-- organization and user_id/owner_id only says who created them, yet deleting an account (Better Auth's
-- admin/remove-user) cascaded through them: the team lost every client, list and campaign the member made.
-- Now the creator becomes NULL ("former member" in the UI). Forward-only.
--
-- ON DELETE SET NULL (the organization's):
--   projects, business_profiles, exclusions, chat_sessions, chat_messages, jobs, lists, outreach_campaigns
--   (and targets and sends through their composite keys), outreach_inbound_messages, outreach_inbox_replies,
--   saved_searches and saved_search_runs, user_webhooks, user_oauth_connections (the sender row stays,
--   disconnected and unlinked, below), and the cost records llm_usage, vendor_calls, contact_lookups.
-- ON DELETE CASCADE (the person's), unchanged: session, account, member, invitation (sent by them),
--   user_api_keys (a key acts as its creator), user_memory_doc, notifications, notification_settings,
--   low_credit_alerts.
DO $$
DECLARE
    col RECORD;
    fk TEXT;
BEGIN
    FOR col IN SELECT * FROM (VALUES
        ('projects', 'user_id'), ('business_profiles', 'user_id'), ('exclusions', 'user_id'),
        ('chat_sessions', 'user_id'), ('chat_messages', 'user_id'), ('jobs', 'user_id'), ('lists', 'user_id'),
        ('outreach_campaigns', 'owner_id'), ('outreach_inbound_messages', 'owner_id'),
        ('outreach_inbox_replies', 'owner_id'), ('saved_searches', 'user_id'), ('saved_search_runs', 'user_id'),
        ('user_webhooks', 'user_id'), ('user_oauth_connections', 'user_id'), ('llm_usage', 'user_id'),
        ('vendor_calls', 'user_id'), ('contact_lookups', 'user_id')
    ) AS v(tbl, name)
    LOOP
        -- The baseline's inline REFERENCES: the name is Postgres's default, looked up rather than assumed.
        FOR fk IN
            SELECT c.conname FROM pg_constraint c
            WHERE c.conrelid = col.tbl::regclass AND c.contype = 'f' AND c.confrelid = '"user"'::regclass
        LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', col.tbl, fk);
        END LOOP;
        EXECUTE format('ALTER TABLE %I ALTER COLUMN %I DROP NOT NULL', col.tbl, col.name);
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES "user"(id) ON DELETE SET NULL',
            col.tbl, col.tbl || '_' || col.name || '_fkey', col.name
        );
    END LOOP;

    -- The five composite keys that repeat the creator, and only those (looked up by table and columns, never "every
    -- multi-column key"): targets and sends (campaign_id, owner_id) -> campaigns, sends (target_id, campaign_id,
    -- owner_id) -> targets, saved_searches (campaign_id, user_id) -> campaigns, saved_search_runs
    -- (saved_search_id, user_id) -> saved_searches. They are recreated below with ON UPDATE CASCADE.
    FOR fk IN
        SELECT c.conrelid::regclass || ':' || c.conname FROM pg_constraint c
        JOIN (VALUES ('outreach_campaign_targets', 'outreach_campaigns', ARRAY['campaign_id', 'owner_id']),
                     ('outreach_campaign_sends', 'outreach_campaigns', ARRAY['campaign_id', 'owner_id']),
                     ('outreach_campaign_sends', 'outreach_campaign_targets',
                      ARRAY['target_id', 'campaign_id', 'owner_id']),
                     ('saved_searches', 'outreach_campaigns', ARRAY['campaign_id', 'user_id']),
                     ('saved_search_runs', 'saved_searches', ARRAY['saved_search_id', 'user_id'])
             ) AS k(tbl, ref, cols) ON c.conrelid = k.tbl::regclass AND c.confrelid = k.ref::regclass
        WHERE c.contype = 'f'
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY u(n, i)
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = u.n ORDER BY u.i) = k.cols
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', split_part(fk, ':', 1), split_part(fk, ':', 2));
    END LOOP;
END;
$$;

ALTER TABLE outreach_campaign_targets ALTER COLUMN owner_id DROP NOT NULL;
ALTER TABLE outreach_campaign_sends ALTER COLUMN owner_id DROP NOT NULL;
ALTER TABLE outreach_campaign_targets ADD CONSTRAINT outreach_campaign_targets_campaign_id_owner_id_fkey
    FOREIGN KEY (campaign_id, owner_id) REFERENCES outreach_campaigns(id, owner_id)
    ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE outreach_campaign_sends ADD CONSTRAINT outreach_campaign_sends_campaign_id_owner_id_fkey
    FOREIGN KEY (campaign_id, owner_id) REFERENCES outreach_campaigns(id, owner_id)
    ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE outreach_campaign_sends ADD CONSTRAINT outreach_campaign_sends_target_id_campaign_id_owner_id_fkey
    FOREIGN KEY (target_id, campaign_id, owner_id) REFERENCES outreach_campaign_targets(id, campaign_id, owner_id)
    ON DELETE SET NULL (target_id) ON UPDATE CASCADE;
ALTER TABLE saved_searches ADD CONSTRAINT saved_searches_campaign_id_user_id_fkey
    FOREIGN KEY (campaign_id, user_id) REFERENCES outreach_campaigns(id, owner_id)
    ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE saved_search_runs ADD CONSTRAINT saved_search_runs_saved_search_id_user_id_fkey
    FOREIGN KEY (saved_search_id, user_id) REFERENCES saved_searches(id, user_id)
    ON DELETE CASCADE ON UPDATE CASCADE;

-- A composite key is MATCH SIMPLE: once the creator is NULL it checks nothing. These single-column keys always
-- hold, so a campaign (or saved search) still takes its targets, sends and runs with it when deleted, and a child
-- never names a campaign that does not exist. The composites stay for the owner they carry down.
ALTER TABLE outreach_campaign_targets ADD CONSTRAINT outreach_campaign_targets_campaign_id_fkey
    FOREIGN KEY (campaign_id) REFERENCES outreach_campaigns(id) ON DELETE CASCADE;
ALTER TABLE outreach_campaign_sends ADD CONSTRAINT outreach_campaign_sends_campaign_id_fkey
    FOREIGN KEY (campaign_id) REFERENCES outreach_campaigns(id) ON DELETE CASCADE;
ALTER TABLE outreach_campaign_sends ADD CONSTRAINT outreach_campaign_sends_target_id_fkey
    FOREIGN KEY (target_id) REFERENCES outreach_campaign_targets(id) ON DELETE SET NULL;
ALTER TABLE saved_searches ADD CONSTRAINT saved_searches_campaign_id_fkey
    FOREIGN KEY (campaign_id) REFERENCES outreach_campaigns(id) ON DELETE CASCADE;
ALTER TABLE saved_search_runs ADD CONSTRAINT saved_search_runs_saved_search_id_fkey
    FOREIGN KEY (saved_search_id) REFERENCES saved_searches(id) ON DELETE CASCADE;

-- One message per owner, or per client once the owner is gone: a provider retry is still the same message, and one
-- email to two organizations' client addresses is two messages.
ALTER TABLE outreach_inbound_messages DROP CONSTRAINT outreach_inbound_messages_owner_id_channel_provider_message_key;
CREATE UNIQUE INDEX outreach_inbound_messages_recipient_channel_provider_message_key
    ON outreach_inbound_messages ((coalesce(owner_id, project_id)), channel, provider_message_id);

-- Who created a campaign whose creator's account is gone (no key: the user no longer exists). Emails sent before the
-- deletion carry that id in their Reply-To and unsubscribe links: it scopes them to that member's own campaigns.
ALTER TABLE outreach_campaigns ADD COLUMN former_owner_id UUID;
CREATE INDEX idx_outreach_campaigns_former_owner ON outreach_campaigns(former_owner_id)
    WHERE former_owner_id IS NOT NULL;

-- A sender's token is the person's: when their account goes, every sender they connected is disconnected (token
-- dropped) and unlinked from its clients, so a campaign stops on NO_SENDER instead of sending as them. The row
-- stays for the sends and replies that name it. The web asks the API to revoke at the provider first
-- (POST /v1/admin/users/{id}/senders/release, best-effort); this is the guarantee when that call fails.
-- The organizations a deleted member belonged to (an opaque id, nothing else about them): where an opt-out on a
-- link they minted is recorded when none of their campaigns is left to place it.
CREATE TABLE deleted_member_orgs (
    user_id         UUID NOT NULL,
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    deleted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, organization_id)
);

CREATE FUNCTION release_senders_of_deleted_user() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO deleted_member_orgs (user_id, organization_id)
    SELECT OLD.id, m."organizationId" FROM member m WHERE m."userId" = OLD.id ON CONFLICT DO NOTHING;
    -- BEFORE the row goes, so before the SET NULL cascade clears owner_id.
    UPDATE outreach_campaigns SET former_owner_id = OLD.id WHERE owner_id = OLD.id;
    DELETE FROM project_senders ps USING user_oauth_connections u WHERE ps.sender_id = u.id AND u.user_id = OLD.id;
    UPDATE user_oauth_connections
    SET status = 'disconnected', status_detail = 'owner_deleted', refresh_token_encrypted = '',
        granted_scopes = '{}', updated_at = now(),
        -- A LinkedIn id is unique across organizations (#317): freed, so the account can be connected again.
        provider_account_id = CASE WHEN provider = 'unipile' THEN NULL ELSE provider_account_id END
    WHERE user_id = OLD.id;
    RETURN OLD;
END;
$$;
CREATE TRIGGER release_senders_of_deleted_user BEFORE DELETE ON "user"
    FOR EACH ROW EXECUTE FUNCTION release_senders_of_deleted_user();
