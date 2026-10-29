"""Every table states its tenant path (R1 of the organization-scoping run).

A table has a tenant path when it has a NOT NULL organization_id, is labeled zb:private, zb:personal or
zb:personal-in-org, or has a NOT NULL single-column foreign key to a table that has one. A zb:unscoped table is labeled,
but it is never a parent: a child of "user", settings or llm_usage names no organization. A table that fits none of
these is labeled in its table comment with a reason, so it cannot be added by accident:

    zb:private <col>: <why>             one person's rows, that no teammate reads
    zb:personal <col>: <why>            one person's rows, across their organizations
    zb:personal-in-org <col>: <why>     one person's rows, each in one organization
    zb:unscoped identity|global|cost: <why>

R2: a table with a tenant path, other than a labeled one, stores its own organization_id (NOT NULL), and every foreign
key from it to a table that has an organization_id is composite: (fk_col, organization_id) REFERENCES parent(id,
organization_id). A policy is then one indexed equality, organization_id = zb.org(), and no row can name a parent of
another organization. A foreign key that carries more columns, such as an owner, carries organization_id as well.

R3: on a table with a tenant path that is not labeled zb:personal or zb:personal-in-org, a foreign key to "user" with
ON DELETE CASCADE deletes organization data when its creator's account goes (#333). It needs a constraint comment
`zb:creator-cascade: <why>`, the reason the rows should go with the person.

R4: on the same tables, a unique constraint or unique index with a column that references "user" and no
organization_id is one key per person across organizations, or a second member cannot have a row (#473). It needs an
index comment `zb:per-user: <why>`. Only key columns count (not INCLUDE columns). An expression is read as the
parse tree in pg_index.indexprs, where a column is a `:varattno <attnum>` node, so a string literal or a column of the
same name cannot match. An expression that reads organization_id, such as (organization_id IS NULL), counts as
containing it. An exclusion constraint (contype x) on a user column is a key too: the reason sits on the constraint's
index (COMMENT ON INDEX <constraint name>).

A reason's kind must match its object: a constraint takes zb:creator-cascade:, an index takes zb:per-user:. A comment
that starts with zb: and is not the kind of its object fails.

A partition follows its parent: a constraint cloned from the parent (conparentid) and an index of a partition are not
read, so a reason on the partitioned table covers every partition.

R1 reads relkinds r (table), p (partitioned table) and m (materialized view); R3 and R4 read only the tables R1 finds
with a tenant path, so r and p. A relation made by CREATE TABLE AS or SELECT INTO has relkind r, not m. A materialized
view cannot hold a key or a row-level security policy, so its organization_id never makes a tenant path: R1 fails it
until it carries a label, and R5 refuses a label on one with an organization_id. Such a view has no allowed form: use a
view, which runs with security_invoker.

R5: every relation carries the row-level security its shape calls for (migration 20261013195148_row_level_security.sql
installs it, and its event trigger installs it on every new relation). A table labeled zb:unscoped has it off and no
policy. Every other table has it on and one policy, zb_scope, for every command and every role, with the same USING and
WITH CHECK expression:

    no label, an organization_id    organization_id = zb.org()
    zb:private <col>                <col> = zb.actor()
    zb:personal <col>               <col> = zb.actor() OR <col> IN (live members of zb.org())
    zb:personal-in-org <col>        (<col> = zb.actor() AND organization_id IN (the actor's live organizations))
                                    OR (organization_id = zb.org() AND <col> IN (live members of zb.org()))

The person reads their own rows of every organization where they are a live member (the bell, #743); anyone else's
stay inside zb.org(). The live members come from the view zb.live_members in an uncorrelated IN, which the planner
runs once per query, not from zb.is_live_member(), which runs once per row. The expected text is built here, not read from zb.policy_of(), so a wrong derivation fails.

Every label replaces the organization rule, so no label sits on organization data: a relation with an
organization_id column, or a table that reaches an organization through a parent (a walk like R1's, seeded only by
organization_id). zb:unscoped turns row-level security off and keeps the grants; zb:personal and zb:private drop
organization_id = zb.org(); zb:personal-in-org lets the person write their own rows into any of their organizations.
The one exception is zb:personal-in-org on the relations of PERSONAL_IN_ORG (notifications), which zb.scope holds
too. zb.scope refuses the label on a relation with organization_id when it is set; only this rule sees a path through
a parent. A zb:unscoped label on any table with a tenant path fails as well, as before. Which relations carry a label
at all is a hard list, LABELLED: a new label is a reviewed change to this file.

A second policy changes what a role sees, so only the policies in EXTRA_POLICIES (permissive: they widen zb_scope)
and RESTRICTIVE_POLICIES (restrictive: they narrow it) may exist. A permissive one carries a policy comment
`zb:extra: <why>`, a restrictive one `zb:narrows: <why>`. A restrictive policy is checked for its command and its text
as well, because dropping it or recreating it as `USING (true)` widens access. Adding to either list needs the owner's
approval in the PR. A table under organization_id = zb.org() has DEFAULT
zb.org() unless a fill trigger takes the parent's organization: a default applies before a BEFORE trigger, so it
would skip the parent. A materialized view cannot hold a policy, so zenbree_app may read it only when it is labeled; a
view runs with security_invoker, or it reads its tables as its owner: in public and in zb (zb.live_members).

R6: a trigger function runs under the policies of the role that fired it. A trigger on member or "user" that writes a
tenant table reaches every organization as the superuser and only zb.org()'s rows as zenbree_app, so it needs a
function comment `zb:runs-as-caller: <why>`. The writes are read from the function's source (INSERT INTO, UPDATE,
DELETE FROM a table name), so a write through another function or dynamic SQL is not seen.

R7: zenbree_app has no privilege on Better Auth's keys, OAuth tokens, sessions and verification codes, nor on the
migration ledger: not on the table, not on a column, and not through PUBLIC. A role it is a member of is R9's: R7 reads
has_table_privilege, which counts a membership only when the grant carries INHERIT, and zenbree_app is NOINHERIT.

R8: a trigger that row-level security depends on, because a policy cannot compare the row before an UPDATE with the
row after it, is in GUARD_TRIGGERS: it exists, it fires in its normal state (tgenabled O), and its function carries
`zb:guard: <why>`. Dropping it, disabling it, or enabling it REPLICA or ALWAYS (which change when it fires) fails here.

R9: no privilege or object runs around the policies. It reads every schema but pg_catalog, information_schema,
pg_toast and the temporary ones: a view, a trigger, a column default or a policy reaches a function by its OID, and
Postgres checks no USAGE on the function's schema then, so a schema the app cannot use is no limit.
- no function is SECURITY DEFINER, outside DEFINER_FUNCTIONS: Postgres lets PUBLIC execute a new function, and it runs
  as its owner, the superuser, which no policy limits;
- no relation has a rule, outside RULES, except the SELECT rule of a view or a materialized view: a rule's action runs
  with the rights of the table's owner, so its reads and writes skip the policies;
- zenbree_app, zenbree_system and PUBLIC hold at most SELECT, INSERT, UPDATE and DELETE on a relation in public or a
  view in zb (USAGE, SELECT and UPDATE on a sequence in public), SELECT, INSERT and UPDATE on a column of one, USAGE
  on public and zb, and nothing on any other relation, column or schema. TRUNCATE ignores row-level security;
  TRIGGER, REFERENCES, MAINTAIN and CREATE are not what the app does. Row-level security, R1 and R5 cover public
  only (zb.scope, the event trigger), and R5 checks the views of zb, so nothing else may be reachable. A foreign table
  holds another server's rows, which no policy here limits, so it gets nothing in public either. A privilege
  zenbree_system needs beyond these is in SYSTEM_PRIVILEGES with its reason;
- no default privilege, of any role, in any schema or none, gives the two roles or PUBLIC more than zb.grant gives a
  new relation: the four on a table and USAGE, SELECT and UPDATE on a sequence, in public only, to the two roles only.
  EXECUTE on functions and USAGE on types are Postgres's own defaults for PUBLIC. Without this the problem shows only
  once a migration makes a table;
- no parameter privilege (SET or ALTER SYSTEM) goes to either role or PUBLIC: SET session_replication_role = replica
  turns the guard triggers of R8 off;
- neither role owns a relation, a function or a schema: an owner skips the policies of its tables;
- neither role is a member of another role, directly or through one: SET ROLE pg_read_all_data, or zenbree_system,
  reads every organization. A login role made a member of zenbree_app (design question 7) is the other direction.

R1 to R4 and the labels read public only, and that is enough: a table elsewhere gets no policy, but R9 leaves the app
no privilege on it. R5 reads public and the views of zb, the only places R9 allows a grant. R6 reads the triggers on
member and "user" whatever schema their function is in; R7 reads the five named tables and R8 the named triggers, so
their scope is their list. Role memberships and parameter privileges are cluster-wide catalogs; default privileges
are read in full, including the entries for no schema.

The catalog is read like test_foreign_key_indexes_sql.py does, so a new table is checked with no edit here.
"""

import os
import shutil
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import COPY_PREFIX
from tests.test_agent import db as db
from tests.test_agent import needs_db

pytestmark = needs_db

LABEL = r'^zb:(private [a-z_]+|personal [a-z_]+|personal-in-org [a-z_]+|unscoped (identity|global|cost)):'

# Tables with a path, from the tables that name one outright (an organization_id or a personal label), then every
# table with a NOT NULL single-column foreign key to one already in the set. An unscoped label ends the search at that
# table: it is checked below, but it seeds nothing. schema_migrations is the runner's own bookkeeping.
PATHS = r"""
WITH RECURSIVE base AS (
  SELECT c.oid, c.relname FROM pg_class c
  WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'm') AND c.relname <> 'schema_migrations'
), roots AS (
  SELECT b.oid FROM base b
  WHERE EXISTS (SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = b.oid AND a.attname = 'organization_id' AND NOT a.attisdropped AND a.attnotnull)
     OR coalesce(obj_description(b.oid, 'pg_class'), '') ~ '^zb:(private|personal|personal-in-org) [a-z_]+:'
), parents(oid) AS (
  SELECT oid FROM roots
  UNION
  SELECT c.conrelid FROM pg_constraint c
  JOIN parents p ON p.oid = c.confrelid
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
  WHERE c.contype = 'f' AND cardinality(c.conkey) = 1 AND a.attnotnull
)
"""

TABLES_WITHOUT_A_TENANT_PATH = rf"""{PATHS}
SELECT b.oid::regclass::text AS "table" FROM base b
WHERE b.oid NOT IN (SELECT oid FROM parents)
  AND coalesce(obj_description(b.oid, 'pg_class'), '') !~ '{LABEL}'
ORDER BY 1
"""

# R2. A labeled table is exempt: its label says it is not organization data, or not one organization's.
UNLABELED = rf"""{PATHS}, unlabeled AS (
  SELECT b.oid FROM base b WHERE b.oid IN (SELECT oid FROM parents)
    AND coalesce(obj_description(b.oid, 'pg_class'), '') !~ '{LABEL}'
)"""

TABLES_WITHOUT_AN_ORGANIZATION_ID = rf"""{UNLABELED}
SELECT u.oid::regclass::text AS "table",
       k.parent, k.fk
FROM unlabeled u
LEFT JOIN LATERAL (SELECT c.confrelid::regclass::text AS parent, a.attname AS fk
                     FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                    WHERE c.conrelid = u.oid AND c.contype = 'f' AND cardinality(c.conkey) = 1 AND a.attnotnull
                      AND c.confrelid IN (SELECT oid FROM parents) ORDER BY c.conname LIMIT 1) k ON true
WHERE NOT EXISTS (SELECT 1 FROM pg_attribute a
                  WHERE a.attrelid = u.oid AND a.attname = 'organization_id' AND a.attnotnull AND NOT a.attisdropped)
ORDER BY 1
"""

# A key is composite on the organization when the position of the child's organization_id in conkey holds the parent's
# organization_id in confkey. Column order is part of the key, so it is compared by position.
FOREIGN_KEYS_WITHOUT_THE_ORGANIZATION = rf"""{UNLABELED}, keys AS (
  SELECT c.*, 
         (SELECT a.attnum FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attname = 'organization_id'
             AND NOT a.attisdropped) AS child_org,
         (SELECT a.attnum FROM pg_attribute a WHERE a.attrelid = c.confrelid AND a.attname = 'organization_id'
             AND NOT a.attisdropped) AS parent_org
  FROM pg_constraint c WHERE c.contype = 'f' AND c.conrelid IN (SELECT oid FROM unlabeled)
)
SELECT k.conrelid::regclass::text || ' (' || cols.names || ') -> ' || k.confrelid::regclass::text AS "key",
       format('%s%s',
         CASE WHEN EXISTS (SELECT 1 FROM pg_constraint u WHERE u.conrelid = k.confrelid AND u.contype IN ('u', 'p')
                           AND u.conkey = k.confkey || k.parent_org)
              THEN '' ELSE format('ALTER TABLE %s ADD UNIQUE (%s, organization_id); ', k.confrelid::regclass,
                                  refcols.names) END,
         format('ALTER TABLE %s DROP CONSTRAINT %I, ADD FOREIGN KEY (%s, organization_id) REFERENCES %s (%s, '
                'organization_id)%s%s;', k.conrelid::regclass, k.conname, cols.names, k.confrelid::regclass,
                refcols.names,
                CASE k.confupdtype WHEN 'c' THEN ' ON UPDATE CASCADE' WHEN 'n' THEN ' ON UPDATE SET NULL'
                                   WHEN 'd' THEN ' ON UPDATE SET DEFAULT' WHEN 'r' THEN ' ON UPDATE RESTRICT'
                                   ELSE '' END,
                CASE k.confdeltype WHEN 'c' THEN ' ON DELETE CASCADE'
                                   WHEN 'n' THEN format(' ON DELETE SET NULL (%s)', cols.names)
                                   WHEN 'd' THEN format(' ON DELETE SET DEFAULT (%s)', cols.names)
                                   WHEN 'r' THEN ' ON DELETE RESTRICT' ELSE '' END)) AS fix
FROM keys k
CROSS JOIN LATERAL (SELECT string_agg(a.attname, ', ' ORDER BY n) AS names
                      FROM unnest(k.conkey) WITH ORDINALITY x(attnum, n)
                      JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = x.attnum) cols
CROSS JOIN LATERAL (SELECT string_agg(a.attname, ', ' ORDER BY n) AS names
                      FROM unnest(k.confkey) WITH ORDINALITY x(attnum, n)
                      JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = x.attnum) refcols
WHERE k.parent_org IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM generate_subscripts(k.conkey, 1) i
                  WHERE k.conkey[i] = k.child_org AND k.confkey[i] = k.parent_org)
ORDER BY 1
"""

CASCADE_REASON = r'^zb:creator-cascade: \S'
KEY_REASON = r'^zb:per-user: \S'

# R3. A cascade from "user": the creator's account takes the row with it. A labeled table is exempt, as in R2.
CREATOR_CASCADES = rf"""{UNLABELED}
SELECT c.conrelid::regclass::text AS "table", c.conname AS name,
       format('COMMENT ON CONSTRAINT %I ON %s IS ''zb:creator-cascade: <why>'';', c.conname, c.conrelid::regclass) AS fix
FROM pg_constraint c
WHERE c.contype = 'f' AND c.confrelid = '"user"'::regclass AND c.confdeltype = 'c'
  AND c.conparentid = 0 AND c.conrelid IN (SELECT oid FROM unlabeled)
  AND coalesce(obj_description(c.oid, 'pg_constraint'), '') !~ '{CASCADE_REASON}'
ORDER BY 1, 2
"""

# R4. A key column references "user" when a foreign key to it has the column among its own; the key leads to a person
# when it does not also hold organization_id. An expression column has indkey 0, so the parse tree of the expressions
# is searched for the column's :varattno instead. An exclusion constraint has no indisunique but is a key as well.
PER_USER_KEYS = rf"""{UNLABELED}, keys AS (
  SELECT i.indrelid, i.indexrelid,
         (SELECT array_agg(k) FROM unnest(i.indkey::int2[]) WITH ORDINALITY x(k, n) WHERE n <= i.indnkeyatts) AS cols,
         coalesce(i.indexprs::text, '') AS exprs
  FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
  WHERE (i.indisunique OR EXISTS (SELECT 1 FROM pg_constraint x WHERE x.conindid = i.indexrelid AND x.contype = 'x'))
    AND NOT ic.relispartition AND i.indrelid IN (SELECT oid FROM unlabeled)
), holds AS (
  SELECT k.*,
         (k.cols && ARRAY(SELECT unnest(f.conkey) FROM pg_constraint f WHERE f.conrelid = k.indrelid
                            AND f.contype = 'f' AND f.confrelid = '"user"'::regclass)
          OR EXISTS (SELECT 1 FROM pg_constraint f, unnest(f.conkey) u(attnum)
                      WHERE f.conrelid = k.indrelid AND f.contype = 'f' AND f.confrelid = '"user"'::regclass
                        AND k.exprs ~ (':varattno ' || u.attnum || '\M'))) AS has_user,
         EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.indrelid AND a.attname = 'organization_id'
                    AND NOT a.attisdropped
                    AND (a.attnum = ANY (k.cols) OR k.exprs ~ (':varattno ' || a.attnum || '\M'))) AS has_org
  FROM keys k
)
SELECT h.indrelid::regclass::text AS "table", ic.relname AS name,
       format('COMMENT ON INDEX %I IS ''zb:per-user: <why>'';', ic.relname) AS fix
FROM holds h JOIN pg_class ic ON ic.oid = h.indexrelid
WHERE h.has_user AND NOT h.has_org
  AND coalesce(obj_description(h.indexrelid, 'pg_class'), '') !~ '{KEY_REASON}'
ORDER BY 1, 2
"""

# A comment that starts like a reason but is not one: no reason after the colon, or a typo in the name.
MALFORMED_REASONS = rf"""
SELECT 'constraint ' || c.conrelid::regclass::text || '.' || c.conname AS object, d.description
FROM pg_description d JOIN pg_constraint c ON d.classoid = 'pg_constraint'::regclass AND d.objoid = c.oid
WHERE c.connamespace = 'public'::regnamespace AND d.description ~* '^\s*zb' AND d.description !~ '{CASCADE_REASON}'
UNION ALL
SELECT 'index ' || ic.relname, d.description
FROM pg_description d JOIN pg_class ic ON d.classoid = 'pg_class'::regclass AND d.objoid = ic.oid
WHERE ic.relnamespace = 'public'::regnamespace AND ic.relkind IN ('i', 'I') AND d.description ~* '^\s*zb'
  AND d.description !~ '{KEY_REASON}'
ORDER BY 1
"""


UNSCOPED = r'^zb:unscoped (identity|global|cost):'
EXTRA_REASON = r'^zb:extra: \S'
NARROWS_REASON = r'^zb:narrows: \S'
# R5. The only policies besides zb_scope. HubSpot is one grant per person across organizations (design question 3).
EXTRA_POLICIES = ('user_oauth_connections.zb_hubspot_per_user',)
# R5. Restrictive policies, by command and text: only the owner writes a HubSpot grant stored outside zb.org().
_HUBSPOT_OWNER = "((provider <> 'hubspot'::text) OR (user_id = zb.actor()) OR (organization_id = zb.org()))"
RESTRICTIVE_POLICIES = {
    'user_oauth_connections.zb_hubspot_owner_updates': ('w', _HUBSPOT_OWNER),
    'user_oauth_connections.zb_hubspot_owner_deletes': ('d', _HUBSPOT_OWNER),
}
# R5. The only relations with organization_id that may carry a label (zb:personal-in-org), the list in zb.scope.
PERSONAL_IN_ORG = ('notifications',)
# Every relation that carries a label, by kind. A label replaces the organization rule or turns row-level security
# off, so each one is a reviewed change here.
LABELLED = {
    'private': ('user_memory_doc',),
    'personal': (
        'crm_activity_writes',
        'crm_contact_writes',
        'crm_gate_checks',
        'notification_settings',
        'push_subscriptions',
    ),
    'personal-in-org': PERSONAL_IN_ORG,
    'unscoped': (
        '"user"',
        'account',
        'auth_email_sends',
        'contact_lookups',
        'invitation',
        'jwks',
        'llm_usage',
        'member',
        'organization',
        'session',
        'settings',
        'vendor_calls',
        'verification',
        'webhook_events',
    ),
}
# R8. Triggers that row-level security depends on, with their function.
GUARD_TRIGGERS = {'user_oauth_connections.zb_keep_hubspot_owner': 'zb.keep_hubspot_owner()'}
GUARD_REASON = r'^zb:guard: \S'
CALLER_REASON = r'^zb:runs-as-caller: \S'

# R5. The person policies' subqueries, as pg_get_expr prints them.
LIVE_MEMBERS_OF_ORG = ' SELECT l.user_id\n   FROM zb.live_members l\n  WHERE (l.organization_id = zb.org())'
LIVE_ORGS_OF_ACTOR = ' SELECT l.organization_id\n   FROM zb.live_members l\n  WHERE (l.user_id = zb.actor())'

# R5. The tables that reach an organization: R1's walk, seeded only by organization_id. A person-labelled table seeds
# R1's walk, so a child of one would count, and then no label would satisfy both R2 and this rule.
ORG_PARENTS = r"""org_parents(oid) AS (
  SELECT b.oid FROM base b
  WHERE EXISTS (SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = b.oid AND a.attname = 'organization_id' AND NOT a.attisdropped AND a.attnotnull)
  UNION
  SELECT c.conrelid FROM pg_constraint c
  JOIN org_parents p ON p.oid = c.confrelid
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
  WHERE c.contype = 'f' AND cardinality(c.conkey) = 1 AND a.attnotnull
)"""

ON_ORGANIZATION_DATA = 'on organization data'

# R5. One row per problem: the relation, what is wrong, and the SQL that fixes it.
SECURITY_PROBLEMS = rf"""{PATHS}, {ORG_PARENTS}, rel AS (
  SELECT c.oid, c.oid::regclass::text AS rel, c.relkind, c.relrowsecurity, c.reloptions,
         coalesce(obj_description(c.oid, 'pg_class'), '') AS label,
         substring(coalesce(obj_description(c.oid, 'pg_class'), '')
                   FROM '^zb:(?:private|personal|personal-in-org) ([a-z_]+):') AS person,
         EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'organization_id'
                   AND NOT a.attisdropped) AS has_org,
         c.oid IN (SELECT oid FROM org_parents) AS reaches_org,
         EXISTS (SELECT 1 FROM pg_trigger tg JOIN pg_proc p ON p.oid = tg.tgfoid
                  WHERE tg.tgrelid = c.oid AND NOT tg.tgisinternal AND p.proname ~ '^fill_.*organization_id') AS filled,
         (SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
           WHERE d.adrelid = c.oid AND a.attname = 'organization_id') AS org_default
  FROM pg_class c
  WHERE (c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'm', 'v') AND c.relname <> 'schema_migrations')
     OR (c.relnamespace = 'zb'::regnamespace AND c.relkind = 'v')
), shaped AS (
  SELECT r.*,
         CASE
           WHEN r.label ~ '^zb:private ' THEN format('(%I = zb.actor())', r.person)
           WHEN r.label ~ '^zb:personal ' THEN format('((%I = zb.actor()) OR %s)', r.person, tm.teammate)
           WHEN r.label ~ '^zb:personal-in-org ' AND r.has_org
             THEN format('(((%I = zb.actor()) AND (organization_id IN ({LIVE_ORGS_OF_ACTOR}))) OR '
                         '((organization_id = zb.org()) AND %s))', r.person, tm.teammate)
           WHEN r.label !~ '^zb:' AND r.has_org THEN '(organization_id = zb.org())'
         END AS wanted,
         (SELECT p FROM pg_policy p WHERE p.polrelid = r.oid AND p.polname = 'zb_scope') AS policy
  FROM rel r
  CROSS JOIN LATERAL (SELECT CASE WHEN r.person IS NOT NULL
                              THEN format('(%I IN ({LIVE_MEMBERS_OF_ORG}))', r.person) END AS teammate) tm
), problems(rel, problem, fix) AS (
  SELECT rel, 'a view reads its tables as its owner', format('ALTER VIEW %s SET (security_invoker = true);', rel)
    FROM shaped WHERE relkind = 'v' AND NOT coalesce('security_invoker=true' = ANY (reloptions), false)
  UNION ALL
  SELECT rel, 'zenbree_app reads an unlabeled materialized view, which no policy limits',
         format('REVOKE ALL ON %s FROM zenbree_app;', rel)
    FROM shaped WHERE relkind = 'm' AND label !~ '{LABEL}' AND has_table_privilege('zenbree_app', oid, 'SELECT')
  UNION ALL
  SELECT rel, 'row-level security is on, or a policy is left, on a zb:unscoped table', format('SELECT zb.scope(%L);', rel)
    FROM shaped s WHERE relkind IN ('r', 'p') AND label ~ '{UNSCOPED}'
     AND (relrowsecurity OR EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = s.oid AND p.polname = 'zb_scope'))
  UNION ALL
  SELECT rel, format('a %s label {ON_ORGANIZATION_DATA}: it replaces organization_id = zb.org(), so zenbree_app '
                     'reaches other organizations'' rows', substring(label FROM '^\S*')),
         format('COMMENT ON TABLE %s IS NULL; -- remove the label. The only label allowed {ON_ORGANIZATION_DATA} is '
                'zb:personal-in-org, on {', '.join(PERSONAL_IN_ORG)}', rel)
    FROM shaped WHERE relkind IN ('r', 'p', 'm') AND label ~* '^\s*zb'
     AND (has_org OR reaches_org OR (label ~ '^zb:unscoped' AND oid IN (SELECT oid FROM parents)))
     AND NOT (label ~ '^zb:personal-in-org ' AND rel = ANY (ARRAY{list(PERSONAL_IN_ORG)}::text[]))
  UNION ALL
  SELECT rel, 'no policy shape: add organization_id or a zb:private, zb:personal or zb:personal-in-org label',
         'see test_every_table_states_its_tenant_path'
    FROM shaped WHERE relkind IN ('r', 'p') AND label !~ '{UNSCOPED}' AND wanted IS NULL
  UNION ALL
  SELECT rel, format('row-level security %s, policy zb_scope %s, expected %s',
                     CASE WHEN relrowsecurity THEN 'on' ELSE 'off' END,
                     CASE WHEN policy IS NULL THEN 'missing'
                          ELSE format('%s %s for %s: %s / %s', CASE WHEN (policy).polpermissive THEN 'permissive'
                                                                   ELSE 'restrictive' END,
                                      (policy).polcmd, (policy).polroles,
                                      pg_get_expr((policy).polqual, oid), pg_get_expr((policy).polwithcheck, oid)) END,
                     wanted),
         format('SELECT zb.scope(%L);', rel)
    FROM shaped WHERE relkind IN ('r', 'p') AND wanted IS NOT NULL
     AND NOT (relrowsecurity AND policy IS NOT NULL AND (policy).polpermissive AND (policy).polcmd = '*'
              AND (policy).polroles = '{{0}}'
              AND pg_get_expr((policy).polqual, oid) IS NOT DISTINCT FROM wanted
              AND pg_get_expr((policy).polwithcheck, oid) IS NOT DISTINCT FROM wanted)
  UNION ALL
  SELECT s.rel, format('policy %s is not one of EXTRA_POLICIES: {', '.join(EXTRA_POLICIES)}, nor of '
                       'RESTRICTIVE_POLICIES: {', '.join(RESTRICTIVE_POLICIES)}', p.polname),
         format('DROP POLICY %I ON %s;', p.polname, s.rel)
    FROM shaped s JOIN pg_policy p ON p.polrelid = s.oid
   WHERE p.polname <> 'zb_scope'
     AND s.rel || '.' || p.polname <> ALL (ARRAY{[*EXTRA_POLICIES, *RESTRICTIVE_POLICIES]}::text[])
  UNION ALL
  SELECT s.rel, format('policy %s widens zb_scope with no reason', p.polname),
         format('COMMENT ON POLICY %I ON %s IS ''zb:extra: <why>'';', p.polname, s.rel)
    FROM shaped s JOIN pg_policy p ON p.polrelid = s.oid
   WHERE p.polname <> 'zb_scope' AND p.polpermissive
     AND coalesce(obj_description(p.oid, 'pg_policy'), '') !~ '{EXTRA_REASON}'
  UNION ALL
  SELECT s.rel, format('policy %s narrows zb_scope with no reason', p.polname),
         format('COMMENT ON POLICY %I ON %s IS ''zb:narrows: <why>'';', p.polname, s.rel)
    FROM shaped s JOIN pg_policy p ON p.polrelid = s.oid
   WHERE NOT p.polpermissive AND coalesce(obj_description(p.oid, 'pg_policy'), '') !~ '{NARROWS_REASON}'
  UNION ALL
  SELECT w.rel, format('policy %s is not the restrictive %s policy %s: found %s', w.pol, w.cmd, w.expr,
                       CASE WHEN p.oid IS NULL THEN 'nothing'
                            ELSE format('%s %s for %s: %s / %s', CASE WHEN p.polpermissive THEN 'permissive'
                                                                     ELSE 'restrictive' END,
                                        p.polcmd, p.polroles, pg_get_expr(p.polqual, p.polrelid),
                                        pg_get_expr(p.polwithcheck, p.polrelid)) END),
         format('CREATE POLICY %I ON %s AS RESTRICTIVE FOR %s USING %s;', w.pol, w.rel,
                CASE w.cmd WHEN 'w' THEN 'UPDATE' WHEN 'd' THEN 'DELETE' WHEN 'a' THEN 'INSERT' WHEN 'r' THEN 'SELECT'
                           ELSE 'ALL' END, w.expr)
    FROM (VALUES {', '.join(f'({k.split(".")[0]!r}, {k.split(".")[1]!r}, {v[0]!r}, $q${v[1]}$q$)' for k, v in RESTRICTIVE_POLICIES.items())})
         w(rel, pol, cmd, expr)
    LEFT JOIN pg_policy p ON p.polrelid = to_regclass(w.rel) AND p.polname = w.pol
   WHERE NOT (p.oid IS NOT NULL AND NOT p.polpermissive AND p.polcmd = w.cmd AND p.polroles = '{{0}}'
              AND pg_get_expr(p.polqual, p.polrelid) = w.expr AND p.polwithcheck IS NULL)
  UNION ALL
  SELECT rel, format('organization_id defaults to %s, expected %s', coalesce(org_default, 'nothing'),
                     CASE WHEN filled THEN 'nothing: the fill trigger takes the parent''s' ELSE 'zb.org()' END),
         format('SELECT zb.scope(%L);', rel)
    FROM shaped WHERE relkind IN ('r', 'p') AND wanted LIKE '%(organization_id = zb.org())%'
     AND CASE WHEN filled THEN org_default IS NOT DISTINCT FROM 'zb.org()'
              ELSE org_default IS DISTINCT FROM 'zb.org()' END
)
SELECT * FROM problems ORDER BY 1, 2
"""

# R6. The tables a trigger function writes, from its source.
CALLER_WRITES = rf"""{PATHS}
SELECT DISTINCT tg.tgrelid::regclass::text AS "table", p.oid::regprocedure::text AS function, lower(m[1]) AS writes
FROM pg_trigger tg
JOIN pg_proc p ON p.oid = tg.tgfoid
CROSS JOIN LATERAL regexp_matches(p.prosrc,
  '\m(?:insert\s+into|update|delete\s+from)\s+(?:only\s+)?(?:public\.)?"?([a-z_]+)"?', 'gi') m
WHERE tg.tgrelid IN ('member'::regclass, '"user"'::regclass) AND NOT tg.tgisinternal
  AND lower(m[1]) IN (SELECT c.relname FROM pg_class c WHERE c.oid IN (SELECT oid FROM parents)
                        AND coalesce(obj_description(c.oid, 'pg_class'), '') !~ '{UNSCOPED}')
  AND coalesce(obj_description(p.oid, 'pg_proc'), '') !~ '{CALLER_REASON}'
ORDER BY 1, 2, 3
"""

# R7. has_*_privilege also counts a grant to PUBLIC.
SECRET_GRANTS = """
SELECT t AS "table" FROM unnest(ARRAY['jwks', 'account', 'session', 'verification', 'schema_migrations']) t
WHERE has_table_privilege('zenbree_app', format('public.%I', t),
                          'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN')
   OR has_any_column_privilege('zenbree_app', format('public.%I', t), 'SELECT, INSERT, UPDATE, REFERENCES')
ORDER BY 1
"""

# R8. One row per guard trigger that is missing, not in its normal state, runs another function or has no reason.
# REPLICA skips it in a normal session; ALWAYS also fires it while session_replication_role is replica.
GUARD_PROBLEMS = f"""
SELECT w.name AS "trigger",
       CASE WHEN tg.oid IS NULL THEN 'missing'
            WHEN tg.tgenabled = 'D' THEN 'disabled'
            WHEN tg.tgenabled = 'R' THEN 'enabled REPLICA: a normal session skips it'
            WHEN tg.tgenabled = 'A' THEN 'enabled ALWAYS: it fires in replica sessions too'
            WHEN tg.tgfoid::regprocedure::text <> w.fn THEN format('runs %s, not %s', tg.tgfoid::regprocedure, w.fn)
            ELSE 'its function has no zb:guard: reason' END AS problem
FROM (VALUES {', '.join(f'({k!r}, {v!r})' for k, v in GUARD_TRIGGERS.items())}) w(name, fn)
LEFT JOIN pg_trigger tg ON tg.tgrelid = to_regclass(split_part(w.name, '.', 1)) AND tg.tgname = split_part(w.name, '.', 2)
WHERE tg.oid IS NULL OR tg.tgenabled <> 'O' OR tg.tgfoid::regprocedure::text <> w.fn
   OR coalesce(obj_description(tg.tgfoid, 'pg_proc'), '') !~ '{GUARD_REASON}'
ORDER BY 1
"""

# R9. Functions, in any schema, that may run as their owner. Empty: such a function reads around every policy, so an
# entry is the user's decision, with its reason in a comment above it.
DEFINER_FUNCTIONS: tuple[str, ...] = ()
# R9. Rules, as '<relation>.<rule>', that may exist. Empty for the same reason: a rule's action runs as the owner.
RULES: tuple[str, ...] = ()
# R9. What zenbree_system holds beyond SELECT, INSERT, UPDATE and DELETE, as '<relation> <privilege>': '<why>'.
SYSTEM_PRIVILEGES: dict[str, str] = {}
APP_ROLES = ('zenbree_app', 'zenbree_system')


def _text_array(values) -> str:
    return 'ARRAY[' + ', '.join("'" + v.replace("'", "''") + "'" for v in values) + ']::text[]'


def privilege_problems(roles=APP_ROLES) -> str:
    """R9 for `roles`: one row per object, what lets the role around the policies, and the SQL that fixes it. A test
    passes a throwaway role, because a membership is cluster-wide and the real roles are shared."""
    return f"""
WITH roles AS (SELECT oid, rolname AS name FROM pg_roles WHERE rolname = ANY ({_text_array(roles)})),
grantees AS (SELECT oid, name FROM roles UNION ALL SELECT 0, 'PUBLIC'),
schemas AS (SELECT oid FROM pg_namespace WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                                           AND nspname !~ '^pg_(toast_)?temp_[0-9]+$'),
-- What a role may hold on each relation: the app's four where the policies are, nothing anywhere else.
reach AS (
  SELECT c.oid, c.relkind,
         CASE WHEN c.relkind = 'f' THEN ': a foreign table holds rows no policy here limits'
              WHEN NOT (n.nspname = 'public' OR (n.nspname = 'zb' AND c.relkind = 'v'))
                THEN ': row-level security is installed in public only'
              ELSE '' END AS outside
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relnamespace IN (SELECT oid FROM schemas)
)
SELECT p.oid::regprocedure::text AS object,
       format('is SECURITY DEFINER: it runs as %s, around every policy', p.proowner::regrole) AS problem,
       format('ALTER FUNCTION %s SECURITY INVOKER;', p.oid::regprocedure) AS fix
FROM pg_proc p
WHERE p.pronamespace IN (SELECT oid FROM schemas) AND p.prosecdef
  AND p.oid::regprocedure::text <> ALL ({_text_array(DEFINER_FUNCTIONS)})
UNION ALL
SELECT format('%s.%I', c.oid::regclass, r.rulename),
       format('is a rule: its action runs as %s, around every policy', c.relowner::regrole),
       format('DROP RULE %I ON %s;', r.rulename, c.oid::regclass)
FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class
WHERE c.relnamespace IN (SELECT oid FROM schemas)
  AND NOT (r.ev_type = '1' AND c.relkind IN ('v', 'm'))
  AND format('%s.%I', c.oid::regclass, r.rulename) <> ALL ({_text_array(RULES)})
UNION ALL
SELECT c.oid::regclass::text, format('grants %s to %s%s', a.privilege_type, g.name, x.outside),
       format('REVOKE %s ON %s FROM %s;', a.privilege_type, c.oid::regclass, g.name)
FROM pg_class c JOIN reach x ON x.oid = c.oid CROSS JOIN LATERAL aclexplode(c.relacl) a
JOIN grantees g ON g.oid = a.grantee
WHERE a.privilege_type <> ALL (CASE WHEN x.outside <> '' THEN '{{}}'::text[]
                                    WHEN c.relkind = 'S' THEN ARRAY['USAGE', 'SELECT', 'UPDATE']
                                    ELSE ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE'] END)
  AND NOT (g.name = 'zenbree_system'
           AND c.oid::regclass::text || ' ' || a.privilege_type = ANY ({_text_array(SYSTEM_PRIVILEGES)}))
UNION ALL
SELECT format('%s.%I', c.oid::regclass, t.attname),
       format('grants %s on the column to %s%s', a.privilege_type, g.name, x.outside),
       format('REVOKE %s (%I) ON %s FROM %s;', a.privilege_type, t.attname, c.oid::regclass, g.name)
FROM pg_class c JOIN reach x ON x.oid = c.oid JOIN pg_attribute t ON t.attrelid = c.oid
CROSS JOIN LATERAL aclexplode(t.attacl) a JOIN grantees g ON g.oid = a.grantee
WHERE a.privilege_type <> ALL (CASE WHEN x.outside <> '' THEN '{{}}'::text[]
                                    ELSE ARRAY['SELECT', 'INSERT', 'UPDATE'] END)
UNION ALL
SELECT quote_ident(n.nspname), format('grants %s on the schema to %s', a.privilege_type, g.name),
       format('REVOKE %s ON SCHEMA %I FROM %s;', a.privilege_type, n.nspname, g.name)
FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a JOIN grantees g ON g.oid = a.grantee
WHERE n.oid IN (SELECT oid FROM schemas)
  AND NOT (a.privilege_type = 'USAGE' AND n.nspname IN ('public', 'zb'))
UNION ALL
-- Every entry, also those of no schema (defaclnamespace 0), which apply in every schema.
SELECT format('default privileges of %s%s on %s', d.defaclrole::regrole,
              CASE WHEN d.defaclnamespace = 0 THEN '' ELSE ' in ' || d.defaclnamespace::regnamespace END, k.objects),
       format('grant %s to %s', a.privilege_type, g.name),
       format('ALTER DEFAULT PRIVILEGES FOR ROLE %s%s REVOKE %s ON %s FROM %s;', d.defaclrole::regrole,
              CASE WHEN d.defaclnamespace = 0 THEN '' ELSE ' IN SCHEMA ' || d.defaclnamespace::regnamespace END,
              a.privilege_type, upper(k.objects), g.name)
FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a JOIN grantees g ON g.oid = a.grantee
CROSS JOIN LATERAL (
  SELECT CASE d.defaclobjtype WHEN 'r' THEN 'tables' WHEN 'S' THEN 'sequences' WHEN 'f' THEN 'functions'
                              WHEN 'T' THEN 'types' WHEN 'n' THEN 'schemas' ELSE 'large objects' END AS objects,
         CASE WHEN d.defaclobjtype = 'f' THEN ARRAY['EXECUTE']
              WHEN d.defaclobjtype = 'T' THEN ARRAY['USAGE']
              WHEN g.oid = 0 OR d.defaclnamespace IS DISTINCT FROM 'public'::regnamespace THEN '{{}}'::text[]
              WHEN d.defaclobjtype = 'r' THEN ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']
              WHEN d.defaclobjtype = 'S' THEN ARRAY['USAGE', 'SELECT', 'UPDATE']
              ELSE '{{}}'::text[] END AS allowed
) k
WHERE a.privilege_type <> ALL (k.allowed)
UNION ALL
SELECT 'parameter ' || p.parname, format('grants %s on the parameter to %s', a.privilege_type, g.name),
       format('REVOKE %s ON PARAMETER %s FROM %s;', a.privilege_type, p.parname, g.name)
FROM pg_parameter_acl p CROSS JOIN LATERAL aclexplode(p.paracl) a JOIN grantees g ON g.oid = a.grantee
UNION ALL
SELECT o.object, format('is owned by %s, which skips the policies of what it owns', r.name),
       format('ALTER %s %s OWNER TO CURRENT_USER;', o.kind, o.object)
FROM (
  SELECT c.relowner AS owner, c.oid::regclass::text AS object,
         CASE c.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'S' THEN 'SEQUENCE'
                        WHEN 'f' THEN 'FOREIGN TABLE' WHEN 'c' THEN 'TYPE' ELSE 'TABLE' END AS kind
  FROM pg_class c WHERE c.relnamespace IN (SELECT oid FROM schemas) AND c.relkind NOT IN ('i', 'I')
  UNION ALL
  SELECT p.proowner, p.oid::regprocedure::text, 'ROUTINE' FROM pg_proc p WHERE p.pronamespace IN (SELECT oid FROM schemas)
  UNION ALL
  SELECT n.nspowner, quote_ident(n.nspname), 'SCHEMA' FROM pg_namespace n WHERE n.oid IN (SELECT oid FROM schemas)
) o JOIN roles r ON r.oid = o.owner
UNION ALL
SELECT r.name, 'is a member of ' || m.rolname || CASE WHEN direct THEN '' ELSE ', through another role' END,
       CASE WHEN direct THEN format('REVOKE %I FROM %I;', m.rolname, r.name)
            ELSE 'revoke the role it holds ' || m.rolname || ' through (listed too)' END
FROM roles r JOIN pg_roles m ON m.oid <> r.oid AND pg_has_role(r.oid, m.oid, 'MEMBER')
CROSS JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM pg_auth_members d WHERE d.member = r.oid AND d.roleid = m.oid)) x(direct)
ORDER BY 1, 2
"""


# Every labelled relation, by kind; a comment that only looks like a label has the kind of its first word.
LABELS = """
SELECT coalesce(substring(d.description FROM '^zb:([a-z-]+) '), d.description) AS kind,
       c.oid::regclass::text AS rel
FROM pg_class c JOIN pg_description d ON d.classoid = 'pg_class'::regclass AND d.objoid = c.oid AND d.objsubid = 0
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind NOT IN ('i', 'I') AND d.description ~* '^\\s*zb'
ORDER BY 1, 2
"""


def test_every_table_states_its_tenant_path(db):
    offenders = [r['table'] for r in db.execute(TABLES_WITHOUT_A_TENANT_PATH).fetchall()]
    fixes = '\n'.join(
        f'  {t}: ALTER TABLE {t} ADD COLUMN organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE CASCADE;\n'
        f'      (or a NOT NULL foreign key to a table that has an organization), or\n'
        f"      COMMENT ON TABLE {t} IS 'zb:private <user column>: <why>';\n"
        f"      COMMENT ON TABLE {t} IS 'zb:personal <user column>: <why>';\n"
        f"      COMMENT ON TABLE {t} IS 'zb:personal-in-org <user column>: <why>';\n"
        f"      COMMENT ON TABLE {t} IS 'zb:unscoped identity|global|cost: <why>';"
        for t in offenders
    )
    assert not offenders, (
        f'{len(offenders)} tables name no organization:\n{fixes}\n'
        'Add the SQL to a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql. Give a label a reason that is true.'
    )


def test_the_rule_reads_labels_and_paths_through_foreign_keys(db):
    db.execute("""
        CREATE TABLE zt_root (id int PRIMARY KEY, organization_id uuid NOT NULL);
        CREATE TABLE zt_child (id int PRIMARY KEY, root_id int NOT NULL REFERENCES zt_root(id));
        CREATE TABLE zt_grandchild (id int PRIMARY KEY, child_id int NOT NULL REFERENCES zt_child(id));
        CREATE TABLE zt_nullable (id int PRIMARY KEY, root_id int REFERENCES zt_root(id));
        CREATE TABLE zt_label (id int PRIMARY KEY);
        COMMENT ON TABLE zt_label IS 'zb:unscoped global: a test';
        CREATE TABLE zt_unscoped_child (id int PRIMARY KEY, label_id int NOT NULL REFERENCES zt_label(id));
        CREATE TABLE zt_user_child (id int PRIMARY KEY, user_id uuid NOT NULL REFERENCES "user"(id));
        CREATE TABLE zt_settings_child (id int PRIMARY KEY, k text NOT NULL REFERENCES settings(key));
        CREATE TABLE zt_llm_usage_child (id int PRIMARY KEY, u uuid NOT NULL REFERENCES llm_usage(id));
        CREATE TABLE zt_badlabel (id int PRIMARY KEY);
        COMMENT ON TABLE zt_badlabel IS 'zb:unscoped whatever: a test';
    """)
    found = {r['table'] for r in db.execute(TABLES_WITHOUT_A_TENANT_PATH).fetchall()}
    assert {t for t in found if t.startswith('zt_')} == {
        'zt_nullable',
        'zt_badlabel',
        'zt_unscoped_child',
        'zt_user_child',
        'zt_settings_child',
        'zt_llm_usage_child',
    }


def test_every_tenant_table_stores_its_organization(db):
    offenders = db.execute(TABLES_WITHOUT_AN_ORGANIZATION_ID).fetchall()
    fixes = '\n'.join(
        f'  {r["table"]}: ALTER TABLE {r["table"]} ADD COLUMN organization_id uuid;\n'
        f'      fill it from {r["parent"]} (through {r["fk"]}), then\n'
        f'      ALTER TABLE {r["table"]} ALTER COLUMN organization_id SET NOT NULL;\n'
        f'      CREATE INDEX idx_{r["table"]}_organization_id ON {r["table"]}(organization_id);\n'
        f'      CREATE TRIGGER {r["table"]}_fill_organization_id BEFORE INSERT OR UPDATE OF {r["fk"]} ON {r["table"]}\n'
        f"        FOR EACH ROW EXECUTE FUNCTION fill_organization_id_from_parent('{r['parent']}', '{r['fk']}');"
        for r in offenders
    )
    assert not offenders, (
        f'{len(offenders)} tables reach their organization only through a parent, so a policy would have to read the '
        f'parent row:\n{fixes}\n'
        'Add the SQL to a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql.'
    )


def test_every_tenant_foreign_key_names_the_organization(db):
    offenders = db.execute(FOREIGN_KEYS_WITHOUT_THE_ORGANIZATION).fetchall()
    keys = '\n'.join(f'  {r["key"]}\n      {r["fix"]}' for r in offenders)
    assert not offenders, (
        f'{len(offenders)} foreign keys point at a table with an organization_id but do not carry it, so the child '
        f'can name a parent of another organization:\n{keys}\n'
        'Add the SQL to a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql. The suggested key keeps the ON '
        'UPDATE and ON DELETE actions of the one it replaces: keep them, or an owner key that cascades stops working '
        'and deleting an account fails. A SET NULL key names its own columns, so the delete does not null '
        'organization_id.'
    )


def test_the_organization_rules_read_the_catalog(db):
    db.execute("""
        CREATE TABLE zt_org (id uuid PRIMARY KEY, organization_id uuid NOT NULL, UNIQUE (id, organization_id));
        CREATE TABLE zt_bare (id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES zt_org(id));
        CREATE TABLE zt_unique (id uuid PRIMARY KEY, other uuid, organization_id uuid NOT NULL, UNIQUE (id, organization_id));
        CREATE TABLE zt_ok (
          id uuid PRIMARY KEY, org_id uuid NOT NULL, other uuid, owner uuid, organization_id uuid NOT NULL,
          FOREIGN KEY (org_id, organization_id) REFERENCES zt_org (id, organization_id) ON DELETE CASCADE,
          FOREIGN KEY (other, organization_id) REFERENCES zt_unique (id, organization_id) ON DELETE SET NULL (other));
        CREATE TABLE zt_wrong (
          id uuid PRIMARY KEY, org_id uuid NOT NULL, other uuid, organization_id uuid NOT NULL,
          FOREIGN KEY (org_id) REFERENCES zt_org (id) ON DELETE CASCADE,
          FOREIGN KEY (other) REFERENCES zt_unique (id) ON DELETE SET NULL);
        CREATE TABLE zt_swapped (
          id uuid PRIMARY KEY, org_id uuid NOT NULL, organization_id uuid NOT NULL,
          UNIQUE (organization_id, id),
          FOREIGN KEY (organization_id, org_id) REFERENCES zt_org (organization_id, id) ON DELETE CASCADE);
        CREATE TABLE zt_label (id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES zt_org(id));
        COMMENT ON TABLE zt_label IS 'zb:unscoped global: a test';
        CREATE TABLE zt_owned (id uuid PRIMARY KEY, owner uuid, organization_id uuid NOT NULL, UNIQUE (id, owner));
        CREATE TABLE zt_owner_key (
          id uuid PRIMARY KEY, owned_id uuid NOT NULL, owner uuid, organization_id uuid NOT NULL,
          FOREIGN KEY (owned_id, owner) REFERENCES zt_owned (id, owner) ON UPDATE CASCADE ON DELETE CASCADE);
    """)
    tables = {r['table'] for r in db.execute(TABLES_WITHOUT_AN_ORGANIZATION_ID).fetchall()}
    keys = {r['key']: r['fix'] for r in db.execute(FOREIGN_KEYS_WITHOUT_THE_ORGANIZATION).fetchall()}
    assert {t for t in tables if t.startswith('zt_')} == {'zt_bare'}
    assert {k for k in keys if k.startswith('zt_')} == {
        'zt_bare (org_id) -> zt_org',
        'zt_wrong (org_id) -> zt_org',
        'zt_wrong (other) -> zt_unique',
        'zt_owner_key (owned_id, owner) -> zt_owned',
    }
    assert keys['zt_owner_key (owned_id, owner) -> zt_owned'] == (
        'ALTER TABLE zt_owned ADD UNIQUE (id, owner, organization_id); '
        'ALTER TABLE zt_owner_key DROP CONSTRAINT zt_owner_key_owned_id_owner_fkey, ADD FOREIGN KEY '
        '(owned_id, owner, organization_id) REFERENCES zt_owned (id, owner, organization_id) '
        'ON UPDATE CASCADE ON DELETE CASCADE;'
    )
    assert keys['zt_wrong (org_id) -> zt_org'] == (
        'ALTER TABLE zt_wrong DROP CONSTRAINT zt_wrong_org_id_fkey, ADD FOREIGN KEY (org_id, organization_id) '
        'REFERENCES zt_org (id, organization_id) ON DELETE CASCADE;'
    )
    assert keys['zt_wrong (other) -> zt_unique'].endswith('ON DELETE SET NULL (other);')


def test_every_creator_cascade_on_organization_data_has_a_reason(db):
    offenders = db.execute(CREATOR_CASCADES).fetchall()
    lines = '\n'.join(f'  {r["table"]}.{r["name"]}: {r["fix"]}' for r in offenders)
    assert not offenders, (
        f'{len(offenders)} foreign keys to "user" cascade on a table that holds organization data, so deleting the '
        f"creator's account deletes rows the organization owns (#333):\n{lines}\n"
        "Make the key ON DELETE SET NULL (the creator column nullable) or, when the rows are the person's, give the "
        'cascade its reason in a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql.'
    )


def test_every_per_user_key_on_organization_data_has_a_reason(db):
    offenders = db.execute(PER_USER_KEYS).fetchall()
    lines = '\n'.join(f'  {r["table"]}.{r["name"]}: {r["fix"]}' for r in offenders)
    assert not offenders, (
        f'{len(offenders)} unique keys hold a user column and no organization_id, so one person has one row across '
        f'organizations, or a second member cannot have one (#473):\n{lines}\n'
        "Add organization_id to the key or, when the row is the person's by design, give the key its reason in a "
        'new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql.'
    )


def test_a_reason_comment_is_well_formed(db):
    offenders = db.execute(MALFORMED_REASONS).fetchall()
    lines = '\n'.join(f'  {r["object"]}: {r["description"]!r}' for r in offenders)
    assert not offenders, (
        f'{len(offenders)} comments start with zb but are not a reason:\n{lines}\n'
        'Write zb:creator-cascade: <why> on a constraint, or zb:per-user: <why> on an index, with a reason.'
    )


def test_the_key_rules_read_the_catalog(db):
    db.execute("""
        CREATE TABLE zt_org (id uuid PRIMARY KEY, organization_id uuid NOT NULL, UNIQUE (id, organization_id));
        CREATE TABLE zt_bare (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES "user"(id)
            ON DELETE CASCADE);
        CREATE TABLE zt_reasoned (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL,
          CONSTRAINT zt_reasoned_creator FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE);
        COMMENT ON CONSTRAINT zt_reasoned_creator ON zt_reasoned IS 'zb:creator-cascade: the import is private';
        CREATE TABLE zt_setnull (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid REFERENCES "user"(id) ON DELETE SET NULL);
        CREATE TABLE zt_child_cascade (
          id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES zt_org(id), user_id uuid REFERENCES "user"(id)
            ON DELETE CASCADE);
        CREATE TABLE zt_personal (id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE);
        COMMENT ON TABLE zt_personal IS 'zb:personal user_id: a test';
        CREATE TABLE zt_personal_child (
          id uuid PRIMARY KEY, p uuid NOT NULL REFERENCES zt_personal(id),
          user_id uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE);

        CREATE TABLE zt_keys (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES "user"(id),
          other uuid, k text);
        CREATE UNIQUE INDEX zt_keys_per_user ON zt_keys (user_id);
        CREATE UNIQUE INDEX zt_keys_partial ON zt_keys (user_id, k) WHERE other IS NULL;
        CREATE UNIQUE INDEX zt_keys_reasoned ON zt_keys (user_id, other);
        COMMENT ON INDEX zt_keys_reasoned IS 'zb:per-user: one per person by design';
        CREATE UNIQUE INDEX zt_keys_in_org ON zt_keys (organization_id, user_id);
        CREATE UNIQUE INDEX zt_keys_no_user ON zt_keys (k);
        CREATE UNIQUE INDEX zt_keys_included ON zt_keys (k) INCLUDE (user_id);
        CREATE UNIQUE INDEX zt_keys_expression ON zt_keys (lower(k), (user_id::text));
        CREATE UNIQUE INDEX zt_keys_coalesce ON zt_keys ((coalesce(user_id, id)), k);
        CREATE UNIQUE INDEX zt_keys_expr_no_user ON zt_keys (lower(k));
        CREATE UNIQUE INDEX zt_keys_expr_in_org ON zt_keys ((coalesce(user_id, id)), (organization_id::text));
        CREATE INDEX zt_keys_not_unique ON zt_keys (user_id);
        CREATE TABLE zt_constraint (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES "user"(id),
          CONSTRAINT zt_constraint_key UNIQUE (user_id));
        CREATE TABLE zt_personal_key (id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES "user"(id) UNIQUE);
        COMMENT ON TABLE zt_personal_key IS 'zb:personal-in-org user_id: a test';
    """)
    cascades = {f'{r["table"]}.{r["name"]}' for r in db.execute(CREATOR_CASCADES).fetchall()}
    assert {c for c in cascades if c.startswith('zt_')} == {
        'zt_bare.zt_bare_user_id_fkey',
        'zt_child_cascade.zt_child_cascade_user_id_fkey',
        # a child of a personal table is not itself labeled
        'zt_personal_child.zt_personal_child_user_id_fkey',
    }
    keys = {r['name']: r['fix'] for r in db.execute(PER_USER_KEYS).fetchall()}
    assert {k for k in keys if k.startswith('zt_')} == {
        'zt_keys_per_user',
        'zt_keys_partial',
        'zt_constraint_key',
        'zt_keys_expression',
        'zt_keys_coalesce',
    }
    assert keys['zt_keys_per_user'] == "COMMENT ON INDEX zt_keys_per_user IS 'zb:per-user: <why>';"
    assert db.execute(CREATOR_CASCADES).fetchall()[0]['fix'].startswith('COMMENT ON CONSTRAINT ')


def test_a_malformed_reason_fails_and_a_well_formed_one_passes(db):
    db.execute("""
        CREATE TABLE zt_m (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, a uuid REFERENCES "user"(id) ON DELETE CASCADE,
          b uuid REFERENCES "user"(id) ON DELETE CASCADE, c uuid REFERENCES "user"(id) ON DELETE CASCADE,
          CONSTRAINT zt_m_empty FOREIGN KEY (a) REFERENCES "user"(id) ON DELETE CASCADE);
        CREATE UNIQUE INDEX zt_m_key ON zt_m (b);
        COMMENT ON CONSTRAINT zt_m_empty ON zt_m IS 'zb:creator-cascade:';
        COMMENT ON INDEX zt_m_key IS 'zb:per-user:   ';
        CREATE TABLE zt_typo (id uuid PRIMARY KEY, organization_id uuid NOT NULL, u uuid NOT NULL REFERENCES "user"(id));
        CREATE UNIQUE INDEX zt_typo_key ON zt_typo (u);
        COMMENT ON INDEX zt_typo_key IS 'zb:peruser: one per person';
    """)
    malformed = {r['object'] for r in db.execute(MALFORMED_REASONS).fetchall()}
    assert {m for m in malformed if 'zt_' in m} == {'constraint zt_m.zt_m_empty', 'index zt_m_key', 'index zt_typo_key'}
    flagged = {r['name'] for r in db.execute(PER_USER_KEYS).fetchall()}
    assert {'zt_m_key', 'zt_typo_key'} <= flagged
    assert 'zt_m_empty' in {r['name'] for r in db.execute(CREATOR_CASCADES).fetchall()}
    db.execute("""
        COMMENT ON CONSTRAINT zt_m_empty ON zt_m IS 'zb:creator-cascade: a real reason';
        COMMENT ON INDEX zt_m_key IS 'zb:per-user: a real reason';
        COMMENT ON INDEX zt_typo_key IS 'zb:per-user: a real reason';
    """)
    assert not [m for m in db.execute(MALFORMED_REASONS).fetchall() if 'zt_' in m['object']]
    assert not {r['name'] for r in db.execute(PER_USER_KEYS).fetchall()} & {'zt_m_key', 'zt_typo_key'}


def test_a_materialized_view_is_never_a_tenant_root(db):
    """Catches: a view column counted as an organization_id, which R2 cannot ask to be NOT NULL and RLS cannot cover."""
    db.execute("""
        CREATE MATERIALIZED VIEW zt_mv_org AS SELECT gen_random_uuid() AS id, gen_random_uuid() AS organization_id;
        CREATE MATERIALIZED VIEW zt_mv_bare AS SELECT gen_random_uuid() AS id;
        CREATE UNIQUE INDEX zt_mv_org_id ON zt_mv_org (id);
    """)
    found = {r['table'] for r in db.execute(TABLES_WITHOUT_A_TENANT_PATH).fetchall()}
    assert {t for t in found if t.startswith('zt_mv')} == {'zt_mv_org', 'zt_mv_bare'}
    db.execute("COMMENT ON MATERIALIZED VIEW zt_mv_bare IS 'zb:unscoped global: a test'")
    found = {r['table'] for r in db.execute(TABLES_WITHOUT_A_TENANT_PATH).fetchall()}
    assert {t for t in found if t.startswith('zt_mv')} == {'zt_mv_org'}
    # Its organization_id makes it organization data, which zb:unscoped would open to zenbree_app.
    with pytest.raises(psycopg.errors.RaiseException, match='cannot carry zb:unscoped'):
        db.execute("COMMENT ON MATERIALIZED VIEW zt_mv_org IS 'zb:unscoped global: a test'")


def test_a_reason_of_the_wrong_kind_does_not_satisfy_a_rule(db):
    """Catches: a zb:per-user reason on a creator cascade, or the reverse, turning R3 or R4 off."""
    db.execute("""
        CREATE TABLE zt_k (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, u uuid NOT NULL, v uuid NOT NULL REFERENCES "user"(id),
          CONSTRAINT zt_k_cascade FOREIGN KEY (u) REFERENCES "user"(id) ON DELETE CASCADE);
        CREATE UNIQUE INDEX zt_k_key ON zt_k (v);
        COMMENT ON CONSTRAINT zt_k_cascade ON zt_k IS 'zb:per-user: wrong kind';
        COMMENT ON INDEX zt_k_key IS 'zb:creator-cascade: wrong kind';
    """)
    assert 'zt_k_cascade' in {r['name'] for r in db.execute(CREATOR_CASCADES).fetchall()}
    assert 'zt_k_key' in {r['name'] for r in db.execute(PER_USER_KEYS).fetchall()}
    malformed = {r['object'] for r in db.execute(MALFORMED_REASONS).fetchall()}
    assert {m for m in malformed if 'zt_k' in m} == {'constraint zt_k.zt_k_cascade', 'index zt_k_key'}


def test_r4_reads_the_expression_tree_not_its_text(db):
    db.execute("""
        CREATE TABLE zt_e (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES "user"(id), k text,
          "Owner Id" uuid REFERENCES "user"(id));
        CREATE TABLE zt_lower (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, lower uuid REFERENCES "user"(id), k text);
        CREATE UNIQUE INDEX zt_e_literal ON zt_e (lower(k), coalesce(k, 'user_id'));
        CREATE UNIQUE INDEX zt_lower_fn ON zt_lower (lower(k));
        CREATE UNIQUE INDEX zt_e_org_literal ON zt_e (user_id, (k || 'organization_id'));
        CREATE UNIQUE INDEX zt_e_org_read ON zt_e (user_id, (organization_id IS NULL));
        CREATE UNIQUE INDEX zt_e_quoted ON zt_e ((("Owner Id")::text));
        CREATE UNIQUE INDEX zt_e_user_expr ON zt_e ((user_id::text), k);
        ALTER TABLE zt_e ADD CONSTRAINT zt_e_excl EXCLUDE USING btree (user_id WITH =);
        CREATE TABLE zt_e_org_excl (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid REFERENCES "user"(id),
          EXCLUDE USING btree (user_id WITH =, organization_id WITH =));
    """)
    flagged = {r['name'] for r in db.execute(PER_USER_KEYS).fetchall() if r['table'].startswith('zt_')}
    assert flagged == {'zt_e_org_literal', 'zt_e_quoted', 'zt_e_user_expr', 'zt_e_excl'}
    db.execute("COMMENT ON INDEX zt_e_excl IS 'zb:per-user: a test'")
    assert 'zt_e_excl' not in {r['name'] for r in db.execute(PER_USER_KEYS).fetchall()}


def test_r4_matches_a_whole_attribute_number(db):
    """Catches: organization_id at attnum 2 matching an expression on attnum 23, which hides a per-user key."""
    filler = ', '.join(f'c{n} int' for n in range(3, 23))
    db.execute(f"""
        CREATE TABLE zt_wide (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL, {filler}, user_id uuid REFERENCES "user"(id));
        CREATE UNIQUE INDEX zt_wide_user ON zt_wide ((user_id::text));
    """)
    assert (
        db.execute(
            "SELECT attnum FROM pg_attribute WHERE attrelid = 'zt_wide'::regclass AND attname = 'user_id'"
        ).fetchone()['attnum']
        == 23
    )
    assert 'zt_wide_user' in {r['name'] for r in db.execute(PER_USER_KEYS).fetchall()}


def test_a_reason_on_a_partitioned_table_covers_its_partitions(db):
    db.execute("""
        CREATE TABLE zt_pt (
          id uuid, organization_id uuid NOT NULL, user_id uuid NOT NULL, d date NOT NULL,
          CONSTRAINT zt_pt_creator FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
        ) PARTITION BY RANGE (d);
        CREATE UNIQUE INDEX zt_pt_key ON zt_pt (user_id, d);
        CREATE TABLE zt_pt_2026 PARTITION OF zt_pt FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
    """)

    def mine():
        cascades = {r['name'] for r in db.execute(CREATOR_CASCADES).fetchall() if r['table'].startswith('zt_pt')}
        keys = {r['name'] for r in db.execute(PER_USER_KEYS).fetchall() if r['table'].startswith('zt_pt')}
        return cascades, keys

    assert mine() == ({'zt_pt_creator'}, {'zt_pt_key'})
    db.execute("""
        COMMENT ON CONSTRAINT zt_pt_creator ON zt_pt IS 'zb:creator-cascade: a test';
        COMMENT ON INDEX zt_pt_key IS 'zb:per-user: a test';
    """)
    assert mine() == (set(), set())


# The schema just before #333 made the creator column the organization's: every creator cascade is still there.
CREATOR_MIGRATION = '20261006161714_org_rows_outlive_their_creator.sql'


@pytest.fixture(scope='module')
def before_333(tmp_path_factory):
    url = os.environ['TEST_DATABASE_URL']
    older = tmp_path_factory.mktemp('older')
    for p in sorted(MIGRATIONS.glob('*.sql')):
        if p.name < CREATOR_MIGRATION:
            shutil.copy(p, older / p.name)
    name = f'{COPY_PREFIX}before_333_{uuid.uuid4().hex[:10]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name}')
    with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True, row_factory=dict_row) as c:
        migrate(c, older)
        yield c
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


def test_the_schema_before_333_fails_r3_on_its_creator_cascades(before_333):
    """Catches: a rule that reads only the current schema, where the cascades of #333 are already SET NULL.

    Six of them reach their organization through a parent, so this covers the walk on real data. low_credit_alerts and
    user_api_keys are not #333 tables: #333 did not touch them, they cascade to this day, and the key_reasons migration
    gives them a reason. csv_imports does not exist yet on this schema. #333 changed five more tables (jobs,
    saved_search_runs, llm_usage, vendor_calls, contact_lookups) that have no tenant path here, so R1 fails them instead.
    """
    found = {r['table'] for r in before_333.execute(CREATOR_CASCADES).fetchall()}
    assert found == {
        'business_profiles',
        'chat_messages',
        'chat_sessions',
        'exclusions',
        'lists',
        'outreach_campaigns',
        'outreach_inbound_messages',
        'outreach_inbox_replies',
        'projects',
        'saved_searches',
        'user_oauth_connections',
        'user_webhooks',
        'low_credit_alerts',
        'user_api_keys',
    }


def test_every_relation_has_the_row_level_security_its_shape_calls_for(db):
    offenders = db.execute(SECURITY_PROBLEMS).fetchall()
    lines = '\n'.join(f'  {r["rel"]}: {r["problem"]}\n      {r["fix"]}' for r in offenders)
    assert not offenders, (
        f'{len(offenders)} relations do not have the row-level security their shape calls for:\n{lines}\n'
        'Add the SQL to a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql. zb.scope derives the policy from '
        'the table: a migration that changes its columns or labels calls it again.'
    )


def test_r5_reads_the_catalog(db):
    db.execute("""
        CREATE TABLE zt_ok (id int, organization_id uuid NOT NULL);
        CREATE TABLE zt_off (id int, organization_id uuid NOT NULL);
        ALTER TABLE zt_off DISABLE ROW LEVEL SECURITY;
        CREATE TABLE zt_wrong (id int, organization_id uuid NOT NULL);
        ALTER POLICY zb_scope ON zt_wrong USING (true);
        CREATE TABLE zt_gone (id int, organization_id uuid NOT NULL);
        DROP POLICY zb_scope ON zt_gone;
        CREATE TABLE zt_extra (id int, organization_id uuid NOT NULL);
        CREATE POLICY zt_wider ON zt_extra USING (true);
        CREATE TABLE zt_unscoped (id int);
        COMMENT ON TABLE zt_unscoped IS 'zb:unscoped global: a test';
        ALTER TABLE zt_unscoped ENABLE ROW LEVEL SECURITY;
        CREATE TABLE zt_unscoped_policy (id int);
        COMMENT ON TABLE zt_unscoped_policy IS 'zb:unscoped global: a test';
        CREATE POLICY zb_scope ON zt_unscoped_policy USING (true);
        CREATE TABLE zt_private (id int, user_id uuid NOT NULL);
        COMMENT ON TABLE zt_private IS 'zb:private user_id: a test';
        CREATE TABLE zt_filled (id int, list_id uuid NOT NULL, organization_id uuid NOT NULL);
        CREATE TRIGGER zt_filled_fill BEFORE INSERT ON zt_filled FOR EACH ROW
          EXECUTE FUNCTION fill_organization_id_from_parent('lists', 'list_id');
        ALTER TABLE zt_filled ALTER COLUMN organization_id SET DEFAULT zb.org();
        CREATE TABLE zt_nodefault (id int, organization_id uuid NOT NULL);
        ALTER TABLE zt_nodefault ALTER COLUMN organization_id DROP DEFAULT;
        CREATE MATERIALIZED VIEW zt_mv AS SELECT 1 AS id;
        GRANT SELECT ON zt_mv TO zenbree_app;
        CREATE VIEW zt_v AS SELECT 1 AS id;
        ALTER VIEW zt_v SET (security_invoker = false);
        CREATE TABLE zt_unscoped_child (id int, list_id uuid NOT NULL REFERENCES lists (id));
        COMMENT ON TABLE zt_unscoped_child IS 'zb:unscoped global: a test';
        CREATE TABLE zt_personal_child (id int, list_id uuid NOT NULL REFERENCES lists (id), user_id uuid NOT NULL);
        COMMENT ON TABLE zt_personal_child IS 'zb:personal user_id: a test';
        CREATE TABLE zt_person (id int PRIMARY KEY, user_id uuid NOT NULL);
        COMMENT ON TABLE zt_person IS 'zb:personal user_id: a test';
        CREATE TABLE zt_person_child (id int, person_id int NOT NULL REFERENCES zt_person (id), user_id uuid NOT NULL);
        COMMENT ON TABLE zt_person_child IS 'zb:personal user_id: a test';
        ALTER VIEW zb.live_members RESET (security_invoker);
    """)
    # zb.scope refuses the label on a relation with an organization_id; with the event trigger held off, R5 must
    # still name it.
    db.execute("""
        SELECT set_config('zb.scoping', 'on', false);
        CREATE TABLE zt_unscoped_org (id int, organization_id uuid NOT NULL);
        COMMENT ON TABLE zt_unscoped_org IS 'zb:unscoped global: a test';
        CREATE MATERIALIZED VIEW zt_unscoped_mv AS SELECT NULL::uuid AS organization_id;
        COMMENT ON MATERIALIZED VIEW zt_unscoped_mv IS 'zb:unscoped global: a test';
        CREATE TABLE zt_personal_org (id int, organization_id uuid NOT NULL, user_id uuid NOT NULL);
        COMMENT ON TABLE zt_personal_org IS 'zb:personal user_id: a test';
        CREATE TABLE zt_private_org (id int, organization_id uuid NOT NULL, user_id uuid NOT NULL);
        COMMENT ON TABLE zt_private_org IS 'zb:private user_id: a test';
        CREATE TABLE zt_pio_org (id int, organization_id uuid NOT NULL, user_id uuid NOT NULL);
        COMMENT ON TABLE zt_pio_org IS 'zb:personal-in-org user_id: a test';
        SELECT set_config('zb.scoping', '', false);
    """)
    problems = db.execute(SECURITY_PROBLEMS).fetchall()
    # The label rule by its own row: a held-off label also leaves a policy that differs from the label's shape, and
    # that row alone would keep the relation in the set below.
    on_org_data = {r['rel'] for r in problems if ON_ORGANIZATION_DATA in r['problem']}
    assert {r for r in on_org_data if r.startswith('zt_')} == {
        'zt_unscoped_child',
        'zt_unscoped_org',
        'zt_unscoped_mv',
        'zt_personal_org',
        'zt_private_org',
        'zt_pio_org',
        'zt_personal_child',
    }
    assert 'zb.live_members' in {r['rel'] for r in problems if r['problem'] == 'a view reads its tables as its owner'}
    found = {r['rel'] for r in problems}
    assert {r for r in found if r.startswith('zt_')} == {
        'zt_personal_org',
        'zt_private_org',
        'zt_pio_org',
        'zt_personal_child',
        'zt_unscoped_child',
        'zt_unscoped_org',
        'zt_unscoped_mv',
        'zt_off',
        'zt_wrong',
        'zt_gone',
        'zt_extra',
        'zt_unscoped',
        'zt_unscoped_policy',
        'zt_filled',
        'zt_nodefault',
        'zt_mv',
        'zt_v',
    }
    # A reason does not admit a policy that is not on the list.
    db.execute("COMMENT ON POLICY zt_wider ON zt_extra IS 'zb:extra: a test'")
    db.execute(
        "SELECT zb.scope(t) FROM unnest('{zt_off,zt_wrong,zt_gone,zt_unscoped,zt_unscoped_policy,zt_filled,zt_nodefault}'::regclass[]) t"
    )
    found = {r['rel'] for r in db.execute(SECURITY_PROBLEMS).fetchall()}
    assert {r for r in found if r.startswith('zt_')} == {
        'zt_mv',
        'zt_v',
        'zt_extra',
        'zt_unscoped_child',
        'zt_unscoped_org',
        'zt_unscoped_mv',
        'zt_personal_org',
        'zt_private_org',
        'zt_pio_org',
        'zt_personal_child',
    }


LABELS_ON_ORGANIZATION_DATA = [
    "COMMENT ON TABLE chat_sessions IS 'zb:personal user_id: a test'",
    "COMMENT ON TABLE chat_sessions IS 'zb:private user_id: a test'",
    "COMMENT ON TABLE lists IS 'zb:personal-in-org user_id: a test'",
    "COMMENT ON TABLE chat_messages IS 'zb:unscoped global: a test'",
    "COMMENT ON TABLE lists IS 'zb:unscoped identity: a test'",
    "COMMENT ON TABLE lists IS ' zb:whatever: a test'",
]


@pytest.mark.parametrize('label', LABELS_ON_ORGANIZATION_DATA)
def test_a_label_on_organization_data_is_refused(db, label):
    """Catches: a COMMENT that replaces a table's organization rule. zb:unscoped turns row-level security off and keeps
    zenbree_app's grants; zb:personal and zb:private drop organization_id = zb.org(), so a teammate reads the person's
    rows of every organization and the person writes into one they are not in; zb:personal-in-org lets the person
    write their own rows into any of their organizations."""
    table = label.split()[3]
    policy = 'SELECT p.oid, pg_get_expr(p.polqual, p.polrelid) AS qual, c.relrowsecurity FROM pg_class c'
    policy += " LEFT JOIN pg_policy p ON p.polrelid = c.oid AND p.polname = 'zb_scope' WHERE c.oid = %s::regclass"
    before = db.execute(policy, (table,)).fetchone()
    with pytest.raises(psycopg.errors.RaiseException, match=r'cannot carry .*only label allowed on organization data'):
        db.execute(label)
    assert db.execute(policy, (table,)).fetchone() == before
    assert before['relrowsecurity'] and before['qual'] == '(organization_id = zb.org())'


def test_the_reviewed_personal_in_org_label_stays(db):
    """The one label allowed on organization data, set again: zb.scope keeps notifications as it is."""
    label = db.execute("SELECT obj_description('notifications'::regclass, 'pg_class') AS d").fetchone()['d']
    db.execute(sql.SQL('COMMENT ON TABLE notifications IS {}').format(sql.Literal(label)))
    assert not [r for r in db.execute(SECURITY_PROBLEMS).fetchall() if r['rel'] == 'notifications']


def test_personal_in_org_matches_the_list_in_zb_scope(db):
    """Catches: one list widened and not the other, so the rule and the function disagree on what is allowed."""
    src = db.execute("SELECT prosrc FROM pg_proc WHERE oid = 'zb.scope(regclass)'::regprocedure").fetchone()['prosrc']
    listed = db.execute(
        "SELECT substring(%s FROM 'personal_in_org CONSTANT text\\[\\] := ARRAY\\[([^]]*)\\]') AS l", (src,)
    ).fetchone()['l']
    assert listed is not None, 'zb.scope no longer declares personal_in_org CONSTANT text[] := ARRAY[...]'
    assert tuple(x.strip().strip("'") for x in listed.split(',')) == PERSONAL_IN_ORG


def test_every_label_is_on_the_reviewed_list(db):
    found: dict[str, set[str]] = {}
    for r in db.execute(LABELS).fetchall():
        found.setdefault(r['kind'], set()).add(r['rel'])
    expected = {kind: set(rels) for kind, rels in LABELLED.items()}
    listed = '\n'.join(f'  {kind}: {", ".join(rels)}' for kind, rels in LABELLED.items())
    assert found == expected, (
        f'The labelled relations differ from LABELLED in {__name__}.\n'
        f'Found:\n' + '\n'.join(f'  {k}: {", ".join(sorted(v))}' for k, v in sorted(found.items())) + '\n'
        f'LABELLED:\n{listed}\n'
        'A label replaces the organization rule or turns row-level security off, so each one is reviewed: add the '
        "relation to LABELLED in the same PR as the label, with the owner's approval, or remove the label."
    )


def test_the_label_list_reads_the_catalog(db):
    db.execute("""
        CREATE TABLE zt_person (id int, user_id uuid NOT NULL);
        COMMENT ON TABLE zt_person IS 'zb:personal user_id: a test';
        CREATE VIEW zt_view AS SELECT 1 AS id;
        COMMENT ON VIEW zt_view IS 'zb:unscoped global: a test';
        CREATE TABLE zt_odd (id int);
        COMMENT ON TABLE zt_odd IS 'ZB:Personal user_id: a test';
    """)
    found = {(r['kind'], r['rel']) for r in db.execute(LABELS).fetchall() if r['rel'].startswith('zt_')}
    assert found == {('personal', 'zt_person'), ('unscoped', 'zt_view'), ('ZB:Personal user_id: a test', 'zt_odd')}


def test_every_trigger_on_member_or_user_that_writes_a_tenant_table_has_a_reason(db):
    offenders = db.execute(CALLER_WRITES).fetchall()
    lines = '\n'.join(
        f'  {r["function"]} on {r["table"]} writes {r["writes"]}:\n'
        f"      COMMENT ON FUNCTION {r['function']} IS 'zb:runs-as-caller: <why>';"
        for r in offenders
    )
    assert not offenders, (
        f'{len(offenders)} trigger functions on member or "user" write a tenant table under the policies of whoever '
        f'deletes the row, so as zenbree_app they reach only zb.org():\n{lines}\n'
        'Say who fires it and why that reach is right, in a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql.'
    )


def test_r6_reads_the_trigger_source(db):
    db.execute("""
        CREATE TABLE zt_t (id int, organization_id uuid NOT NULL);
        CREATE FUNCTION zt_writes() RETURNS trigger LANGUAGE plpgsql AS
          $$ BEGIN DELETE FROM zt_t WHERE id = 1; UPDATE settings SET value = value; RETURN OLD; END $$;
        CREATE FUNCTION zt_reads() RETURNS trigger LANGUAGE plpgsql AS
          $$ BEGIN PERFORM 1 FROM zt_t; RETURN OLD; END $$;
        CREATE TRIGGER zt_w AFTER DELETE ON member FOR EACH ROW EXECUTE FUNCTION zt_writes();
        CREATE TRIGGER zt_r AFTER DELETE ON "user" FOR EACH ROW EXECUTE FUNCTION zt_reads();
    """)
    found = {(r['function'], r['writes']) for r in db.execute(CALLER_WRITES).fetchall()}
    assert found == {('zt_writes()', 'zt_t')}
    db.execute("COMMENT ON FUNCTION zt_writes() IS 'zb:runs-as-caller: a test'")
    assert not db.execute(CALLER_WRITES).fetchall()


def test_the_app_role_has_no_grant_on_the_secret_tables(db):
    offenders = [r['table'] for r in db.execute(SECRET_GRANTS).fetchall()]
    lines = '\n'.join(f'  {t}: REVOKE ALL ON {t} FROM zenbree_app, PUBLIC;' for t in offenders)
    assert not offenders, (
        f'zenbree_app can reach {len(offenders)} tables of keys, tokens, sessions or migrations:\n{lines}\n'
        'Add the SQL to a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql.'
    )


@pytest.mark.parametrize(
    'grant',
    [
        'GRANT SELECT ON account TO zenbree_app',
        'GRANT SELECT (id) ON session TO zenbree_app',
        'GRANT SELECT ON jwks TO PUBLIC',
        'GRANT TRUNCATE ON verification TO zenbree_app',
    ],
)
def test_r7_counts_every_kind_of_grant(db, grant):
    db.execute(grant)
    assert len(db.execute(SECRET_GRANTS).fetchall()) == 1


def test_every_guard_trigger_is_in_place(db):
    offenders = db.execute(GUARD_PROBLEMS).fetchall()
    lines = '\n'.join(f'  {r["trigger"]}: {r["problem"]}' for r in offenders)
    assert not offenders, (
        f'{len(offenders)} triggers that row-level security depends on are not in place:\n{lines}\n'
        'A policy sees only the new row of an UPDATE, so the trigger is what keeps a teammate from taking a row over. '
        'Restore it in a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql; for a state other than O: '
        'ALTER TABLE <table> ENABLE TRIGGER <name>.'
    )


@pytest.mark.parametrize(
    ('change', 'problem'),
    [
        ('DROP TRIGGER zb_keep_hubspot_owner ON user_oauth_connections', 'missing'),
        ('ALTER TABLE user_oauth_connections DISABLE TRIGGER zb_keep_hubspot_owner', 'disabled'),
        (
            'ALTER TABLE user_oauth_connections ENABLE REPLICA TRIGGER zb_keep_hubspot_owner',
            'enabled REPLICA: a normal session skips it',
        ),
        (
            'ALTER TABLE user_oauth_connections ENABLE ALWAYS TRIGGER zb_keep_hubspot_owner',
            'enabled ALWAYS: it fires in replica sessions too',
        ),
        ("COMMENT ON FUNCTION zb.keep_hubspot_owner() IS 'a test'", 'its function has no zb:guard: reason'),
    ],
)
def test_r8_reads_the_catalog(db, change, problem):
    db.execute(change)
    assert [(r['trigger'], r['problem']) for r in db.execute(GUARD_PROBLEMS).fetchall()] == [
        ('user_oauth_connections.zb_keep_hubspot_owner', problem)
    ]


def test_no_privilege_runs_around_the_policies(db):
    offenders = db.execute(privilege_problems()).fetchall()
    lines = '\n'.join(f'  {r["object"]} {r["problem"]}:\n      {r["fix"]}' for r in offenders)
    assert not offenders, (
        f'zenbree_app or zenbree_system can read or write around row-level security ({len(offenders)}):\n'
        f'{lines}\n'
        'A SECURITY DEFINER function and a rule run as their owner, the superuser, in any schema: write a SECURITY '
        'INVOKER function or a trigger, or ask the user to add an entry to DEFINER_FUNCTIONS or RULES in this file '
        'with its reason. A privilege zenbree_system needs goes in SYSTEM_PRIVILEGES with its reason, also with the '
        "user's approval. Outside public the app gets no privilege at all: row-level security is installed there "
        'only. Add the SQL to a new migration api/db/<date -u +%Y%m%d%H%M%S>_<name>.sql.'
    )


def _problems(db, roles=APP_ROLES):
    return {(r['object'], r['problem']) for r in db.execute(privilege_problems(roles)).fetchall()}


DEFINER = 'is SECURITY DEFINER: it runs as postgres, around every policy'
OWNED = 'is owned by zenbree_app, which skips the policies of what it owns'
# What the owner holds after ALTER ... OWNER TO: the owner's own grant moves with it.
OWNER_TABLE_GRANTS = {f'grants {p} to zenbree_app' for p in ('TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')}
OUTSIDE = ': row-level security is installed in public only'
RULE = 'is a rule: its action runs as postgres, around every policy'


@pytest.mark.parametrize(
    ('change', 'expected'),
    [
        (
            'CREATE FUNCTION zt_every_list() RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER'
            ' AS $$ SELECT id FROM public.lists $$',
            {('zt_every_list()', DEFINER)},
        ),
        (
            'CREATE FUNCTION zb.zt_definer() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$',
            {('zb.zt_definer()', DEFINER)},
        ),
        ('GRANT TRUNCATE ON lists TO zenbree_app', {('lists', 'grants TRUNCATE to zenbree_app')}),
        ('GRANT TRIGGER ON exclusions TO zenbree_system', {('exclusions', 'grants TRIGGER to zenbree_system')}),
        (
            'GRANT ALL ON saved_searches TO PUBLIC',
            {('saved_searches', f'grants {p} to PUBLIC') for p in ('TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')},
        ),
        (
            'GRANT REFERENCES (id) ON lists TO zenbree_app',
            {('lists.id', 'grants REFERENCES on the column to zenbree_app')},
        ),
        ('GRANT CREATE ON SCHEMA public TO zenbree_app', {('public', 'grants CREATE on the schema to zenbree_app')}),
        ('ALTER TABLE lists OWNER TO zenbree_app', {('lists', OWNED)} | {('lists', g) for g in OWNER_TABLE_GRANTS}),
        ('ALTER FUNCTION zb.org() OWNER TO zenbree_app', {('zb.org()', OWNED)}),
        (
            'ALTER SCHEMA zb OWNER TO zenbree_app',
            {('zb', OWNED), ('zb', 'grants CREATE on the schema to zenbree_app')},
        ),
        # A schema the app has no USAGE on: a view in public still reaches the function by its OID (review round 4).
        (
            'CREATE SCHEMA zt_private; CREATE FUNCTION zt_private.f() RETURNS int LANGUAGE sql SECURITY DEFINER'
            ' AS $$ SELECT 1 $$',
            {('zt_private.f()', DEFINER)},
        ),
        (
            'CREATE SCHEMA zt_private; GRANT USAGE ON SCHEMA zt_private TO zenbree_app',
            {('zt_private', 'grants USAGE on the schema to zenbree_app')},
        ),
        (
            'CREATE SCHEMA zt_private; CREATE TABLE zt_private.t (id int); GRANT SELECT ON zt_private.t TO zenbree_app',
            {('zt_private.t', f'grants SELECT to zenbree_app{OUTSIDE}')},
        ),
        (
            'CREATE TABLE zb.zt_t (id int); GRANT SELECT ON zb.zt_t TO PUBLIC',
            {('zb.zt_t', f'grants SELECT to PUBLIC{OUTSIDE}')},
        ),
        (
            'CREATE TABLE zb.zt_t (id int); GRANT INSERT (id) ON zb.zt_t TO zenbree_system',
            {('zb.zt_t.id', f'grants INSERT on the column to zenbree_system{OUTSIDE}')},
        ),
        # The places R5 checks: a view of zb, and a view or a materialized view of public, whose SELECT rule is the
        # view itself.
        (
            'CREATE VIEW zb.zt_v WITH (security_invoker = true) AS SELECT 1 AS x; GRANT SELECT ON zb.zt_v TO zenbree_app',
            set(),
        ),
        ('CREATE VIEW zt_v WITH (security_invoker = true) AS SELECT id FROM lists', set()),
        ('CREATE MATERIALIZED VIEW zt_m AS SELECT id FROM lists', set()),
        (
            'CREATE FOREIGN DATA WRAPPER zt_w; CREATE SERVER zt_s FOREIGN DATA WRAPPER zt_w;'
            ' CREATE FOREIGN TABLE zt_f (id int) SERVER zt_s; GRANT SELECT ON zt_f TO zenbree_app',
            {('zt_f', 'grants SELECT to zenbree_app: a foreign table holds rows no policy here limits')},
        ),
        (
            'CREATE RULE zt_touch AS ON UPDATE TO lists DO ALSO'
            ' UPDATE projects SET name = name WHERE projects.user_id = NEW.user_id',
            {('lists.zt_touch', RULE)},
        ),
        (
            'CREATE TABLE zb.zt_seen (id uuid); CREATE RULE zt_copy AS ON INSERT TO zb.zt_seen DO INSTEAD NOTHING',
            {('zb.zt_seen.zt_copy', RULE)},
        ),
        (
            'ALTER DEFAULT PRIVILEGES GRANT TRUNCATE ON TABLES TO zenbree_app',
            {('default privileges of postgres on tables', 'grant TRUNCATE to zenbree_app')},
        ),
        # zb.grant gives the same in public; with no schema it also reaches a table made anywhere else.
        ('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO zenbree_app', set()),
        (
            'ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO zenbree_app',
            {('default privileges of postgres on tables', 'grant SELECT to zenbree_app')},
        ),
        (
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO PUBLIC',
            {('default privileges of postgres in public on sequences', 'grant SELECT to PUBLIC')},
        ),
        ('ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC', set()),
    ],
)
def test_r9_reads_the_catalog(db, change, expected):
    """Each change lives in this test's database copy: grants, owners, rules and default privileges are per database,
    and the roles are not changed. The case proves which rows the change adds, no more and no fewer; the rule above,
    not this test, proves the catalog is clean. A problem already in the catalog can fail a case too: when it is on
    the object the case changes (its row was there before), or when it changes what the case creates (a default
    privilege adds a grant to each new relation)."""
    before = _problems(db)
    db.execute(change)
    assert _problems(db) - before == expected


@pytest.mark.parametrize(
    ('grant', 'expected'),
    [
        (
            'GRANT SET ON PARAMETER session_replication_role TO zenbree_app',
            ('parameter session_replication_role', 'grants SET on the parameter to zenbree_app'),
        ),
        (
            'GRANT ALTER SYSTEM ON PARAMETER work_mem TO PUBLIC',
            ('parameter work_mem', 'grants ALTER SYSTEM on the parameter to PUBLIC'),
        ),
    ],
)
def test_r9_reads_the_parameter_grants(db, grant, expected):
    """pg_parameter_acl is shared by the whole cluster, so the grant runs in a transaction that rolls back: no other
    database of the shared cluster sees it, and it never stays on the real role."""
    before = _problems(db)
    with db.transaction(force_rollback=True):
        db.execute(grant)
        assert _problems(db) - before == {expected}
    assert _problems(db) == before


def test_r9_counts_a_membership_direct_or_through_another_role(db):
    """On throwaway roles, because a membership is cluster-wide. R7 alone missed GRANT pg_read_all_data TO
    zenbree_app: a NOINHERIT role gets a grant without INHERIT, so has_table_privilege counts nothing, while SET ROLE
    pg_read_all_data reads every table."""
    tag = uuid.uuid4().hex[:12]
    probe, middle, login = f'zt_member_{tag}', f'zt_middle_{tag}', f'zt_login_{tag}'
    try:
        db.execute(f"""
            CREATE ROLE {probe} NOLOGIN NOINHERIT; CREATE ROLE {middle} NOLOGIN; CREATE ROLE {login} NOLOGIN;
            GRANT pg_read_all_data TO {probe}; GRANT pg_write_all_data TO {middle}; GRANT {middle} TO {probe};
            GRANT {probe} TO {login};
        """)
        assert {(o, p) for o, p in _problems(db, (probe,)) if o == probe} == {
            (probe, 'is a member of pg_read_all_data'),
            (probe, f'is a member of {middle}'),
            (probe, 'is a member of pg_write_all_data, through another role'),
        }
    finally:
        db.execute(f'DROP ROLE IF EXISTS {login}, {probe}, {middle}')
