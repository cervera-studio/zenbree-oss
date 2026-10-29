-- Segments (#938): two person lists of the user's first client. Marco Bianchi is in both, tied by his LinkedIn
-- URL: a CEO with fit 85 and no email in `Segmenti A`, with an email and fit 60 in `Segmenti B`. Giulia Verdi is a
-- CEO with an email and fit 90 in `Segmenti A`. Luca Neri is a CTO in `Segmenti B`, and a CEO with fit 95 on an
-- excluded row of `Segmenti A`: no rule reads that row, so he matches no CEO rule until it is included again.
-- `Senza alias` has no exact alias, so no identity. `Segmenti A` has the AI column `uses_hubspot`, empty for everyone.
-- Run: bin/stack psql -v email=<user email> -f - < features/segments-seed.sql
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE ctx ON COMMIT DROP AS
SELECT u.id AS uid, default_organization(u.id) AS org,
       (SELECT id FROM projects WHERE user_id = u.id ORDER BY created_at LIMIT 1) AS pid,
       gen_random_uuid() AS a, gen_random_uuid() AS b
FROM "user" u WHERE u.email = :'email';
INSERT INTO lists (id, organization_id, user_id, project_id, name, entity)
SELECT a, org, uid, pid, 'Segmenti A', 'person' FROM ctx
UNION ALL SELECT b, org, uid, pid, 'Segmenti B', 'person' FROM ctx;
INSERT INTO list_columns (list_id, key, label, kind, position) SELECT a, 'uses_hubspot', 'Usa HubSpot', 'ai', 1 FROM ctx;
INSERT INTO list_rows (list_id, cells, dedupe_key, relevance_score, excluded)
SELECT a, '{"full_name": "Marco Bianchi", "title": "CEO", "linkedin_url": "https://www.linkedin.com/in/marco-bianchi/"}'::jsonb,
       'linkedin.com/in/marco-bianchi', 85, false FROM ctx
UNION ALL
SELECT a, '{"full_name": "Giulia Verdi", "title": "Co-founder & CEO", "email": "giulia@acme.verify.test"}'::jsonb,
       'giulia@acme.verify.test', 90, false FROM ctx
UNION ALL
SELECT a, '{"full_name": "Luca Neri", "title": "CEO", "email": "luca@acme.verify.test"}'::jsonb, 'luca@acme.verify.test', 95,
       true FROM ctx
UNION ALL
SELECT b, '{"full_name": "Marco Bianchi", "email": "marco@acme.verify.test", "linkedin_url": "https://www.linkedin.com/in/marco-bianchi/"}'::jsonb,
       'marco@acme.verify.test', 60, false FROM ctx
UNION ALL
SELECT b, '{"full_name": "Luca Neri", "title": "CTO", "email": "luca@acme.verify.test"}'::jsonb, 'luca@acme.verify.test', 70, false FROM ctx
UNION ALL
SELECT b, '{"full_name": "Senza alias", "title": "CEO"}'::jsonb, 'senza-alias', 95, false FROM ctx;
-- A row written by SQL is not marked for the link beat: link it here, as the beat would.
SELECT count(audience_link_row(r.id)) AS linked FROM list_rows r JOIN ctx ON r.list_id IN (ctx.a, ctx.b);
SELECT a AS list_a, b AS list_b FROM ctx;
COMMIT;
