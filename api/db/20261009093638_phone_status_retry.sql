-- #504: the web no longer offers a paid phone search on a row whose phone_status is not_found. Before #510 the
-- lookup also wrote not_found when a vendor failed or was switched off, so an old not_found may be an outage: clear
-- it, and each such row can be searched once more.
UPDATE list_rows
   SET cells = cells - 'phone_status'
 WHERE cells->>'phone_status' = 'not_found'
   AND coalesce(cells->>'phone', '') = '';
