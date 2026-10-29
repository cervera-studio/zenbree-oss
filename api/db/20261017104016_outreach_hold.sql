CREATE TABLE outreach_stops (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    email text NOT NULL CHECK (email = lower(btrim(email)) AND email <> ''),
    reason text NOT NULL CHECK (reason IN ('human_reply', 'opt_out', 'hard_bounce')),
    cause text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'failed', 'confirmed')),
    attempts int NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error_code text,
    confirmed_at timestamptz,
    confirmed_by text CHECK (confirmed_by IN ('provider', 'not_enrolled', 'user')),
    confirmed_user_id uuid REFERENCES "user"(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT outreach_stops_confirmed_shape
        CHECK ((status = 'confirmed') = (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL)),
    CONSTRAINT outreach_stops_failed_has_code CHECK (status <> 'failed' OR last_error_code IS NOT NULL),
    CONSTRAINT outreach_stops_one_per_lead UNIQUE (lead_id),
    CONSTRAINT outreach_stops_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT outreach_stops_connection_id_org_fkey FOREIGN KEY (connection_id, organization_id)
        REFERENCES instantly_connections (id, organization_id) ON DELETE RESTRICT,
    CONSTRAINT outreach_stops_lead_id_org_fkey FOREIGN KEY (lead_id, organization_id)
        REFERENCES instantly_leads (id, organization_id) ON DELETE RESTRICT
);
CREATE INDEX idx_outreach_stops_organization_id ON outreach_stops(organization_id);
CREATE INDEX idx_outreach_stops_connection_id ON outreach_stops(connection_id);
CREATE INDEX idx_outreach_stops_confirmed_user_id ON outreach_stops(confirmed_user_id);
CREATE INDEX idx_outreach_stops_person ON outreach_stops(project_id, email, status);

-- A set-returning SQL function, so the planner inlines it into the query that calls it.
CREATE FUNCTION outreach_person_emails(p_project uuid, p_email text, p_linkedin text) RETURNS TABLE (email text)
LANGUAGE sql STABLE AS $$
    SELECT lower(btrim(p_email)) WHERE btrim(coalesce(p_email, '')) <> ''
    UNION
    SELECT a.value FROM audience_aliases a
     WHERE a.project_id = p_project AND a.entity = 'person' AND a.kind = 'email' AND a.namespace = ''
       AND a.identity_id IN (
           SELECT h.identity_id FROM audience_aliases h
            WHERE h.project_id = p_project AND h.entity = 'person' AND h.namespace = ''
              AND ((h.kind = 'email' AND h.value = audience_alias_value('email', p_email))
                OR (h.kind = 'linkedin_url' AND h.value = audience_alias_value('linkedin_url', p_linkedin))))
$$;

-- Set-returning for the same reason as above: a scalar SQL function that reads a table is never inlined.
CREATE FUNCTION outreach_hold(p_project uuid, p_channel text, p_email text, p_linkedin text)
RETURNS TABLE (reason text) LANGUAGE sql STABLE AS $$
    SELECT h.reason FROM outreach_person_emails(p_project, p_email, p_linkedin) e
    CROSS JOIN LATERAL (
        SELECT CASE s.status WHEN 'failed' THEN 'stop_failed' WHEN 'pending' THEN 'stop_pending' ELSE 'stopped' END,
               CASE s.status WHEN 'failed' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END
          FROM outreach_stops s WHERE s.project_id = p_project AND s.email = e.email
        UNION ALL
        -- the outcomes of instantly_leads_held_idx, as literals, so the planner proves its predicate
        SELECT 'held_by_instantly', 4 FROM instantly_leads l
         WHERE p_channel = 'email' AND l.project_id = p_project AND l.email = e.email
           AND l.outcome IN ('publishing', 'accepted', 'uncertain')
    ) h(reason, rank)
    ORDER BY h.rank LIMIT 1
$$;
