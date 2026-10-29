-- Saved views (#827): a named filter of one list (its tab and its search text), kept by the client (project).
CREATE TABLE saved_views (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    list_id uuid NOT NULL,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    tab text NOT NULL DEFAULT 'all' CHECK (tab IN ('all', 'delivered', 'pending', 'no_contact', 'excluded')),
    query text NOT NULL DEFAULT '' CHECK (length(query) <= 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT saved_views_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT saved_views_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
        REFERENCES lists (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_saved_views_project ON saved_views(project_id, created_at);
CREATE INDEX idx_saved_views_list ON saved_views(list_id);
CREATE INDEX idx_saved_views_organization_id ON saved_views(organization_id);

-- A view is counted in the sidebar of its project: a new or deleted one wakes that project's Home streams.
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON saved_views
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
