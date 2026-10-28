-- #519: a not_found that a lookup wrote before #506 (email null) may be an outage, so the batch searches it again
-- after every row never tried. A not_found from a CSV has no email key and stays final.
UPDATE list_rows
   SET cells = cells || '{"email_status": "failed"}'
 WHERE cells->>'email_status' = 'not_found'
   AND jsonb_typeof(cells->'email') = 'null';
