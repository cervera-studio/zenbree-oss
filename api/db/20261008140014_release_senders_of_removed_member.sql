-- A removed member's senders stop sending for the organization (#411). Removing a member (Better Auth's
-- organization/remove-member, or any DELETE FROM member) left their mailbox and LinkedIn linked to its clients, so
-- the team kept sending as them, and they could no longer disconnect them (their token is refused for that
-- organization). A sender's token is the person's: as release_senders_of_deleted_user does for a deleted account,
-- only for that organization, their senders there are disconnected (token dropped) and unlinked from its clients.
-- The rows stay for the sends and replies that name them. Revoking at Google and Unipile is #471.
CREATE FUNCTION release_senders_of_removed_member() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
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
CREATE TRIGGER release_senders_of_removed_member AFTER DELETE ON member
    FOR EACH ROW EXECUTE FUNCTION release_senders_of_removed_member();
