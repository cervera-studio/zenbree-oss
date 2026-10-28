-- Run after bench/due_seed.sql, for bench/opt_outs.py (#1121): the agency's owner has HubSpot connected, every person
-- of the agency has a verdict, and a person identity ties each address to the target's LinkedIn profile and to a
-- second address. One person in 50 opted out under their second address, which only the aliases reach, and one in
-- 50 under the address the target holds.
-- A clear verdict counts for the plan only when it was read in this beat: bench/due.py asks at now() + 4 days.
-- Usage: psql -f bench/opt_out_seed.sql (about 20 seconds)
\set ON_ERROR_STOP on
SET session_replication_role = replica;

CREATE TEMP TABLE b_person AS
SELECT DISTINCT ON (c.project_id, t.email_key) c.organization_id AS org, c.project_id, c.owner_id, t.email_key,
       t.linkedin_alias, gen_random_uuid() AS identity, row_number() OVER () AS n
FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id
WHERE c.organization_id = (SELECT id FROM organization WHERE slug = 'org-0') AND t.email_key IS NOT NULL
ORDER BY c.project_id, t.email_key, t.created_at;

INSERT INTO user_oauth_connections (user_id, organization_id, provider, refresh_token_encrypted, status)
SELECT DISTINCT owner_id, org, 'hubspot', 'bench', 'connected' FROM b_person;

INSERT INTO audience_identities (id, organization_id, project_id, entity)
SELECT identity, org, project_id, 'person' FROM b_person;

INSERT INTO audience_aliases (organization_id, project_id, identity_id, entity, kind, namespace, value, source)
SELECT org, project_id, identity, 'person', k.kind, '', k.value, 'bench'
FROM b_person
CROSS JOIN LATERAL (VALUES ('email', email_key), ('email', 'alt.' || email_key),
                           ('linkedin_url', linkedin_alias)) k(kind, value)
WHERE k.value IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at)
SELECT owner_id, CASE WHEN n % 50 = 0 THEN 'alt.' || email_key ELSE email_key END,
       CASE WHEN n % 50 IN (0, 25) THEN 'HUBSPOT_OPTED_OUT' END, now() + interval '5 days'
FROM b_person
ON CONFLICT DO NOTHING;

ANALYZE user_oauth_connections, audience_identities, audience_aliases, crm_gate_checks;
SELECT (SELECT count(*) FROM b_person) AS people,
       (SELECT count(*) FROM crm_gate_checks WHERE reason = 'HUBSPOT_OPTED_OUT') AS opted_out;
