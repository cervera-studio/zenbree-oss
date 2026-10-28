-- Seed for the JIT benchmark (bench/jit.py, #1049) on a migrated, empty, disposable database.
-- Many clients: :orgs organizations of 5 projects each, plus one agency of 50 projects that carries the
-- #1048 hold load (10k aliases, 2k held Instantly leads and 500 stops per project). Each project has 4 campaigns of
-- :targets people, except the agency's heavy client (Client 1): 100 campaigns of 1,000 people, as campaigns pile up.
-- Usage: psql -v orgs=100 -v targets=1000 -f bench/jit_seed.sql (about 2 minutes, 2.3M people, 3.8M sends)
\set ON_ERROR_STOP on
-- Triggers off: the home notifies and fill triggers are not under test, and organization_id is given.
SET session_replication_role = replica;

CREATE TEMP TABLE b_org AS
SELECT gen_random_uuid() AS id, i AS n, i = 0 AS agency FROM generate_series(0, :orgs) i;
INSERT INTO organization (id, name, slug, "createdAt")
SELECT id, 'Org ' || n, 'org-' || n, now() FROM b_org;
INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt")
SELECT id, 'Owner ' || n, 'owner-' || n || '@bench.test', true, now(), now() FROM b_org;
INSERT INTO member (id, "organizationId", "userId", role, "createdAt")
SELECT gen_random_uuid(), id, id, 'owner', now() FROM b_org;

CREATE TEMP TABLE b_project AS
SELECT gen_random_uuid() AS id, o.id AS org, o.agency, p AS n, o.agency AND p = 1 AS heavy
FROM b_org o, generate_series(1, 50) p WHERE p <= CASE WHEN o.agency THEN 50 ELSE 5 END;
INSERT INTO projects (id, user_id, name, organization_id)
SELECT id, org, 'Client ' || n, org FROM b_project;
INSERT INTO lists (id, user_id, name, entity, project_id, organization_id)
SELECT id, org, 'List ' || n, 'person', id, org FROM b_project;

CREATE TEMP TABLE b_campaign AS
SELECT gen_random_uuid() AS id, p.id AS project, p.org, c AS n, CASE WHEN p.heavy THEN 1000 ELSE :targets END AS size
FROM b_project p, generate_series(1, 100) c WHERE c <= CASE WHEN p.heavy THEN 100 ELSE 4 END;
INSERT INTO outreach_campaigns (id, owner_id, name, status, steps, approve_before_sending, activated_at, created_at,
                                list_id, organization_id, project_id)
SELECT id, org, 'Campaign ' || n, 'active',
       '[{"channel": "email", "delay_days": 0}, {"channel": "email", "delay_days": 3},
         {"channel": "linkedin_message", "delay_days": 3}]',
       n % 2 = 0, now() - interval '10 days', now() - n * interval '1 hour', project, org, project
FROM b_campaign;

-- Statuses and steps spread so every subquery of campaign_query has rows: one in ten replied, one in twenty
-- excluded, the rest active between step 1 and 3.
INSERT INTO outreach_campaign_targets (id, campaign_id, owner_id, email, linkedin_url, first_name, status, next_step,
                                       replied_at, created_at, organization_id)
SELECT gen_random_uuid(), c.id, c.org, 'p' || t || '-' || c.n || '@' || left(c.project::text, 8) || '.test',
       'https://linkedin.com/in/p' || t || '-' || left(c.id::text, 8), 'P' || t,
       CASE WHEN t % 10 = 0 THEN 'replied' WHEN t % 20 = 1 THEN 'excluded' ELSE 'active' END,
       1 + t % 3, CASE WHEN t % 10 = 0 THEN now() - interval '1 day' END, now() - interval '9 days', c.org
FROM b_campaign c, generate_series(1, c.size) t;

-- One send per step the target passed, the last one waiting for approval on approval campaigns, a terminal
-- failure on one target in fifty.
INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, attempts, recipient,
                                     idempotency_key, organization_id, attempted_at, delivered_at)
SELECT t.campaign_id, t.owner_id, t.id, s, 'email',
       CASE WHEN s < t.next_step THEN 'sent'
            WHEN abs(hashtext(t.id::text)) % 50 = 0 THEN 'failed'
            WHEN c.approve_before_sending THEN 'awaiting_approval' ELSE 'approved' END,
       CASE WHEN abs(hashtext(t.id::text)) % 50 = 0 AND s = t.next_step THEN 5 ELSE 1 END,
       t.email, t.id || ':' || s, t.organization_id, now() - interval '1 day',
       CASE WHEN s < t.next_step THEN now() - interval '1 day' END
FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id, generate_series(1, 2) s
WHERE s <= t.next_step;

-- The #1048 hold load: on every agency project, 10k aliases (5k people with an email and a LinkedIn alias),
-- 2k held Instantly leads and 500 stops; 500 aliases, 50 leads and 10 stops on each small client.
INSERT INTO audience_aliases (organization_id, project_id, identity_id, entity, kind, namespace, value, source)
SELECT p.org, p.id, i.identity, 'person', k.kind, '',
       CASE k.kind WHEN 'email' THEN 'h' || i.n || '@' || left(p.id::text, 8) || '.test'
                   ELSE 'https://linkedin.com/in/h' || i.n || '-' || left(p.id::text, 8) END, 'bench'
FROM b_project p
CROSS JOIN LATERAL (SELECT n, gen_random_uuid() AS identity
                    FROM generate_series(1, CASE WHEN p.agency THEN 5000 ELSE 250 END) n) i
CROSS JOIN (VALUES ('email'), ('linkedin_url')) k(kind);

INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email, outcome)
SELECT p.org, p.id, gen_random_uuid(), 'ws', 'ic', 'h' || g.i || '@' || left(p.id::text, 8) || '.test',
       (ARRAY['publishing', 'accepted', 'uncertain'])[1 + g.i % 3]
FROM b_project p, generate_series(1, 2000) g(i) WHERE g.i <= CASE WHEN p.agency THEN 2000 ELSE 50 END;

INSERT INTO outreach_stops (organization_id, project_id, connection_id, lead_id, email, reason, cause, status,
                            last_error_code, confirmed_at, confirmed_by)
SELECT p.org, p.id, gen_random_uuid(), gen_random_uuid(), 'h' || (2000 + g.i) || '@' || left(p.id::text, 8) || '.test',
       'human_reply', 'reply', (ARRAY['pending', 'failed', 'confirmed'])[1 + g.i % 3],
       CASE WHEN g.i % 3 = 1 THEN 'BENCH' END, CASE WHEN g.i % 3 = 2 THEN now() END, CASE WHEN g.i % 3 = 2 THEN 'provider' END
FROM b_project p, generate_series(1, 500) g(i) WHERE g.i <= CASE WHEN p.agency THEN 500 ELSE 10 END;

SET session_replication_role = origin;
ANALYZE;

SELECT (SELECT count(*) FROM organization) AS orgs, (SELECT count(*) FROM projects) AS projects,
       (SELECT count(*) FROM outreach_campaigns) AS campaigns, (SELECT count(*) FROM outreach_campaign_targets) AS targets,
       (SELECT count(*) FROM outreach_campaign_sends) AS sends, (SELECT count(*) FROM audience_aliases) AS aliases;
