-- Seeds a user row and an assistant row that ends on a people no-results funnel (#260), in the chat titled :title.
-- bin/stack psql -v title='<chat title>' -f - < features/chat-noresults-people-seed.sql
-- The question has the shape api/app/agent/funnel.py builds for an empty Apollo people search.
WITH s AS (SELECT id, user_id FROM chat_sessions WHERE title = :'title')
INSERT INTO chat_messages (session_id, user_id, role, content, credits_used, status, question, tool_calls, created_at)
SELECT id, user_id, 'user', 'Head of sales a Torino o Milano, aziende da 20-100 dipendenti', 0, NULL, NULL::jsonb, NULL::jsonb,
  now() - interval '2 min' FROM s
UNION ALL
SELECT id, user_id, 'assistant', '', 0, 'success',
  $q${"question": "No person matches every filter",
      "options": [
        {"label": "Without who", "description": null, "drop": "who", "estimate": {"count": 412, "noun": "person"},
         "tool": "search_people",
         "filters": {"locations": ["Torino, Italy", "Milano, Italy"], "employee_ranges": ["20,100"]}}],
      "funnel": {"steps": [{"group": "where", "values": ["Torino, Italy", "Milano, Italy"], "count": 90210},
                           {"group": "size", "values": ["20–100"], "count": 412},
                           {"group": "who", "values": ["Head of Sales"], "count": 0}],
                 "excluded_index": 2, "entity": "person"}}$q$::jsonb,
  $t$[{"id": "t1", "name": "search_people", "input": {"locations": ["Torino, Italy", "Milano, Italy"],
        "employee_ranges": ["20,100"], "titles": ["Head of Sales"]}, "result": "{}", "credits_used": 0,
       "verdict": "success", "summary": {"count": 0, "noun": "person"}}]$t$::jsonb,
  now() - interval '1 min' FROM s;
