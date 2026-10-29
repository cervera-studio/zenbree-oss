-- The emails the hold keeps in a project, and why: the one statement of the rule. `channel` is NULL for a hold on
-- every channel. The outcomes are those of instantly_leads_held_idx, as literals, so the planner proves its predicate.
CREATE FUNCTION outreach_held_emails(p_project uuid)
RETURNS TABLE (email text, channel text, reason text, rank int) LANGUAGE sql STABLE AS $$
    SELECT s.email, NULL::text,
           CASE s.status WHEN 'failed' THEN 'stop_failed' WHEN 'pending' THEN 'stop_pending' ELSE 'stopped' END,
           CASE s.status WHEN 'failed' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END
      FROM outreach_stops s WHERE s.project_id = p_project
    UNION ALL
    SELECT l.email, 'email', 'held_by_instantly', 4 FROM instantly_leads l
     WHERE l.project_id = p_project AND l.outcome IN ('publishing', 'accepted', 'uncertain')
$$;

-- OFFSET 0 keeps the lookup a subquery of its own, so the email and the channel reach the index scan of each table.
CREATE OR REPLACE FUNCTION outreach_hold(p_project uuid, p_channel text, p_email text, p_linkedin text)
RETURNS TABLE (reason text) LANGUAGE sql STABLE AS $$
    SELECT h.reason FROM outreach_person_emails(p_project, p_email, p_linkedin) e
    CROSS JOIN LATERAL (
        SELECT h.reason, h.rank FROM outreach_held_emails(p_project) h
         WHERE h.email = e.email AND (h.channel IS NULL OR h.channel = p_channel) OFFSET 0
    ) h
    ORDER BY h.rank LIMIT 1
$$;

-- The same rule for a whole project, so a count reads the holds once and not once per message. It reads the aliases
-- the other way from outreach_person_emails: tests/test_approval_counts_sql.py checks that the two agree.
CREATE FUNCTION outreach_held_keys(p_project uuid)
RETURNS TABLE (kind text, value text, channel text) LANGUAGE sql STABLE AS $$
    SELECT 'email', h.email, h.channel FROM outreach_held_emails(p_project) h
    UNION ALL
    SELECT k.kind, k.value, h.channel FROM outreach_held_emails(p_project) h
      JOIN audience_aliases a ON a.project_id = p_project AND a.entity = 'person' AND a.namespace = ''
                             AND a.kind = 'email' AND a.value = h.email
      JOIN audience_aliases k ON k.identity_id = a.identity_id AND k.project_id = p_project AND k.entity = 'person'
                             AND k.namespace = '' AND k.kind IN ('email', 'linkedin_url')
$$;

-- Computing this at read time cost 37 ms for 20,000 waiting messages. A stored column is not recomputed when its
-- function changes: a new audience_alias_value must also UPDATE this column.
ALTER TABLE outreach_campaign_targets
    ADD COLUMN linkedin_alias text GENERATED ALWAYS AS (audience_alias_value('linkedin_url', linkedin_url)) STORED;

-- Only a lead that enters or leaves the hold changes a count: publishing -> accepted notifies nothing.
DROP TRIGGER home_touch_update ON instantly_leads;
CREATE TRIGGER home_touch_update AFTER UPDATE ON instantly_leads FOR EACH ROW
    WHEN ((OLD.project_id, OLD.email, OLD.outcome IN ('publishing', 'accepted', 'uncertain'))
          IS DISTINCT FROM (NEW.project_id, NEW.email, NEW.outcome IN ('publishing', 'accepted', 'uncertain')))
    EXECUTE FUNCTION home_touch('project');
