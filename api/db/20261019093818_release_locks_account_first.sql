-- A member's or a user's release locks each account row before it deletes the account's links, the order that connect,
-- link, unlink and a campaign's preselection take. Deleting the links first deadlocked with a connect or link of the
-- same mailbox, which holds the account row while it writes the client's link and pool rows (#1085). NO KEY UPDATE
-- lets a pool row's foreign key check (KEY SHARE) through, so a pool write never waits on a release.

CREATE OR REPLACE FUNCTION release_senders_of_removed_member() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM user_oauth_connections
    WHERE user_id = OLD."userId" AND organization_id = OLD."organizationId"
    ORDER BY id FOR NO KEY UPDATE;
    DELETE FROM project_senders ps USING user_oauth_connections u
    WHERE ps.sender_id = u.id AND u.user_id = OLD."userId" AND u.organization_id = OLD."organizationId";
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
    -- BEFORE the row goes, so before the SET NULL cascade clears owner_id.
    UPDATE outreach_campaigns SET former_owner_id = OLD.id WHERE owner_id = OLD.id;
    PERFORM 1 FROM user_oauth_connections WHERE user_id = OLD.id ORDER BY id FOR NO KEY UPDATE;
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
