-- A mailbox row from before #307 (a legacy row: provider_account_id NULL) and the identified account of the same
-- address are one mailbox, so a thread on one is a thread on the other (#1069). A pin may move from the legacy row to
-- that account and nowhere else. A client that moved to the account before this fix keeps its conversations held on
-- a row it no longer links: the backfill moves them, by the rule that connect and link apply from now on.

-- The one form of a mailbox address that two rows are compared by.
CREATE FUNCTION mailbox_address(p_label text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT lower(btrim(coalesce(p_label, '')))
$$;

CREATE FUNCTION mailbox_twin(p_legacy uuid, p_account uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM user_oauth_connections l
        JOIN user_oauth_connections a ON a.organization_id = l.organization_id AND a.provider = l.provider
        WHERE l.id = p_legacy AND a.id = p_account
          AND l.provider IN ('google', 'microsoft')
          AND l.provider_account_id IS NULL AND a.provider_account_id IS NOT NULL
          AND mailbox_address(l.account_label) <> ''
          AND mailbox_address(a.account_label) = mailbox_address(l.account_label))
$$;

CREATE OR REPLACE FUNCTION outreach_target_pin_is_final() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.sender_pinned_at = OLD.sender_pinned_at AND mailbox_twin(OLD.sender_id, NEW.sender_id) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'outreach target %: its conversation on account % since % is final, not account % since %',
        OLD.id, OLD.sender_id, OLD.sender_pinned_at, NEW.sender_id, NEW.sender_pinned_at
        USING ERRCODE = 'integrity_constraint_violation';
END
$$;

-- A conversation on a legacy row that its client does not link goes on from the client's account of that mailbox, a
-- live one first. p_account: only to that account, the one a connect or link just gave the client, so linking
-- another account never resumes a held conversation. NULL: every client, every account. The scan starts from the
-- legacy rows, so it reads their pins only.
CREATE FUNCTION move_legacy_pins(p_project uuid, p_account uuid)
RETURNS TABLE (campaign uuid, account uuid, live boolean)
LANGUAGE sql AS $$
    UPDATE outreach_campaign_targets t
    SET sender_id = (
        SELECT a.id FROM project_senders ps JOIN user_oauth_connections a ON a.id = ps.sender_id
        WHERE ps.project_id = c.project_id AND (p_account IS NULL OR a.id = p_account)
          AND mailbox_twin(t.sender_id, a.id)
        ORDER BY a.status = 'disconnected', a.connected_at DESC NULLS LAST, a.id
        LIMIT 1)
    FROM outreach_campaigns c
    WHERE c.id = t.campaign_id AND (p_project IS NULL OR c.project_id = p_project)
      AND t.sender_id IN (SELECT u.id FROM user_oauth_connections u
                          WHERE u.provider_account_id IS NULL AND u.provider IN ('google', 'microsoft'))
      AND NOT EXISTS (SELECT 1 FROM project_senders ps WHERE ps.project_id = c.project_id AND ps.sender_id = t.sender_id)
      AND EXISTS (SELECT 1 FROM project_senders ps
                  WHERE ps.project_id = c.project_id AND (p_account IS NULL OR ps.sender_id = p_account)
                    AND mailbox_twin(t.sender_id, ps.sender_id))
    RETURNING t.campaign_id, t.sender_id, t.status IN ('active', 'paused')
$$;

-- Each live campaign of a live moved conversation pools its account, by the rule of sender_pool.sync_linked. The
-- trigger above checks every moved pin. home_touch is off as in 20261017191816_sender_accounts.sql.
ALTER TABLE outreach_campaign_targets DISABLE TRIGGER home_touch;
INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)
SELECT DISTINCT c.id, ps.sender_id, ps.project_id, ps.provider, ps.organization_id
FROM move_legacy_pins(NULL, NULL) m
JOIN outreach_campaigns c ON c.id = m.campaign
JOIN project_senders ps ON ps.project_id = c.project_id AND ps.sender_id = m.account
WHERE m.live AND c.status NOT IN ('stopped', 'completed')
ON CONFLICT DO NOTHING;
ALTER TABLE outreach_campaign_targets ENABLE TRIGGER home_touch;
