-- Seeds one user row and one assistant row that ends on a question, in the chat titled :title.
-- bin/stack psql -v title='Trova CEO di agenzie marketing a Milano' -f - < features/chat-question-seed.sql
WITH s AS (SELECT id, user_id FROM chat_sessions WHERE title = :'title')
INSERT INTO chat_messages (session_id, user_id, role, content, credits_used, status, question, tool_calls, created_at)
SELECT id, user_id, 'user', 'agenzie di marketing a Milano', 0, NULL, NULL::jsonb, NULL::jsonb, now() - interval '2 min' FROM s
UNION ALL
SELECT id, user_id, 'assistant', 'Ho trovato 31 agenzie.', 2, 'success',
  $q${"question": "L'email è obbligatoria per ogni persona?",
      "options": [{"label": "Sì, solo persone con email verificata", "description": "Le righe senza email non vengono consegnate."},
                  "Includila se la trovo"]}$q$::jsonb,
  $t$[{"id": "t1", "name": "search_companies", "input": {"query": "agenzia marketing"}, "result": "{}", "credits_used": 2,
       "verdict": "success", "summary": {"count": 31, "noun": "company"}},
      {"id": "t3", "name": "set_brief", "input": {}, "result": "{}", "credits_used": 0, "verdict": "success",
       "brief": {"who": ["CEO"], "companies": ["Agenzia"], "where": ["Milano"], "signal": [], "email": null}},
      {"id": "t4", "name": "remember", "input": {}, "result": "{}", "credits_used": 0, "verdict": "success",
       "memory_saved": "Cerca CEO a Milano"}]$t$::jsonb,
  now() - interval '1 min' FROM s;
