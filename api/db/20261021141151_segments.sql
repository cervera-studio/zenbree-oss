-- Segments (#938): a saved rule over the people or accounts of one project, and the identities that match it.
--
-- A member is an audience identity (#937), never a list row: a person is judged over all their visible rows in the
-- live lists of the project, so a title in one list and an email in another can make one match. Only
-- app.segments.evaluate.apply writes segment_members, under a per-project advisory lock.

CREATE TABLE segments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    -- who made it, for display: evaluation runs as the organization, so a segment outlives its creator
    created_by uuid REFERENCES "user"(id) ON DELETE SET NULL,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    entity text NOT NULL CHECK (entity IN ('person', 'company')),
    -- the root group of the rule, as app.segments.rules.parse returned it
    rule jsonb NOT NULL,
    -- +1 on each edit; a PATCH names the version it read, so two editors never overwrite each other
    version int NOT NULL DEFAULT 1,
    -- NULL while a draft: a draft has a preview and no members
    published_at timestamptz,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT segments_id_org_key UNIQUE (id, organization_id),
    CONSTRAINT segments_project_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_segments_project ON segments (project_id, created_at);
CREATE INDEX idx_segments_created_by ON segments (created_by);
CREATE INDEX idx_segments_organization_id ON segments (organization_id);
-- The published segments of a project: what re-evaluation reads first.
CREATE INDEX idx_segments_live ON segments (project_id) WHERE published_at IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE segment_members (
    segment_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    organization_id uuid NOT NULL,
    -- the row shown for the member: the best fit, then the oldest row. Display only.
    list_row_id uuid,
    -- 1 at the first join, +1 at each join after an exit: never 0, never reused
    episode int NOT NULL CHECK (episode >= 1),
    entered_at timestamptz NOT NULL DEFAULT now(),
    -- NULL while a member; an exit keeps the row, so a later join knows the last episode
    exited_at timestamptz,
    PRIMARY KEY (segment_id, identity_id),
    CONSTRAINT segment_members_segment_fkey FOREIGN KEY (segment_id, organization_id)
        REFERENCES segments (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT segment_members_identity_fkey FOREIGN KEY (identity_id, organization_id)
        REFERENCES audience_identities (id, organization_id) ON DELETE CASCADE,
    -- the column list keeps organization_id, which the other keys of this row need
    CONSTRAINT segment_members_row_fkey FOREIGN KEY (list_row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE SET NULL (list_row_id)
);
CREATE INDEX idx_segment_members_live ON segment_members (segment_id, identity_id) WHERE exited_at IS NULL;
CREATE INDEX idx_segment_members_identity ON segment_members (identity_id);
CREATE INDEX idx_segment_members_row ON segment_members (list_row_id) WHERE list_row_id IS NOT NULL;
CREATE INDEX idx_segment_members_organization_id ON segment_members (organization_id);
