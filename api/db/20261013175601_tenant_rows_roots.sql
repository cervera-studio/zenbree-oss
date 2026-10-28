-- Every tenant table stores its own organization_id (1 of 4: the root tables and the key every other table uses).
--
-- A row-level security policy is then one indexed equality, organization_id = zb.org(). A policy that reads the
-- parent row instead measured 8.6 to 100 times slower at client scale. tests/test_tenant_catalog_sql.py (R2) reads
-- the catalog and fails on a table that reaches its organization only through a parent, and on a foreign key to a
-- table with an organization_id that does not carry it.
--
-- This file: the trigger function that fills the column, a UNIQUE (id, organization_id) on each parent that lacks
-- one, and the composite keys of the six tables that already have the column. The next three files give the other
-- 18 tables the column: lists and runs, outreach, chat and profiles.
--
-- The tables are small before go-live, so these are plain, validating constraints. A validating ADD FOREIGN KEY
-- holds a lock while it scans the child table; on a large table add the key NOT VALID and run ALTER TABLE ...
-- VALIDATE CONSTRAINT as a separate step, which takes a lighter lock.

-- Sets organization_id from the parent row, so an insert that leaves it NULL (the image before this migration, a
-- test, a verify seed) takes the parent's organization, and an update of the parent column moves the row to the new
-- parent's.
-- Arguments: the parent table and the foreign key column in this table. The parent's key is its id.
-- PR 5 of this run makes DEFAULT zb.org() the normal path. This trigger stays: it costs one primary-key lookup. An
-- INSERT that names an organization is left as it is, and the composite key refuses it when the parent is in
-- another one. Only an UPDATE of the key column puts the row in the new parent's organization. A parent that is not
-- there raises the foreign key error the plain key raised.
CREATE FUNCTION fill_organization_id_from_parent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_organization uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.organization_id IS NOT NULL THEN
            RETURN NEW;
        END IF;
    -- ON UPDATE CASCADE of an owner key rewrites the column with the value it has.
    ELSIF to_jsonb(NEW) -> TG_ARGV[1] IS NOT DISTINCT FROM to_jsonb(OLD) -> TG_ARGV[1] THEN
        RETURN NEW;
    END IF;
    EXECUTE format('SELECT organization_id FROM %s WHERE id = ($1).%I', TG_ARGV[0]::regclass, TG_ARGV[1])
        INTO parent_organization USING NEW;
    IF parent_organization IS NULL THEN
        RAISE EXCEPTION 'insert or update on table "%" violates foreign key: %.% names no row of %',
            TG_TABLE_NAME, TG_TABLE_NAME, TG_ARGV[1], TG_ARGV[0] USING ERRCODE = 'foreign_key_violation';
    END IF;
    NEW.organization_id := parent_organization;
    RETURN NEW;
END $$;

-- A row keeps its own organization. When its NOT NULL project is in another one, the project moves to a project of the
-- row's organization, as #307 chose (20261006134459): the creator's oldest, else the organization's oldest; an
-- organization with no project gets the default one. Moving the row instead would show it to the members of another
-- organization and cut the links of its jobs, lists and imports. No app path makes such a row; this is for history
-- and hand-written SQL. A link that may be NULL and points into another organization is cut (NULL): it is the same
-- effect as the parent's ON DELETE SET NULL. The ledger (credit_transactions) and a job keep their organization too,
-- because moving a row moves its credits; only their links are cut.
-- The new project holds existing work, so it must not show onboarding: it gets a completed profile, as in #307.
WITH created AS (
INSERT INTO projects (organization_id, user_id, name)
SELECT DISTINCT ON (r.organization_id) r.organization_id, r.user_id, 'Il mio progetto' FROM (
    SELECT c.organization_id, c.user_id, c.created_at FROM chat_sessions c JOIN projects p ON p.id = c.project_id
        WHERE p.organization_id <> c.organization_id
    UNION ALL SELECT c.organization_id, c.user_id, c.created_at FROM lists c JOIN projects p ON p.id = c.project_id
        WHERE p.organization_id <> c.organization_id
    UNION ALL SELECT c.organization_id, c.owner_id, c.created_at FROM outreach_campaigns c
        JOIN projects p ON p.id = c.project_id WHERE p.organization_id <> c.organization_id
    UNION ALL SELECT c.organization_id, c.user_id, c.created_at FROM csv_imports c
        JOIN projects p ON p.id = c.project_id WHERE p.organization_id <> c.organization_id
) r
WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.organization_id = r.organization_id)
ORDER BY r.organization_id, r.created_at
RETURNING id, user_id
)
INSERT INTO business_profiles (project_id, user_id, completed_at, welcome_closed_at)
SELECT id, user_id, now(), now() FROM created;
UPDATE chat_sessions c SET project_id = (
    SELECT n.id FROM projects n WHERE n.organization_id = c.organization_id
    ORDER BY (n.user_id = c.user_id) IS TRUE DESC, n.created_at LIMIT 1)
FROM projects p WHERE p.id = c.project_id AND p.organization_id <> c.organization_id;
UPDATE lists c SET project_id = coalesce(
    (SELECT s.project_id FROM chat_sessions s WHERE s.id = c.chat_session_id AND s.organization_id = c.organization_id),
    (SELECT n.id FROM projects n WHERE n.organization_id = c.organization_id
      ORDER BY (n.user_id = c.user_id) IS TRUE DESC, n.created_at LIMIT 1))
FROM projects p WHERE p.id = c.project_id AND p.organization_id <> c.organization_id;
UPDATE outreach_campaigns c SET project_id = coalesce(
    (SELECT l.project_id FROM lists l WHERE l.id = c.list_id AND l.organization_id = c.organization_id),
    (SELECT n.id FROM projects n WHERE n.organization_id = c.organization_id
      ORDER BY (n.user_id = c.owner_id) IS TRUE DESC, n.created_at LIMIT 1))
FROM projects p WHERE p.id = c.project_id AND p.organization_id <> c.organization_id;
UPDATE csv_imports c SET project_id = (
    SELECT n.id FROM projects n WHERE n.organization_id = c.organization_id
    ORDER BY (n.user_id = c.user_id) IS TRUE DESC, n.created_at LIMIT 1)
FROM projects p WHERE p.id = c.project_id AND p.organization_id <> c.organization_id;
UPDATE lists SET chat_session_id = NULL FROM chat_sessions p
    WHERE p.id = lists.chat_session_id AND p.organization_id <> lists.organization_id;
UPDATE csv_imports SET chat_session_id = NULL FROM chat_sessions p
    WHERE p.id = csv_imports.chat_session_id AND p.organization_id <> csv_imports.organization_id;
UPDATE csv_imports SET list_id = NULL FROM lists p
    WHERE p.id = csv_imports.list_id AND p.organization_id <> csv_imports.organization_id;
UPDATE outreach_campaigns SET list_id = NULL FROM lists p
    WHERE p.id = outreach_campaigns.list_id AND p.organization_id <> outreach_campaigns.organization_id;
UPDATE credit_transactions SET project_id = NULL FROM projects p
    WHERE p.id = credit_transactions.project_id AND p.organization_id <> credit_transactions.organization_id;
UPDATE jobs SET chat_session_id = NULL FROM chat_sessions p
    WHERE p.id = jobs.chat_session_id AND p.organization_id <> jobs.organization_id;
UPDATE jobs SET list_id = NULL FROM lists p
    WHERE p.id = jobs.list_id AND p.organization_id <> jobs.organization_id;
UPDATE jobs SET retry_of = NULL FROM jobs p
    WHERE p.id = jobs.retry_of AND p.organization_id <> jobs.organization_id;

-- The parents of the keys below.
ALTER TABLE chat_sessions ADD CONSTRAINT chat_sessions_id_org_key UNIQUE (id, organization_id);
ALTER TABLE lists ADD CONSTRAINT lists_id_org_key UNIQUE (id, organization_id);
ALTER TABLE jobs ADD CONSTRAINT jobs_id_org_key UNIQUE (id, organization_id);
ALTER TABLE credit_transactions ADD CONSTRAINT credit_transactions_id_org_key UNIQUE (id, organization_id);
ALTER TABLE outreach_campaigns ADD CONSTRAINT outreach_campaigns_id_org_key UNIQUE (id, organization_id);
ALTER TABLE user_oauth_connections ADD CONSTRAINT user_oauth_connections_id_org_key UNIQUE (id, organization_id);
ALTER TABLE outreach_campaigns ADD CONSTRAINT outreach_campaigns_id_owner_org_key UNIQUE (id, owner_id, organization_id);

-- The composite keys.
ALTER TABLE chat_sessions DROP CONSTRAINT chat_sessions_project_id_fkey,
    ADD CONSTRAINT chat_sessions_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE CASCADE;
ALTER TABLE lists DROP CONSTRAINT lists_project_id_fkey,
    ADD CONSTRAINT lists_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE CASCADE;
ALTER TABLE lists DROP CONSTRAINT lists_chat_session_id_fkey,
    ADD CONSTRAINT lists_chat_session_id_org_fkey FOREIGN KEY (chat_session_id, organization_id)
    REFERENCES chat_sessions (id, organization_id) ON DELETE SET NULL (chat_session_id);
ALTER TABLE csv_imports DROP CONSTRAINT csv_imports_project_id_fkey,
    ADD CONSTRAINT csv_imports_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE CASCADE;
ALTER TABLE csv_imports DROP CONSTRAINT csv_imports_chat_session_id_fkey,
    ADD CONSTRAINT csv_imports_chat_session_id_org_fkey FOREIGN KEY (chat_session_id, organization_id)
    REFERENCES chat_sessions (id, organization_id) ON DELETE SET NULL (chat_session_id);
ALTER TABLE csv_imports DROP CONSTRAINT csv_imports_list_id_fkey,
    ADD CONSTRAINT csv_imports_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE SET NULL (list_id);
ALTER TABLE outreach_campaigns DROP CONSTRAINT outreach_campaigns_project_id_fkey,
    ADD CONSTRAINT outreach_campaigns_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE CASCADE;
ALTER TABLE outreach_campaigns DROP CONSTRAINT outreach_campaigns_list_id_fkey,
    ADD CONSTRAINT outreach_campaigns_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE SET NULL (list_id);
ALTER TABLE credit_transactions DROP CONSTRAINT credit_transactions_project_id_fkey,
    ADD CONSTRAINT credit_transactions_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
    REFERENCES projects (id, organization_id) ON DELETE SET NULL (project_id);
ALTER TABLE jobs DROP CONSTRAINT jobs_chat_session_id_fkey,
    ADD CONSTRAINT jobs_chat_session_id_org_fkey FOREIGN KEY (chat_session_id, organization_id)
    REFERENCES chat_sessions (id, organization_id) ON DELETE SET NULL (chat_session_id);
ALTER TABLE jobs DROP CONSTRAINT jobs_list_id_fkey,
    ADD CONSTRAINT jobs_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
    REFERENCES lists (id, organization_id) ON DELETE SET NULL (list_id);
ALTER TABLE jobs DROP CONSTRAINT jobs_retry_of_fkey,
    ADD CONSTRAINT jobs_retry_of_org_fkey FOREIGN KEY (retry_of, organization_id)
    REFERENCES jobs (id, organization_id) ON DELETE SET NULL (retry_of);
