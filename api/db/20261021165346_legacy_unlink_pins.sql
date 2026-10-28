-- Unlink and owner release must apply the same mailbox-identity rule as connect/link (#1099).
CREATE FUNCTION reconcile_legacy_pins(p_project uuid) RETURNS void LANGUAGE sql AS $$
    INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)
    SELECT DISTINCT c.id, ps.sender_id, ps.project_id, ps.provider, ps.organization_id
    FROM move_legacy_pins(p_project, NULL) m
    JOIN outreach_campaigns c ON c.id = m.campaign
    JOIN project_senders ps ON ps.project_id = c.project_id AND ps.sender_id = m.account
    WHERE m.live AND c.status NOT IN ('stopped', 'completed')
    ON CONFLICT DO NOTHING
$$;

CREATE FUNCTION unlink_sender_accounts(p_accounts uuid[]) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    clients uuid[];
    client uuid;
BEGIN
    -- Pins include clients not linked yet: a concurrent link must not escape the campaign-before-account order.
    SELECT array_agg(p.id ORDER BY p.id) INTO clients FROM projects p
    WHERE EXISTS (SELECT 1 FROM project_senders ps WHERE ps.project_id = p.id AND ps.sender_id = ANY(p_accounts))
       OR EXISTS (SELECT 1 FROM outreach_campaigns c JOIN outreach_campaign_targets t ON t.campaign_id = c.id
                  WHERE c.project_id = p.id AND t.sender_id = ANY(p_accounts));
    PERFORM 1 FROM projects WHERE id = ANY(clients) ORDER BY id FOR KEY SHARE;
    PERFORM 1 FROM outreach_campaigns WHERE project_id = ANY(clients) ORDER BY id FOR KEY SHARE;
    -- Lock twins too: two owners releasing one mailbox must not each hold a link the other's pool insert needs.
    PERFORM 1 FROM user_oauth_connections u
    WHERE u.id = ANY(p_accounts)
       OR EXISTS (SELECT 1 FROM project_senders ps WHERE ps.project_id = ANY(clients) AND ps.sender_id = u.id)
    ORDER BY u.id FOR NO KEY UPDATE;
    DELETE FROM project_senders WHERE sender_id = ANY(p_accounts);
    FOREACH client IN ARRAY coalesce(clients, '{}'::uuid[]) LOOP
        PERFORM reconcile_legacy_pins(client);
    END LOOP;
END
$$;

CREATE OR REPLACE FUNCTION release_senders_of_removed_member() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM unlink_sender_accounts(ARRAY(SELECT id FROM user_oauth_connections
        WHERE user_id = OLD."userId" AND organization_id = OLD."organizationId"));
    UPDATE user_oauth_connections
    SET status = 'disconnected', status_detail = 'owner_removed', refresh_token_encrypted = '',
        granted_scopes = '{}', updated_at = now(),
        -- A LinkedIn id is unique across organizations (#317): freed, so the account can be connected again.
        provider_account_id = CASE WHEN provider = 'unipile' THEN NULL ELSE provider_account_id END
    WHERE user_id = OLD."userId" AND organization_id = OLD."organizationId"
      -- A deleted account's memberships cascade here: its release already ran and says why.
      AND status_detail IS DISTINCT FROM 'owner_deleted';
    RETURN OLD;
END;
$$;

CREATE OR REPLACE FUNCTION release_senders_of_deleted_user() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO deleted_member_orgs (user_id, organization_id)
    SELECT OLD.id, m."organizationId" FROM member m WHERE m."userId" = OLD.id ON CONFLICT DO NOTHING;
    PERFORM unlink_sender_accounts(ARRAY(SELECT id FROM user_oauth_connections WHERE user_id = OLD.id));
    -- BEFORE the row goes, so before the SET NULL cascade clears owner_id.
    UPDATE outreach_campaigns SET former_owner_id = OLD.id WHERE owner_id = OLD.id;
    UPDATE user_oauth_connections
    SET status = 'disconnected', status_detail = 'owner_deleted', refresh_token_encrypted = '',
        granted_scopes = '{}', updated_at = now(),
        -- A LinkedIn id is unique across organizations (#317): freed, so the account can be connected again.
        provider_account_id = CASE WHEN provider = 'unipile' THEN NULL ELSE provider_account_id END
    WHERE user_id = OLD.id;
    RETURN OLD;
END;
$$;

-- ACCOUNT_DELETE and BACK_OFFICE: release moves only the pin, never the sends, and restores its campaign pool.
GRANT UPDATE (sender_id) ON outreach_campaign_targets TO zenbree_system;
GRANT INSERT ON outreach_campaign_senders TO zenbree_system;

-- A client whose legacy row was unlinked since 20261019093817 ran, with the identified account already
-- linked, still holds conversations on the row it left: linking an account it already links reconciles nothing. The
-- same rule moves them now. home_touch is off around it as in 20261019093817_legacy_mailbox_pins.sql.
-- BACKFILL
ALTER TABLE outreach_campaign_targets DISABLE TRIGGER home_touch;
SELECT reconcile_legacy_pins(id) FROM projects;
ALTER TABLE outreach_campaign_targets ENABLE TRIGGER home_touch;
