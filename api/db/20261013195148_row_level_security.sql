-- Row-level security on every tenant table (PR 5 of the organization-scoping run).
--
-- The database half only. Every service still connects as the superuser `zenbree` (the SQL tests as `postgres`),
-- which bypasses row-level security, so nothing here changes what the app reads or writes. From PR 6 the pool runs
-- `SET LOCAL ROLE zenbree_app` and one of the enter functions below at every checkout; from then on a query that
-- forgets `organization_id = %s` returns the current organization's rows only.

-- Roles are cluster-wide, so this block runs again on every database the migrations build (each test template on
-- the shared harness cluster). Two builds can race on CREATE ROLE, which then raises unique_violation instead of
-- duplicate_object. NOLOGIN: the login role stays `zenbree`, and the pool switches with SET ROLE.
DO $$
BEGIN
    BEGIN
        CREATE ROLE zenbree_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL;
    END;
    BEGIN
        CREATE ROLE zenbree_system NOLOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL;
    END;
    -- A role that already existed keeps the attributes it was made with: set them, so a zenbree_app made with
    -- BYPASSRLS cannot hide behind "already exists". Only when one is wrong: two template builds that ALTER the same
    -- role at once can fail with "tuple concurrently updated".
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zenbree_app'
                  AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolinherit
                       OR rolreplication)) THEN
        ALTER ROLE zenbree_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zenbree_system'
                  AND (rolcanlogin OR rolsuper OR NOT rolbypassrls OR rolcreatedb OR rolcreaterole OR rolinherit
                       OR rolreplication)) THEN
        ALTER ROLE zenbree_system NOLOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    END IF;
END $$;

CREATE SCHEMA zb;
GRANT USAGE ON SCHEMA public, zb TO zenbree_app, zenbree_system;

-- The scope, as the policies read it: transaction-local settings, written only by the enter functions. NULL when
-- unset, so a transaction that entered nothing matches no row. PARALLEL SAFE (a parallel worker gets the leader's
-- settings): a function left unsafe would keep every query under a policy from a parallel plan.
-- No function in zb carries SET search_path: the planner does not inline a SQL function with a SET clause, and
-- zb.org() inlined is what lets a policy be an index condition. Every name inside is schema-qualified instead, or a
-- built-in that pg_catalog resolves first, so a temporary table or type made by zenbree_app forges nothing.
CREATE FUNCTION zb.org() RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT nullif(current_setting('zb.org_id', true), '')::uuid $$;

CREATE FUNCTION zb.actor() RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT nullif(current_setting('zb.actor_id', true), '')::uuid $$;

-- The one spelling of a live member: live_member() and NOT_BANNED in app/auth/middleware.py. member and "user" are
-- zb:unscoped, so a policy that reads this reads no other policy. A view binds its tables when it is made, so a
-- temporary table named member, which comes first in the search path, cannot stand in for public.member.
-- security_invoker: it reads as the role that queries it, not as its owner, the superuser.
CREATE VIEW zb.live_members WITH (security_invoker = true) AS
    SELECT m."userId" AS user_id, m."organizationId" AS organization_id
    FROM public.member m JOIN public."user" u ON u.id = m."userId"
    WHERE u.banned IS NOT TRUE OR (u."banExpires" IS NOT NULL AND u."banExpires" < now());
GRANT SELECT ON zb.live_members TO zenbree_app, zenbree_system;

CREATE FUNCTION zb.is_live_member(p_user uuid, p_org uuid) RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT EXISTS (SELECT 1 FROM zb.live_members l WHERE l.user_id = p_user AND l.organization_id = p_org)
$$;

-- A person in an organization. A user who left or is banned gets SQLSTATE ZB401, which PR 6 turns into
-- AUTH.NOT_A_MEMBER. Returns the member's role.
CREATE FUNCTION zb.enter_member(p_org uuid, p_user uuid) RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    member_role text;
BEGIN
    SELECT m.role INTO member_role FROM public.member m
    WHERE m."userId" = p_user AND m."organizationId" = p_org AND zb.is_live_member(p_user, p_org);
    IF member_role IS NULL THEN
        RAISE EXCEPTION 'user % is not a live member of organization %', p_user, p_org USING ERRCODE = 'ZB401';
    END IF;
    PERFORM set_config('zb.org_id', p_org::text, true), set_config('zb.actor_id', p_user::text, true);
    RETURN member_role;
END $$;

-- The system acting inside one organization, for no person.
CREATE FUNCTION zb.enter_org(p_org uuid) RETURNS void LANGUAGE sql AS $$
    SELECT set_config('zb.org_id', coalesce(p_org::text, ''), true), set_config('zb.actor_id', '', true)
$$;

REVOKE EXECUTE ON FUNCTION zb.enter_member(uuid, uuid), zb.enter_org(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zb.enter_member(uuid, uuid), zb.enter_org(uuid) TO zenbree_app, zenbree_system;

-- The policy a table's shape calls for, as pg_get_expr prints it (so zb.scope can tell an unchanged policy). NULL
-- when the table has no shape: it stays closed, and test_tenant_catalog_sql.py names it.
-- The person policies read zb.live_members in an uncorrelated IN: the planner runs it once per query as a hashed
-- subplan. zb.is_live_member(<col>, ...) there would run once per row, as it is not inlined (it holds a sublink):
-- on 10,000 users, a read of every notification_settings row took 77 ms instead of 2.5 ms, and the bell of a
-- person with 1,000 notifications 14 ms instead of 1.4 ms.
--   zb:private <col>          <col> = zb.actor(): only the person (user_memory_doc)
--   zb:personal <col>         the person, or any live member of the current organization: a teammate's
--                             notification settings, a renewal notice from a beat task
--   zb:personal-in-org <col>  the person, in every organization where they are a live member (today's bell
--                             lists them all; #743 is open on that choice); anyone else's, in the current one
--   organization_id column    organization_id = zb.org()
CREATE FUNCTION zb.policy_of(t regclass) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE
    label text := coalesce(obj_description(t, 'pg_class'), '');
    kind text := substring(label FROM '^zb:(private|personal|personal-in-org) [a-z_]+:');
    col text := substring(label FROM '^zb:(?:private|personal|personal-in-org) ([a-z_]+):');
    has_org boolean := EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = t AND attname = 'organization_id'
                                AND atttypid = 'uuid'::regtype AND NOT attisdropped);
    teammate text;
BEGIN
    IF kind IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = t AND attname = col
                          AND atttypid = 'uuid'::regtype AND NOT attisdropped) THEN
            RETURN NULL;
        END IF;
        teammate := format(E'(%I IN ( SELECT l.user_id\n   FROM zb.live_members l\n  WHERE (l.organization_id = zb.org())))',
                           col);
        RETURN CASE kind
            WHEN 'private' THEN format('(%I = zb.actor())', col)
            WHEN 'personal' THEN format('((%I = zb.actor()) OR %s)', col, teammate)
            WHEN 'personal-in-org' THEN CASE WHEN has_org THEN format(
                E'(((%I = zb.actor()) AND (organization_id IN ( SELECT l.organization_id\n   FROM zb.live_members l\n'
                 '  WHERE (l.user_id = zb.actor())))) OR ((organization_id = zb.org()) AND %s))', col, teammate) END
        END;
    END IF;
    IF label ~ '^zb:' THEN  -- unscoped, or a label of no known kind
        RETURN NULL;
    END IF;
    RETURN CASE WHEN has_org THEN '(organization_id = zb.org())' END;
END $$;

-- Gives a relation its row-level security, policy and insert default, from its catalog facts. Changes nothing when
-- they are already right, so a COMMENT or CREATE TRIGGER that calls it takes no stronger lock than it took itself
-- (a VALIDATE CONSTRAINT stays non-blocking). A migration that changes a table's columns calls it again:
-- SELECT zb.scope('t').
--   zb:unscoped label     row-level security off: Better Auth tables, settings, cost tables
--   a shape (above)       row-level security on and one policy, zb_scope, for every command
--   no shape              row-level security on and no policy: zenbree_app reads 0 rows and writes nothing
--   materialized view     cannot hold a policy: zenbree_app may read it only when it is labeled
--   view                  security_invoker, or it would read its tables as its owner, the superuser
CREATE FUNCTION zb.scope(t regclass) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    rel pg_class%ROWTYPE;
    label text := coalesce(obj_description(t, 'pg_class'), '');
    wanted text;
    existing record;
    has_policy boolean;
    filled boolean;
    org_default text;
    guard text := current_setting('zb.scoping', true);
    -- The only relations with organization_id that may carry a label. test_tenant_catalog_sql.py keeps its
    -- PERSONAL_IN_ORG in step with this line.
    personal_in_org CONSTANT text[] := ARRAY['notifications'];
BEGIN
    SELECT * INTO rel FROM pg_class WHERE oid = t;
    IF rel.relnamespace <> 'public'::regnamespace OR rel.relname = 'schema_migrations' THEN
        RETURN;
    END IF;
    -- Every label replaces the organization rule: zb:unscoped turns row-level security off and keeps the grants,
    -- zb:personal and zb:private drop organization_id = zb.org() from the policy, and zb:personal-in-org lets the
    -- person write their own rows into any of their organizations. On organization data each one opens other
    -- organizations' rows, so fail the COMMENT that tries it. notifications is the reviewed exception (#743).
    IF label ~* '^\s*zb' AND rel.relkind IN ('r', 'p', 'm')
       AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = t AND attname = 'organization_id' AND NOT attisdropped)
       AND NOT (label ~ '^zb:personal-in-org ' AND rel.relname = ANY (personal_in_org))
    THEN
        RAISE EXCEPTION '% has organization_id, so its rows are an organization''s and it cannot carry %: the only '
                        'label allowed on organization data is zb:personal-in-org, on %',
                t, substring(btrim(label) FROM '^\S*'), array_to_string(personal_in_org, ', ')
            USING HINT = format('Remove the label: COMMENT ON TABLE %s IS NULL', t);
    END IF;
    -- The DDL below fires zb.on_ddl again; it returns at once while this is set.
    PERFORM set_config('zb.scoping', 'on', true);
    IF rel.relkind = 'v' THEN
        IF NOT coalesce('security_invoker=true' = ANY (rel.reloptions), false) THEN
            EXECUTE format('ALTER VIEW %s SET (security_invoker = true)', t);
        END IF;
    ELSIF rel.relkind = 'm' THEN
        IF label ~ '^zb:unscoped (identity|global|cost):' THEN
            EXECUTE format('GRANT SELECT ON %s TO zenbree_app', t);
        ELSE
            EXECUTE format('REVOKE ALL ON %s FROM zenbree_app', t);
        END IF;
    ELSIF rel.relkind IN ('r', 'p') THEN
        IF label ~ '^zb:unscoped (identity|global|cost):' THEN
            IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = t AND polname = 'zb_scope') THEN
                EXECUTE format('DROP POLICY zb_scope ON %s', t);
            END IF;
            IF rel.relrowsecurity THEN
                EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', t);
            END IF;
        ELSE
            IF NOT rel.relrowsecurity THEN
                EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
            END IF;
            wanted := zb.policy_of(t);
            SELECT p.polpermissive, p.polcmd, p.polroles, pg_get_expr(p.polqual, p.polrelid) AS qual,
                   pg_get_expr(p.polwithcheck, p.polrelid) AS checks
              INTO existing FROM pg_policy p WHERE p.polrelid = t AND p.polname = 'zb_scope';
            has_policy := FOUND;
            IF wanted IS NULL THEN
                IF has_policy THEN
                    EXECUTE format('DROP POLICY zb_scope ON %s', t);
                END IF;
            ELSIF NOT (has_policy AND existing.polpermissive AND existing.polcmd = '*' AND existing.polroles = '{0}'
                       AND existing.qual IS NOT DISTINCT FROM wanted
                       AND existing.checks IS NOT DISTINCT FROM wanted) THEN
                IF has_policy THEN
                    EXECUTE format('DROP POLICY zb_scope ON %s', t);
                END IF;
                EXECUTE format('CREATE POLICY zb_scope ON %s USING %s WITH CHECK %s', t, wanted, wanted);
            END IF;
            -- An insert that leaves organization_id out takes the scope's organization, except where a fill
            -- trigger takes the parent's: a column default applies before a BEFORE trigger, so it would skip
            -- the parent lookup. The parent wins. The parentheses keep out the person policies' subquery term,
            -- l.organization_id = zb.org(), which names the view's column, not the table's.
            IF wanted IS NOT NULL AND wanted LIKE '%(organization_id = zb.org())%' THEN
                filled := EXISTS (SELECT 1 FROM pg_trigger tg JOIN pg_proc p ON p.oid = tg.tgfoid
                                  WHERE tg.tgrelid = t AND NOT tg.tgisinternal
                                    AND p.proname ~ '^fill_.*organization_id');
                SELECT pg_get_expr(d.adbin, d.adrelid) INTO org_default
                  FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                 WHERE d.adrelid = t AND a.attname = 'organization_id';
                IF filled AND org_default = 'zb.org()' THEN
                    EXECUTE format('ALTER TABLE %s ALTER COLUMN organization_id DROP DEFAULT', t);
                ELSIF NOT filled AND org_default IS NULL THEN
                    EXECUTE format('ALTER TABLE %s ALTER COLUMN organization_id SET DEFAULT zb.org()', t);
                END IF;
            END IF;
        END IF;
    END IF;
    PERFORM set_config('zb.scoping', coalesce(guard, ''), true);
END $$;

-- What the app may do with a new relation. The five secret tables get nothing: Better Auth's keys, OAuth tokens,
-- sessions and verification codes, and the migration ledger. Called once per relation, when it is created, so a
-- later migration that narrows a grant is not undone.
CREATE FUNCTION zb.grant(t regclass) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    rel pg_class%ROWTYPE;
    guard text := current_setting('zb.scoping', true);
BEGIN
    SELECT * INTO rel FROM pg_class WHERE oid = t;
    IF rel.relnamespace <> 'public'::regnamespace
       OR rel.relname IN ('jwks', 'account', 'session', 'verification', 'schema_migrations') THEN
        RETURN;
    END IF;
    PERFORM set_config('zb.scoping', 'on', true);
    IF rel.relkind = 'S' THEN
        EXECUTE format('GRANT USAGE, SELECT, UPDATE ON SEQUENCE %s TO zenbree_app, zenbree_system', t);
    ELSIF rel.relkind IN ('r', 'p', 'v') THEN
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO zenbree_app, zenbree_system', t);
    ELSIF rel.relkind = 'm' THEN
        EXECUTE format('GRANT SELECT ON %s TO zenbree_system', t);  -- zenbree_app: zb.scope decides
    END IF;
    PERFORM set_config('zb.scoping', coalesce(guard, ''), true);
END $$;

-- New relations are closed whatever command makes them: CREATE TABLE, CREATE TABLE AS, SELECT INTO, a partition,
-- a view or a materialized view. No tag filter, so a command added in a later Postgres is not missed. A label set
-- by COMMENT ON TABLE after the CREATE, and a fill trigger added after it, re-derive the table. Other ALTERs do
-- not: a migration that changes a table's columns calls zb.scope itself, and the catalog test says so. Temporary
-- tables and other schemas are left alone.
CREATE FUNCTION zb.on_ddl() RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
    cmd record;
BEGIN
    IF current_setting('zb.scoping', true) = 'on' THEN
        RETURN;
    END IF;
    FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
        IF cmd.classid = 'pg_class'::regclass AND cmd.objsubid = 0 AND cmd.schema_name = 'public' THEN
            IF cmd.command_tag LIKE 'CREATE %' OR cmd.command_tag = 'SELECT INTO' THEN
                PERFORM zb.grant(cmd.objid::regclass);
                PERFORM zb.scope(cmd.objid::regclass);
            ELSIF cmd.command_tag = 'COMMENT' THEN
                PERFORM zb.scope(cmd.objid::regclass);
            END IF;
        ELSIF cmd.classid = 'pg_trigger'::regclass AND cmd.command_tag = 'CREATE TRIGGER' THEN
            PERFORM zb.scope(tg.tgrelid::regclass) FROM pg_trigger tg WHERE tg.oid = cmd.objid;
        END IF;
    END LOOP;
END $$;

REVOKE EXECUTE ON FUNCTION zb.policy_of(regclass), zb.scope(regclass), zb.grant(regclass), zb.on_ddl() FROM PUBLIC;

-- Act on 3 of the interrogate: a teammate may read and write a person's notification settings, push subscriptions
-- and HubSpot caches; the memory document stays the person's alone.
COMMENT ON TABLE user_memory_doc IS
    'zb:private user_id: the memory document is the person''s, in every organization, and no teammate reads it';

SELECT zb.grant(c.oid) FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm', 'S');
SELECT zb.scope(c.oid) FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm');

CREATE EVENT TRIGGER zb_on_ddl ON ddl_command_end EXECUTE FUNCTION zb.on_ddl();

-- The unscoped tables hold no organization's rows, so the grant is the whole limit: only what api/app does with
-- each (read in its SQL on 2026-10-13), as the role that does it. zenbree_system runs the back office (the kill
-- switches in settings), the public routes (the Stripe webhook's webhook_events) and the beat (llm_usage
-- retention); zenbree_app runs a person's requests and tasks. invitation is Better Auth's and the API never reads it.
-- A row lock (FOR UPDATE, FOR NO KEY UPDATE) needs UPDATE on one column, so "user" and organization grant one
-- column each, not the table: a table-wide UPDATE would let zenbree_app set any user's role or ban. On
-- organization the column is id, which every foreign key to it refuses to change (ON UPDATE NO ACTION).
REVOKE ALL ON member, organization, "user", invitation, settings, webhook_events, llm_usage, vendor_calls,
    contact_lookups FROM zenbree_app, zenbree_system;
GRANT SELECT, INSERT ON member TO zenbree_app, zenbree_system;          -- own_organization(), default_organization()
GRANT SELECT, INSERT, UPDATE (id) ON organization TO zenbree_app, zenbree_system;  -- the same, and row locks
GRANT SELECT, UPDATE (locale) ON "user" TO zenbree_app, zenbree_system;           -- agent/language.py, row locks
GRANT SELECT ON settings TO zenbree_app;                                -- the kill switches, read
GRANT SELECT, INSERT, UPDATE, DELETE ON settings TO zenbree_system;     -- and written by the back office
GRANT SELECT, INSERT, UPDATE ON webhook_events TO zenbree_system;       -- Stripe event dedupe, a public route
GRANT SELECT, INSERT ON llm_usage TO zenbree_app;                       -- metering
GRANT SELECT, INSERT, DELETE ON llm_usage TO zenbree_system;            -- and retention
GRANT SELECT, INSERT ON vendor_calls, contact_lookups TO zenbree_app, zenbree_system;

-- HubSpot is one grant per person across organizations (design question 3). hubspot._store moves the row to the
-- organization of the latest connect, so the organization policy alone would show it in that organization only.
-- Permissive, so it widens zb_scope. The read is open to a teammate and to ForOrg, because a campaign of the
-- organization needs its owner's grant: outreach/routes.py activate, the opt-out rules of crm/gate.py, the send
-- gates of outreach/due.py. The write is the owner's alone: only the owner may write the row in, or move it to, an
-- organization other than zb.org(), and only one where they are a live member. Anyone else writes through zb_scope,
-- which keeps the row in zb.org(). So the HubSpot workers (gate, token refresh, sweeps) write as Member(_, owner)
-- or System, never ForOrg. The live members come from an uncorrelated IN, as in the person policies: with
-- zb.is_live_member(user_id, zb.org()) a read that leaves the filtering to the policy called it on every HubSpot
-- row (74 ms instead of 5 ms on 10,000 grants). The WITH CHECK keeps the function: it runs once per written row.
CREATE POLICY zb_hubspot_per_user ON user_oauth_connections
    USING (provider = 'hubspot' AND (user_id = zb.actor() OR user_id IN (SELECT l.user_id FROM zb.live_members l
                                                                          WHERE l.organization_id = zb.org())))
    WITH CHECK (provider = 'hubspot' AND user_id = zb.actor() AND zb.is_live_member(user_id, organization_id));
COMMENT ON POLICY zb_hubspot_per_user ON user_oauth_connections IS
    'zb:extra: a HubSpot grant is its user''s in every organization they belong to (design question 3)';

-- The read above also lets an UPDATE or DELETE reach the row, since both act only on the rows USING shows, so a
-- teammate could delete the owner's grant stored in another organization, or move it into theirs. Restrictive, so
-- it is ANDed with the permissive policies. For UPDATE, USING tests the row before and, with no WITH CHECK, the row
-- after. Two policies with one expression, because a FOR ALL policy would also hide the read that a campaign needs.
--   provider <> 'hubspot'        mailboxes and LinkedIn accounts are the organization's: zb_scope alone decides
--   user_id = zb.actor()         the owner reconnects, refreshes or disconnects their grant wherever it sits
--   organization_id = zb.org()   anyone else reaches only a grant stored in the current organization, as zb_scope
--                                alone would allow
-- A policy filters, so a teammate's UPDATE or DELETE of a grant stored in another organization changes 0 rows and
-- raises no error. A locking read (FOR UPDATE, FOR SHARE) applies the UPDATE policies too: of a grant stored in
-- another organization, it returns 0 rows to anyone but the owner, under enter_member and enter_org alike, where a
-- plain SELECT returns the grant. A caller that is not the owner and reads "no row" as "not connected" hides a
-- wrong scope.
CREATE POLICY zb_hubspot_owner_updates ON user_oauth_connections AS RESTRICTIVE FOR UPDATE
    USING (provider <> 'hubspot' OR user_id = zb.actor() OR organization_id = zb.org());
CREATE POLICY zb_hubspot_owner_deletes ON user_oauth_connections AS RESTRICTIVE FOR DELETE
    USING (provider <> 'hubspot' OR user_id = zb.actor() OR organization_id = zb.org());
COMMENT ON POLICY zb_hubspot_owner_updates ON user_oauth_connections IS
    'zb:narrows: only the owner writes a HubSpot grant stored outside zb.org()';
COMMENT ON POLICY zb_hubspot_owner_deletes ON user_oauth_connections IS
    'zb:narrows: only the owner deletes a HubSpot grant stored outside zb.org()';

-- A HubSpot grant belongs to one person, so no update changes whose it is. A WITH CHECK sees only the new row, and
-- inside zb.org() a teammate passes every policy, so this compares the old row with the new one. No code in api/app
-- changes user_id or provider on a HubSpot row (project_senders.attach adopts a mailbox, never a grant).
--   user_id                  SET user_id = <teammate> would hand them the owner's refresh token. It may become NULL:
--                            the foreign key's ON DELETE SET NULL, when the owner deletes their account. An orphan
--                            is not adopted
--   provider                 a teammate could turn the grant into a mailbox, change its user_id, and turn it back
CREATE FUNCTION zb.keep_hubspot_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.provider IS DISTINCT FROM OLD.provider THEN
        RAISE EXCEPTION 'connection %: a HubSpot grant stays a HubSpot grant, and no other connection becomes one',
                OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.user_id IS DISTINCT FROM OLD.user_id AND NEW.user_id IS NOT NULL THEN
        RAISE EXCEPTION 'connection %: a HubSpot grant belongs to the user who connected it', OLD.id
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
COMMENT ON FUNCTION zb.keep_hubspot_owner() IS
    'zb:guard: the restrictive policies cannot compare the old row with the new one, so a teammate in the grant''s organization could take it over';
CREATE TRIGGER zb_keep_hubspot_owner BEFORE UPDATE ON user_oauth_connections
    FOR EACH ROW WHEN (OLD.provider = 'hubspot' OR NEW.provider = 'hubspot') EXECUTE FUNCTION zb.keep_hubspot_owner();

-- Trigger functions run under the caller's policies. Today only the web, as the superuser, deletes member and
-- "user" rows, so these see every organization. Under zenbree_app they would reach only zb.org()'s rows.
COMMENT ON FUNCTION release_senders_of_removed_member() IS
    'zb:runs-as-caller: the web deletes member rows as the superuser; this releases the senders of every organization the member left';
COMMENT ON FUNCTION release_senders_of_deleted_user() IS
    'zb:runs-as-caller: the web deletes "user" rows as the superuser; this releases the senders and campaigns of every organization';
