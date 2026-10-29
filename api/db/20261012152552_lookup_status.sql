-- #659: the email_status and phone_status values stored before become the ones app/lists/lookups.py reads, and a
-- lookup that got no answer stores 'failed' instead of no status.

-- A JSON null status is no status (enrich_email writes phone_status null to reopen a phone search).
UPDATE list_rows SET cells = cells - 'email_status' WHERE jsonb_typeof(cells->'email_status') = 'null';
UPDATE list_rows SET cells = cells - 'phone_status' WHERE jsonb_typeof(cells->'phone_status') = 'null';

-- A known value in another case or with spaces, from a CSV import or a hand edit.
UPDATE list_rows
   SET cells = jsonb_set(cells, '{email_status}', to_jsonb(lower(btrim(cells->>'email_status'))))
 WHERE lower(btrim(cells->>'email_status')) IN ('verified', 'catch_all', 'mx_ok', 'unknown', 'invalid', 'not_found', 'failed')
   AND cells->>'email_status' <> lower(btrim(cells->>'email_status'));
UPDATE list_rows
   SET cells = jsonb_set(cells, '{phone_status}', to_jsonb(lower(btrim(cells->>'phone_status'))))
 WHERE lower(btrim(cells->>'phone_status')) IN ('revealed', 'not_found', 'failed')
   AND cells->>'phone_status' <> lower(btrim(cells->>'phone_status'));

-- Any other value (an Apollo CSV export's 'unavailable' or 'guessed'): no verdict on an address the row has, and
-- 'not_found' on a row without one, which is how the list and the batches read it until now.
UPDATE list_rows
   SET cells = CASE WHEN coalesce(cells->>'email', '') <> '' THEN cells - 'email_status'
                    ELSE cells || '{"email_status": "not_found"}' END
 WHERE cells->>'email_status' NOT IN ('verified', 'catch_all', 'mx_ok', 'unknown', 'invalid', 'not_found', 'failed');
UPDATE list_rows
   SET cells = CASE WHEN coalesce(cells->>'phone', '') <> '' THEN cells - 'phone_status'
                    ELSE cells || '{"phone_status": "not_found"}' END
 WHERE cells->>'phone_status' NOT IN ('revealed', 'not_found', 'failed');

-- An email lookup whose source failed (#506), or that no source could take (#520), wrote email null and no status.
-- A not_found written before #506 can be an outage too, but nothing tells it from a real miss: it stays final (#519).
-- A phone lookup that failed wrote phone null and no status, like a row with nothing to search by: those stay
-- unsearched, which offers the same search.
UPDATE list_rows
   SET cells = cells || '{"email_status": "failed"}'
 WHERE jsonb_typeof(cells->'email') = 'null' AND NOT cells ? 'email_status';
