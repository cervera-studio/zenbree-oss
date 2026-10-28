-- Every table names its tenant path: an organization_id, a NOT NULL parent that has one, or a label with a reason.
-- tests/test_tenant_catalog_sql.py reads this from the catalog. jobs is the one table that fails it: both of its
-- parents (chat_sessions, lists) are ON DELETE SET NULL, so a job whose chat and list are gone names no
-- organization.
ALTER TABLE jobs ADD COLUMN organization_id uuid REFERENCES organization(id) ON DELETE CASCADE;

-- A job names the organization of the first source below that has one. A retry follows its parent after every step,
-- whatever placed the parent. There is no default_organization() step: it takes the oldest membership, so it files a
-- job of organization B in A, and it creates an organization for a user who has none.
CREATE FUNCTION pg_temp.place_retries() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    -- A retry of a retry resolves one link per pass.
    LOOP
        UPDATE jobs j SET organization_id = r.organization_id
            FROM jobs r WHERE r.id = j.retry_of AND r.organization_id IS NOT NULL AND j.organization_id IS NULL;
        EXIT WHEN NOT FOUND;
    END LOOP;
END $$;

-- 1. The job's own ledger rows. One job in two organizations is data this migration must not guess at.
DO $$
DECLARE
    split int;
BEGIN
    SELECT count(*) INTO split FROM (
        SELECT t.reference_id FROM credit_transactions t JOIN jobs j ON j.id::text = t.reference_id
         WHERE t.reference_type = 'job'
         GROUP BY t.reference_id HAVING count(DISTINCT t.organization_id) > 1) m;
    IF split > 0 THEN
        RAISE EXCEPTION 'jobs.organization_id: % job(s) have ledger rows in more than one organization', split;
    END IF;
END $$;
UPDATE jobs j SET organization_id = t.organization_id
    FROM (SELECT DISTINCT reference_id, organization_id FROM credit_transactions WHERE reference_type = 'job') t
    WHERE t.reference_id = j.id::text;
SELECT pg_temp.place_retries();
-- 2. The chat's organization.
UPDATE jobs j SET organization_id = s.organization_id
    FROM chat_sessions s WHERE s.id = j.chat_session_id AND j.organization_id IS NULL;
SELECT pg_temp.place_retries();
-- 3. The list's organization.
UPDATE jobs j SET organization_id = l.organization_id FROM lists l WHERE l.id = j.list_id AND j.organization_id IS NULL;
SELECT pg_temp.place_retries();
-- 4. The list of a row the job charged.
UPDATE jobs j SET organization_id = c.organization_id
    FROM (SELECT DISTINCT ON (rc.job_id) rc.job_id, l.organization_id
            FROM row_charges rc JOIN list_rows lr ON lr.id = rc.row_id JOIN lists l ON l.id = lr.list_id
           WHERE rc.job_id IS NOT NULL ORDER BY rc.job_id, rc.created_at, rc.id) c
    WHERE c.job_id = j.id AND j.organization_id IS NULL;
SELECT pg_temp.place_retries();

-- What is left has no ledger row, no chat, no list, no charge and no placed retry_of parent: it never held credits.
-- A job with an open hold always has a ledger row (step 1), so no hold is left without its job. A live one is
-- failed first, so no worker runs a row that goes.
-- What happens to the columns that hold a job id:
--   row_charges.job_id, list_plans.approved_job_id, jobs.retry_of  ON DELETE SET NULL (step 4 placed every job with a charge, so none is deleted here)
--   chat_messages.job_id, credit_transactions.reference_id         no foreign key; a deleted job has no ledger row
--       (step 1) and no chat, so nothing reads either
UPDATE jobs SET status = 'failed', error = 'no organization', finished_at = now(), updated_at = now()
 WHERE organization_id IS NULL AND status IN ('queued', 'running', 'needs_input');
DELETE FROM jobs WHERE organization_id IS NULL;

ALTER TABLE jobs ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_jobs_organization_id ON jobs(organization_id);

-- The image before this migration inserts jobs with no organization_id, while migrate runs ahead of the new
-- containers and after a rollback. Fill it from the chat, else the list.
-- ponytail: drop this trigger in a later PR, once no image from before this migration can run.
CREATE FUNCTION fill_job_organization_id() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.organization_id := COALESCE(
        (SELECT organization_id FROM chat_sessions WHERE id = NEW.chat_session_id),
        (SELECT organization_id FROM lists WHERE id = NEW.list_id));
    RETURN NEW;
END $$;
CREATE TRIGGER jobs_fill_organization_id BEFORE INSERT ON jobs FOR EACH ROW
    WHEN (NEW.organization_id IS NULL) EXECUTE FUNCTION fill_job_organization_id();

-- Labels for the tables that are not organization data. The catalog test reads these:
--   zb:personal <column>: <reason>          one person's rows, across their organizations
--   zb:personal-in-org <column>: <reason>   one person's rows, each about one organization
--   zb:unscoped identity|global|cost: <reason>
COMMENT ON TABLE user_memory_doc IS 'zb:personal user_id: the memory document is the person''s, in every organization';
COMMENT ON TABLE notification_settings IS 'zb:personal user_id: one set of notification settings per person';
COMMENT ON TABLE push_subscriptions IS 'zb:personal user_id: a browser subscription belongs to the person';
COMMENT ON TABLE notifications IS 'zb:personal-in-org user_id: a person''s notifications; organization_id, when set, is the organization an item is about';
COMMENT ON TABLE crm_gate_checks IS 'zb:personal owner_id: HubSpot is one connection per user';
COMMENT ON TABLE crm_contact_writes IS 'zb:personal owner_id: HubSpot is one connection per user';
COMMENT ON TABLE crm_activity_writes IS 'zb:personal owner_id: HubSpot is one connection per user';
COMMENT ON TABLE settings IS 'zb:unscoped global: admin settings and kill switches apply to every organization';
COMMENT ON TABLE webhook_events IS 'zb:unscoped global: Stripe events, deduplicated by event id before any organization is known';
COMMENT ON TABLE llm_usage IS 'zb:unscoped cost: per-call cost record keyed by user';
COMMENT ON TABLE vendor_calls IS 'zb:unscoped cost: per-call cost record keyed by user';
COMMENT ON TABLE contact_lookups IS 'zb:unscoped cost: per-call cost record keyed by user';
COMMENT ON TABLE "user" IS 'zb:unscoped identity: Better Auth';
COMMENT ON TABLE session IS 'zb:unscoped identity: Better Auth';
COMMENT ON TABLE account IS 'zb:unscoped identity: Better Auth';
COMMENT ON TABLE verification IS 'zb:unscoped identity: Better Auth';
COMMENT ON TABLE jwks IS 'zb:unscoped identity: Better Auth';
COMMENT ON TABLE organization IS 'zb:unscoped identity: Better Auth';
COMMENT ON TABLE member IS 'zb:unscoped identity: Better Auth; the membership check reads it';
COMMENT ON TABLE invitation IS 'zb:unscoped identity: Better Auth';
