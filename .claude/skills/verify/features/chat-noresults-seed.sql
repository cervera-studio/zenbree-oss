-- Seeds a user row and an assistant row that ends on the no-results funnel, in the chat titled :title.
-- bin/stack psql -v title='Trova CEO di agenzie marketing a Milano' -f - < features/chat-noresults-seed.sql
-- The question has the shape api/app/agent/funnel.py builds; the empty search carries no credits.
WITH s AS (SELECT id, user_id FROM chat_sessions WHERE title = :'title')
INSERT INTO chat_messages (session_id, user_id, role, content, credits_used, status, question, tool_calls, created_at)
SELECT id, user_id, 'user', 'SaaS in Piemonte con 20-100 dipendenti che assumono Account Executive', 0, NULL, NULL::jsonb, NULL::jsonb,
  now() - interval '2 min' FROM s
UNION ALL
SELECT id, user_id, 'assistant', '', 0, 'success',
  $q${"question": "No company matches every filter",
      "options": [
        {"label": "Without signal", "description": null, "drop": "signal", "estimate": {"count": 7, "noun": "company"},
         "filters": {"locations": ["Piemonte, Italy"], "employee_ranges": ["20,100"]}},
        {"label": "Without size", "description": null, "drop": "size", "estimate": {"count": 3, "noun": "company"},
         "filters": {"locations": ["Piemonte, Italy"], "job_titles": ["Account Executive"]}}],
      "funnel": {"steps": [{"group": "where", "values": ["Piemonte, Italy"], "count": 56557},
                           {"group": "size", "values": ["20–100"], "count": 23},
                           {"group": "signal", "values": ["Account Executive"], "count": 0}],
                 "excluded_index": 2}}$q$::jsonb,
  $t$[{"id": "t1", "name": "search_companies", "input": {"locations": ["Piemonte, Italy"], "employee_ranges": ["20,100"],
        "job_titles": ["Account Executive"]}, "result": "{}", "credits_used": 0, "verdict": "success",
       "summary": {"count": 0, "noun": "company"}},
      {"id": "t3", "name": "set_brief", "input": {}, "result": "{}", "credits_used": 0, "verdict": "success",
       "brief": {"who": ["Head of Sales"], "companies": ["SaaS", "20–100 dipendenti"], "where": ["Piemonte"],
                 "signal": ["Assume Account Executive"], "email": "required"}},
      {"id": "t4", "name": "remember", "input": {}, "result": "{}", "credits_used": 0, "verdict": "success",
       "memory_saved": "Cerca SaaS in Piemonte"}]$t$::jsonb,
  now() - interval '1 min' FROM s;
