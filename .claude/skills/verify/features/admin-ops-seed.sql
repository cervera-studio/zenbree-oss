-- Setup for admin-ops.md: one chat for ale@verify.local with an ask_user question, tool calls (three enrich_email
-- batches: all lookups failed, one of ten failed, none failed), two jobs
-- (one failed with stages), a list, vendor calls, a purchase, LLM usage, contact lookups and three
-- settings (aiark on, apollo off, a non-boolean key). Needs adm@verify.local and ale@verify.local seeded.
-- Run: bin/stack psql -f - < features/admin-ops-seed.sql (prints the chat session id).
\set ON_ERROR_STOP on
-- A fresh user has no project until the API's default_project() makes one on first use.
INSERT INTO projects (organization_id, user_id, name)
  SELECT default_organization(u.id), u.id, 'Il mio cliente' FROM "user" u
  WHERE u.email='ale@verify.local' AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.organization_id = default_organization(u.id));
WITH u AS (SELECT id FROM "user" WHERE email='ale@verify.local'),
s AS (INSERT INTO chat_sessions (organization_id, user_id, title, project_id)
      SELECT default_organization(u.id), u.id, 'Trova CEO di agenzie marketing a Milano', (SELECT id FROM projects WHERE organization_id=default_organization(u.id) ORDER BY created_at LIMIT 1) FROM u RETURNING id, user_id)
SELECT id AS sid FROM s \gset
INSERT INTO chat_messages (session_id, user_id, role, content, created_at) SELECT :'sid', user_id, 'user', 'Trova CEO e founder di agenzie di marketing a Milano, tra 10 e 50 dipendenti.', now() - interval '60 min' FROM chat_sessions WHERE id=:'sid';
INSERT INTO chat_messages (session_id, user_id, role, content, question, created_at) SELECT :'sid', user_id, 'assistant', '', '{"question":"Vuoi solo agenzie indipendenti o anche quelle di un gruppo?","options":[{"label":"Solo indipendenti","description":null},{"label":"Anche i gruppi","description":null}]}', now() - interval '59 min' FROM chat_sessions WHERE id=:'sid';
INSERT INTO chat_messages (session_id, user_id, role, content, created_at) SELECT :'sid', user_id, 'user', 'Solo indipendenti.', now() - interval '58 min' FROM chat_sessions WHERE id=:'sid';
INSERT INTO chat_messages (session_id, user_id, role, content, tool_calls, credits_used, created_at) SELECT :'sid', user_id, 'assistant', 'Ho trovato 31 aziende e 47 persone.', '[{"id":"c1","name":"search_companies","input":{"q":"agenzie marketing Milano"},"result":{"ok":true},"credits_used":12,"verdict":"success","vendors":["apollo"],"summary":{"count":31,"noun":"company"}},{"id":"c2","name":"find_people","input":{},"result":{"ok":true},"credits_used":100,"verdict":"success","summary":{"count":47,"noun":"person"}},{"id":"c3","name":"enrich_email","input":{},"result":{"error":"timeout"},"credits_used":0,"verdict":"failed","vendors":["apollo","hunter"]},{"id":"c4","name":"enrich_email","input":{},"result":{"ok":true},"credits_used":0,"verdict":"success","summary":{"count":0,"noun":"email","searched":3,"failed":3}},{"id":"c5","name":"enrich_email","input":{},"result":{"ok":true},"credits_used":0,"verdict":"success","summary":{"count":0,"noun":"email","searched":10,"failed":1}},{"id":"c6","name":"enrich_email","input":{},"result":{"ok":true},"credits_used":0,"verdict":"success","summary":{"count":0,"noun":"email","searched":3,"failed":0}}]', 112, now() - interval '50 min' FROM chat_sessions WHERE id=:'sid';
INSERT INTO jobs (organization_id, user_id, chat_session_id, status, phase, progress_done, progress_total, credits_reserved, credits_spent, started_at, first_row_at, finished_at, created_at, stages)
  SELECT organization_id, user_id, :'sid', 'succeeded', NULL, 47, 50, 312, 112, now()-interval '58 min', now()-interval '57 min 19 s', now()-interval '50 min', now()-interval '58 min',
  '[{"tool":"search_companies","label":"Cerca aziende","units":31,"done":31},{"tool":"find_people","label":"Trova persone","units":50,"done":47}]' FROM chat_sessions WHERE id=:'sid';
INSERT INTO jobs (organization_id, user_id, chat_session_id, status, phase, progress_done, progress_total, credits_reserved, credits_spent, started_at, first_row_at, finished_at, created_at, error, stages, result)
  SELECT organization_id, user_id, :'sid', 'failed', 'enrich_email', 64, 100, 560, 412, now()-interval '30 min', now()-interval '29 min', now()-interval '12 min', now()-interval '30 min', 'hunter: timeout dopo 3 tentativi (30s)',
  jsonb_build_array(
    jsonb_build_object('tool','find_people','label','Trova persone','units',100,'done',100,'started_at',now()-interval '30 min','finished_at',now()-interval '27 min 30 s'),
    jsonb_build_object('tool','enrich_email','label','Email verificate','units',100,'done',64,'error',jsonb_build_object('code','provider'),'started_at',now()-interval '27 min','finished_at',now()-interval '12 min 5 s')),
  '{"refunded":14,"undelivered":3}' FROM chat_sessions WHERE id=:'sid';
INSERT INTO lists (organization_id, user_id, chat_session_id, project_id, name, entity) SELECT organization_id, user_id, :'sid', project_id, 'Aziende', 'company' FROM chat_sessions WHERE id=:'sid';
INSERT INTO list_rows (list_id, cells, dedupe_key) SELECT l.id, '{}', 'c'||g FROM lists l, generate_series(1,31) g WHERE l.chat_session_id=:'sid';
INSERT INTO vendor_calls (user_id, vendor, operation, cost_usd, created_at) SELECT user_id, v, 'x', c, now()-interval '55 min' FROM chat_sessions, (VALUES ('apollo',1.2),('hunter',0.4)) x(v,c) WHERE id=:'sid';
INSERT INTO purchases (organization_id, user_id, stripe_session_id, pack_id, credits, amount_cents, currency) SELECT organization_id, user_id, 'cs_v1', 'topup_1000', 1000, 1600, 'eur' FROM chat_sessions WHERE id=:'sid';
INSERT INTO llm_usage (user_id, session_id, model, input_tokens, output_tokens, cost_usd) SELECT user_id, :'sid', 'deepseek-chat', 100000, 20000, 0 FROM chat_sessions WHERE id=:'sid';
INSERT INTO contact_lookups (user_id, provider, channel, found) SELECT user_id, p, ch, f FROM chat_sessions, (VALUES ('apollo','email',true),('apollo','email',true),('apollo','email',false),('hunter','email',true),('hunter','email',false),('aiark','phone',true),('aiark','phone',false),('aiark','phone',false)) x(p,ch,f) WHERE id=:'sid';
-- #352: a stuck chat (needs input, no rows) and an empty one (succeeded, no rows); ale's company and plan;
-- hunter failing today (it has a switch below), pdl failing with no switch.
INSERT INTO chat_sessions (organization_id, user_id, title, project_id, updated_at)
  SELECT organization_id, user_id, t, project_id, now() - o * interval '1 hour' FROM chat_sessions, (VALUES ('Fornitori di packaging in Veneto', 2), ('Startup fintech a Torino', 3)) x(t, o) WHERE id=:'sid';
INSERT INTO jobs (organization_id, user_id, chat_session_id, status, credits_spent, started_at, finished_at, created_at)
  SELECT s.organization_id, s.user_id, s.id, CASE WHEN s.title LIKE 'Fornitori%' THEN 'needs_input' ELSE 'succeeded' END, CASE WHEN s.title LIKE 'Fornitori%' THEN 0 ELSE 6 END, s.updated_at, s.updated_at + interval '40 s', s.updated_at
  FROM chat_sessions s, chat_sessions o WHERE o.id=:'sid' AND s.user_id = o.user_id AND s.id <> o.id AND s.title IN ('Fornitori di packaging in Veneto', 'Startup fintech a Torino');
INSERT INTO business_profiles (project_id, user_id, profile) SELECT project_id, user_id, '{"company_name":"Rossi Comunicazione"}' FROM chat_sessions WHERE id=:'sid'
  ON CONFLICT DO NOTHING;
INSERT INTO subscriptions (organization_id, user_id, stripe_subscription_id, plan_id, status, current_period_end, event_created)
  SELECT organization_id, user_id, 'sub_v1', 'pro', 'active', date_trunc('day', now()) + interval '20 days', 1 FROM chat_sessions WHERE id=:'sid';
INSERT INTO vendor_calls (user_id, vendor, operation, success, created_at) SELECT user_id, v, v||'.x', false, now() - interval '5 min' FROM chat_sessions, (VALUES ('hunter'),('hunter'),('hunter'),('pdl')) x(v) WHERE id=:'sid';
INSERT INTO settings (key, value, updated_by) SELECT 'vendor_disabled:aiark', 'true', id FROM "user" WHERE email='adm@verify.local';
INSERT INTO settings (key, value) VALUES ('vendor_disabled:apollo', 'false'), ('vendor_disabled:hunter', 'false'), ('signup_bonus_credits', '100');
SELECT :'sid';
