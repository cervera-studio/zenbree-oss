-- Instantly (#929): a client's (project's) sequencer workspace, and the people Zenbree sent to a campaign there.

-- One workspace for each project. The API key is Fernet-encrypted (app.integrations.token_crypto) and never leaves
-- the API. Disconnect clears the key and keeps the row: the leads below stay on record, because Instantly still
-- holds them (#931 reads them to give a person's email to one system).
CREATE TABLE instantly_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    connected_by uuid REFERENCES "user"(id) ON DELETE SET NULL,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    api_key_encrypted text NOT NULL DEFAULT '',
    workspace_id text NOT NULL CHECK (workspace_id <> ''),
    workspace_name text,
    status text NOT NULL DEFAULT 'connected' CHECK (status IN ('connected', 'error', 'disconnected')),
    status_detail text,
    connected_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT instantly_connections_project_key UNIQUE (project_id),
    CONSTRAINT instantly_connections_id_org_key UNIQUE (id, organization_id),
    CONSTRAINT instantly_connections_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_instantly_connections_organization_id ON instantly_connections(organization_id);
CREATE INDEX idx_instantly_connections_connected_by ON instantly_connections(connected_by);

-- One confirmed batch: a list's selected people sent to one campaign. The workspace and campaign are copied, so the
-- record outlives a reconnection to another workspace.
CREATE TABLE instantly_enrollments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    list_id uuid,
    created_by uuid REFERENCES "user"(id) ON DELETE SET NULL,
    workspace_id text NOT NULL,
    campaign_id text NOT NULL CHECK (campaign_id <> ''),
    campaign_name text NOT NULL DEFAULT '',
    -- The campaign's Instantly status number when the user confirmed (1 = active: it may start sending).
    campaign_status integer,
    mapping jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'cancelled', 'failed')),
    error_code text,
    cancel_requested boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    -- The run's heartbeat: a live batch whose heartbeat is old died with its worker, and the user may resume it.
    touched_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT instantly_enrollments_id_org_key UNIQUE (id, organization_id),
    CONSTRAINT instantly_enrollments_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_enrollments_connection_id_org_fkey FOREIGN KEY (connection_id, organization_id)
        REFERENCES instantly_connections (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_enrollments_list_id_org_fkey FOREIGN KEY (list_id, organization_id)
        REFERENCES lists (id, organization_id) ON DELETE SET NULL (list_id)
);
CREATE INDEX idx_instantly_enrollments_project ON instantly_enrollments(project_id, created_at DESC);
CREATE INDEX idx_instantly_enrollments_connection ON instantly_enrollments(connection_id);
CREATE INDEX idx_instantly_enrollments_list ON instantly_enrollments(list_id);
CREATE INDEX idx_instantly_enrollments_organization_id ON instantly_enrollments(organization_id);
CREATE INDEX idx_instantly_enrollments_created_by ON instantly_enrollments(created_by);
-- One live batch for each project: two would race for the same campaign and the same people.
CREATE UNIQUE INDEX instantly_enrollments_one_live ON instantly_enrollments(project_id) WHERE status IN ('queued', 'running');

-- One person of a batch and what happened to them. `publishing` is a request in flight; a row left there by a crash
-- is read as uncertain. The two partial indexes below name the people Instantly may hold.
CREATE TABLE instantly_leads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The selection's order: chunks go, and the results show, in it.
    seq bigint GENERATED ALWAYS AS IDENTITY,
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    row_id uuid,
    workspace_id text NOT NULL,
    campaign_id text NOT NULL,
    email text CHECK (email = lower(btrim(email)) AND email <> ''),
    outcome text NOT NULL CHECK (outcome IN ('pending', 'publishing', 'accepted', 'skipped', 'failed', 'uncertain')),
    reason text,
    instantly_lead_id text,
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT instantly_leads_holds_an_email CHECK (outcome IN ('skipped', 'failed') OR email IS NOT NULL),
    CONSTRAINT instantly_leads_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_leads_enrollment_id_org_fkey FOREIGN KEY (enrollment_id, organization_id)
        REFERENCES instantly_enrollments (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_leads_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE SET NULL (row_id)
);
CREATE INDEX idx_instantly_leads_project ON instantly_leads(project_id);
CREATE INDEX idx_instantly_leads_enrollment ON instantly_leads(enrollment_id, seq);
CREATE INDEX idx_instantly_leads_row ON instantly_leads(row_id);
CREATE INDEX idx_instantly_leads_organization_id ON instantly_leads(organization_id);
-- A person is in a campaign once: a second enrollment (a concurrent one too) cannot take the same email there.
CREATE UNIQUE INDEX instantly_leads_one_per_campaign ON instantly_leads(workspace_id, campaign_id, email)
    WHERE outcome IN ('pending', 'publishing', 'accepted', 'uncertain');
-- What #931 reads: the people a project's Instantly holds, or may hold.
CREATE INDEX instantly_leads_held_idx ON instantly_leads(project_id, email)
    WHERE outcome IN ('publishing', 'accepted', 'uncertain');
