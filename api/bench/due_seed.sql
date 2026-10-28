-- Run after bench/jit_seed.sql, for bench/due.py (#1127): people the dispatcher owes a step, and people of the heavy
-- client enrolled in more than one of its campaigns.
-- Usage: psql -f bench/due_seed.sql (about 20 seconds)
\set ON_ERROR_STOP on
SET session_replication_role = replica;

-- jit_seed's steps have no send_after_days, so no step is ever due. Step 3 comes 3 days after step 2's send (a day
-- ago in the seed): the bench asks at now() + 4 days.
UPDATE outreach_campaigns SET steps =
    '[{"channel": "email", "send_after_days": 0}, {"channel": "email", "send_after_days": 3},
      {"channel": "email", "send_after_days": 6}]';

-- The heavy client's campaigns armed one minute apart, in their number's order, so Campaign 1 holds its people.
UPDATE outreach_campaigns c SET activated_at = now() - interval '10 days' + split_part(c.name, ' ', 2)::int * interval '1 minute'
FROM projects p WHERE p.id = c.project_id AND p.name = 'Client 1'
  AND p.organization_id = (SELECT id FROM organization WHERE slug = 'org-0');

-- One person in 7 of every later campaign has the email of Campaign 1's person of the same number, and one in 11 its
-- LinkedIn URL, written another way.
UPDATE outreach_campaign_targets t
SET email = CASE WHEN n.t % 7 = 0 THEN upper(n.first_email) ELSE t.email END,
    linkedin_url = CASE WHEN n.t % 11 = 0 THEN replace(n.first_linkedin, 'https://', 'www.') || '/' ELSE t.linkedin_url END
FROM (SELECT o.id, f.email AS first_email, f.linkedin_url AS first_linkedin, substring(o.first_name FROM 2)::int AS t
      FROM outreach_campaign_targets o
      JOIN outreach_campaigns oc ON oc.id = o.campaign_id
      JOIN projects p ON p.id = oc.project_id AND p.name = 'Client 1'
      JOIN organization org ON org.id = p.organization_id AND org.slug = 'org-0'
      JOIN outreach_campaigns first ON first.project_id = oc.project_id AND first.name = 'Campaign 1'
      JOIN outreach_campaign_targets f ON f.campaign_id = first.id AND f.first_name = o.first_name
      WHERE oc.id <> first.id) n
WHERE t.id = n.id AND (n.t % 7 = 0 OR n.t % 11 = 0);

SET session_replication_role = origin;
ANALYZE outreach_campaigns, outreach_campaign_targets;

SELECT count(*) FILTER (WHERE email ~ '[A-Z]') AS email_duplicates,
       count(*) FILTER (WHERE linkedin_url LIKE 'www.%') AS linkedin_duplicates
FROM outreach_campaign_targets;
