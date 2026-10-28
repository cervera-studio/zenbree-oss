-- #747 bounces: an active campaign «Verify bounces» that sent 20 step-1 emails, an hour ago, from a send-only Gmail
-- mailbox (sales@zenbree.local, connected before the read scope) to 20 verified addresses of the seed list
-- «Agenzie marketing Milano · SDR», each charged 1 credit by a settled hold. Step 2 waits 30 days, so the beat sends
-- nothing. Run on the dev seed: bin/stack psql -f - < features/campaign-bounces-seed.sql
\set ON_ERROR_STOP on
SELECT u.id AS uid, default_organization(u.id) AS org, l.project_id AS pid, l.id AS lid
FROM "user" u JOIN lists l ON l.user_id = u.id AND l.name = 'Agenzie marketing Milano · SDR'
WHERE u.email = 'dev@zenbree.local' \gset
BEGIN;
CREATE TEMP TABLE picked AS
  SELECT id, email, first_name, row_number() OVER (ORDER BY id) AS n FROM (
    SELECT DISTINCT ON (lower(cells->>'email')) id, lower(cells->>'email') AS email,
           coalesce(cells->>'first_name', split_part(cells->>'full_name', ' ', 1), 'P') AS first_name
    FROM list_rows WHERE list_id = :'lid' AND cells->>'email_status' = 'verified' ORDER BY lower(cells->>'email'), id) d
  ORDER BY id LIMIT 20;
CREATE TEMP TABLE h AS SELECT reserve_credits(:'org', :'uid', 20, 'job', NULL) AS id;
INSERT INTO row_charges (hold_id, row_id, keys, credits, state, settled_at, organization_id)
  SELECT h.id, p.id, '{email,email_status,email_source}', 1, 'charged', now(), :'org' FROM picked p, h;
SELECT settle_hold((SELECT id FROM h));
INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted, granted_scopes, account_label,
    provider_account_id, status, created_at, connected_at, display_name)
  VALUES (:'org', :'uid', 'google', 'x', '{openid,email,https://www.googleapis.com/auth/gmail.send}', 'sales@zenbree.local',
    'g-verify-747', 'connected', now() - interval '20 days', now() - interval '20 days', 'Dev Sales');
INSERT INTO project_senders (project_id, sender_id, provider, organization_id)
  SELECT :'pid', id, provider, organization_id FROM user_oauth_connections WHERE provider_account_id = 'g-verify-747';
INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, approve_before_sending, activated_at, list_id)
  VALUES (:'org', :'pid', :'uid', 'Verify bounces', 'active',
    '[{"step":1,"channel":"email","subject":"Ciao {{firstName}}","body":"Ciao {{firstName}}, una domanda veloce.","send_after_days":0},
      {"step":2,"channel":"email","subject":"","body":"Ti riscrivo, {{firstName}}","send_after_days":30,"threaded":true}]',
    true, now() - interval '2 hours', :'lid');
INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)
  SELECT c.id, u.id, c.project_id, u.provider, c.organization_id FROM outreach_campaigns c, user_oauth_connections u
  WHERE c.name = 'Verify bounces' AND u.provider_account_id = 'g-verify-747';
INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, status, next_step, sender_id, sender_pinned_at, source)
  SELECT c.id, :'uid', p.email, p.first_name, 'active', 2, u.id, now() - interval '1 hour', 'manual' FROM picked p, outreach_campaigns c, user_oauth_connections u
  WHERE c.name = 'Verify bounces' AND u.provider_account_id = 'g-verify-747';
INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient, subject, body, idempotency_key,
    approved_at, attempted_at, delivered_at, message_id, sender_provider, sender_id, attempts)
  SELECT t.campaign_id, :'uid', t.id, 1, 'email', 'sent', t.email, 'Ciao', 'Ciao', gen_random_uuid()::text,
    now() - interval '1 hour', now() - interval '1 hour', now() - interval '1 hour', '<v' || p.n || '@zenbree.local>', 'google', u.id, 1
  FROM outreach_campaign_targets t JOIN picked p ON p.email = t.email
  JOIN user_oauth_connections u ON u.provider_account_id = 'g-verify-747';
COMMIT;
SELECT s.message_id, s.recipient FROM outreach_campaign_sends s JOIN outreach_campaigns c ON c.id = s.campaign_id
 WHERE c.name = 'Verify bounces' ORDER BY s.message_id LIMIT 3;
