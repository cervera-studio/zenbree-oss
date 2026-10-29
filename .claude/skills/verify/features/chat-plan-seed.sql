\set ON_ERROR_STOP on
BEGIN;
UPDATE user_credits SET credits_remaining = :balance WHERE user_id = (SELECT id FROM "user" WHERE email = :'email');
CREATE TEMP TABLE ctx ON COMMIT DROP AS
SELECT u.id AS uid, (SELECT id FROM projects WHERE user_id = u.id ORDER BY created_at LIMIT 1) AS pid,
       gen_random_uuid() AS sid, gen_random_uuid() AS cid, gen_random_uuid() AS pl, gen_random_uuid() AS jid,
       (:'kind' IN ('plan', 'big')) AS is_plan
FROM "user" u WHERE u.email = :'email';
INSERT INTO chat_sessions (id, organization_id, user_id, project_id, title)
SELECT sid, default_organization(uid), uid, pid, CASE WHEN is_plan THEN 'Trova CEO di agenzie marketing a Milano' ELSE 'Titolari di studi dentistici a Torino' END FROM ctx;
INSERT INTO lists (id, organization_id, user_id, project_id, chat_session_id, name, entity)
SELECT cid, default_organization(uid), uid, pid, sid, 'Aziende', 'company' FROM ctx WHERE is_plan;
INSERT INTO list_rows (list_id, cells, dedupe_key)
SELECT cid, jsonb_build_object('name', 'Azienda ' || g), 'a' || g FROM ctx, generate_series(1, 31) g WHERE is_plan;
INSERT INTO lists (id, organization_id, user_id, project_id, chat_session_id, name, entity)
SELECT pl, default_organization(uid), uid, pid, sid, 'Persone', 'person' FROM ctx;
INSERT INTO list_plans (list_id, version, plan)
SELECT pl, 1, p FROM ctx, LATERAL (SELECT CASE WHEN :'kind' = 'legacy' THEN
$j${"mode":"build","entity":"person","target_rows":50,"credits_fixed":50,"credits_per_row":5,"estimated_credits":300,"large_run_credits":100,
"columns":[{"key":"email","label":"Email","required":true}],
"stages":[{"tool":"search_people","label":"Ricerca persone","units":50,"per_row":true}]}$j$::jsonb
WHEN :'kind' = 'small' THEN
$j${"mode":"build","entity":"person","target_rows":50,"credits_fixed":10,"credits_per_row":0.4,"credits_research_per_row":0,"estimated_credits":30,"large_run_credits":100,
"columns":[{"key":"email","label":"Email","required":true,"description":"Verificata"}],
"stages":[
{"tool":"search_companies","label":"Ricerca studi","units":10,"per_row":false,"unit_cost":1,"optional":false},
{"tool":"search_people","label":"Ricerca persone","units":50,"per_row":true,"unit_cost":0.4,"optional":false}]}$j$::jsonb
WHEN is_plan THEN
$j${"mode":"build","entity":"person","target_rows":50,"credits_fixed":50,"credits_per_row":5.0,"credits_research_per_row":2,"estimated_credits":300,"large_run_credits":100,
"columns":[
{"key":"full_name","label":"Nome","required":true,"description":"Nome e cognome"},
{"key":"title","label":"Ruolo","required":true,"description":"CEO, founder o resp. commerciale"},
{"key":"email","label":"Email","required":true,"description":"Verificata, non catch-all"},
{"key":"company_name","label":"Azienda","required":true,"description":"Ragione sociale"},
{"key":"website","label":"Sito","required":false,"description":"Dominio aziendale"},
{"key":"linkedin_url","label":"LinkedIn","required":false,"description":"Profilo della persona"},
{"key":"employees","label":"Dipendenti","required":false,"description":"Fascia aggiornata"},
{"key":"sdr_ad","label":"Annuncio SDR","required":false,"description":"Titolo, data e link"},
{"key":"ai_clienti","label":"Clienti principali","required":false,"research":"Quali settori serve?"},
{"key":"ai_target","label":"Perché è in target","required":false,"research":"Una riga, con la fonte"}],
"stages":[
{"tool":"search_companies","label":"Ricerca aziende","units":25,"per_row":false,"unit_cost":1,"optional":false},
{"tool":"search_companies","label":"Segnali di assunzione","units":25,"per_row":false,"unit_cost":1,"optional":false},
{"tool":"search_people","label":"Ricerca persone","units":50,"per_row":true,"unit_cost":1,"optional":false},
{"tool":"enrich_email","label":"Ricerca email","units":50,"per_row":true,"unit_cost":2,"optional":false}]}$j$::jsonb
ELSE
$j${"mode":"build","entity":"person","target_rows":80,"credits_fixed":50,"credits_per_row":12.0,"credits_research_per_row":0,"estimated_credits":1010,"large_run_credits":100,
"columns":[
{"key":"full_name","label":"Nome","required":true,"description":"Nome e cognome"},
{"key":"email","label":"Email","required":true,"description":"Verificata"},
{"key":"phone","label":"Telefono","required":false,"description":"Numero diretto"}],
"stages":[
{"tool":"search_companies","label":"Ricerca studi","units":50,"per_row":false,"unit_cost":1,"optional":false},
{"tool":"enrich_email","label":"Ricerca email","units":80,"per_row":true,"unit_cost":2,"optional":false},
{"tool":"enrich_phone","label":"Ricerca telefono","units":80,"per_row":true,"unit_cost":10,"optional":true}]}$j$::jsonb END AS p) q;
UPDATE list_plans SET plan = jsonb_set(jsonb_set(plan, '{target_rows}', '100'), '{stages}',
  (SELECT jsonb_agg(CASE WHEN (s->>'per_row')::boolean THEN jsonb_set(s, '{units}', '100') ELSE s END)
   FROM jsonb_array_elements(plan->'stages') s)) || '{"estimated_credits": 550}'
WHERE :'kind' = 'big' AND list_id = (SELECT pl FROM ctx);
INSERT INTO jobs (id, organization_id, user_id, chat_session_id, status) SELECT jid, default_organization(uid), uid, sid, 'needs_input' FROM ctx;
INSERT INTO chat_messages (session_id, user_id, role, content, job_id, created_at)
SELECT sid, uid, 'user', 'Cerco titolari, con email e telefono', jid, now() - interval '2 min' FROM ctx;
INSERT INTO chat_messages (session_id, user_id, role, content, status, question, job_id, created_at)
SELECT sid, uid, 'assistant', 'Ecco il piano. Parto dalle aziende che assumono, poi completo con le altre più in target.', 'success',
  jsonb_build_object('question', '', 'options', '[]'::jsonb, 'plan',
    (SELECT plan || jsonb_build_object('list_id', pl::text, 'version', 1, 'needs_approval', true) FROM list_plans WHERE list_id = pl)),
  jid, now() - interval '1 min' FROM ctx;
SELECT sid FROM ctx;
COMMIT;
