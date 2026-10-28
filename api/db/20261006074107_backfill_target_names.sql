-- #235 backfill: until then enrollment copied only first_name/last_name from the list row, so
-- people of a list that stores only full_name were enrolled with empty names and every step
-- using {{firstName}} was refused. Fill the empty parts from the full_name of the same person
-- in the campaign's list (matched by email, else by LinkedIn URL), split the way
-- app.scheduling.runner.target_name does: leading titles dropped, first word = first name.
-- It only touches a target whose first_name is empty and never overwrites a stored value.
UPDATE outreach_campaign_targets t
SET first_name = left(split_part(n.cleaned, ' ', 1), 200),
    last_name = CASE WHEN btrim(t.last_name) <> '' THEN t.last_name
                     ELSE left(btrim(substr(n.cleaned, length(split_part(n.cleaned, ' ', 1)) + 1)), 200) END
FROM (
    SELECT e.id, name.cleaned
    FROM outreach_campaign_targets e
    JOIN outreach_campaigns c ON c.id = e.campaign_id AND c.owner_id = e.owner_id
    CROSS JOIN LATERAL (
        SELECT btrim(regexp_replace(
                   btrim(r.cells->>'full_name'),
                   '^(?:(?:(?:dott|prof)(?:\.?ssa)?|dr|mr|mrs|ms)\.?\s+|(?:ing|avv|arch|geom|rag|sig)\.(?:ra|na)?\.?\s+)+',
                   '', 'i')) AS cleaned
        FROM list_rows r
        WHERE r.list_id = c.list_id
          AND btrim(coalesce(r.cells->>'full_name', '')) <> ''
          AND ((coalesce(btrim(e.email), '') <> '' AND lower(btrim(r.cells->>'email')) = lower(btrim(e.email)))
            OR (btrim(e.linkedin_url) <> '' AND btrim(r.cells->>'linkedin_url') = btrim(e.linkedin_url)))
        ORDER BY r.seq
        LIMIT 1
    ) name
    WHERE btrim(e.first_name) = ''
) n
WHERE t.id = n.id AND n.cleaned <> '';
