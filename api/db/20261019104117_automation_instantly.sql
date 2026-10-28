-- The automation step that adds a run's people to an Instantly campaign (#1032).

-- The inline CHECK of 20261016184111_automations.sql carries Postgres' default name. Every existing row has one of the
-- five old kinds, which the wider list keeps.
ALTER TABLE automation_run_steps DROP CONSTRAINT automation_run_steps_kind_check,
    ADD CONSTRAINT automation_run_steps_kind_check CHECK (kind IN
        ('find_people', 'qualify', 'find_emails', 'add_to_campaign', 'add_to_instantly', 'write_hubspot'));

-- The people a succeeded run hands to Instantly, written in the transaction that marks them taken (`automation_rows`),
-- so a run that fails, is cancelled or dies holds nobody. Held until the batch is made: the transaction that creates
-- the `instantly_enrollments` row sets `enrollment_id` and clears `row_ids` (the batch's leads name the people then).
-- The workspace and campaign are the ones the step read; publish reads the campaign again.
CREATE TABLE automation_instantly (
    run_id          UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    project_id      UUID NOT NULL,  -- the run's: the beat's pickup reads the holds project by project
    workspace_id    TEXT NOT NULL CHECK (workspace_id <> ''),
    campaign_id     TEXT NOT NULL CHECK (campaign_id <> ''),
    campaign_name   TEXT NOT NULL DEFAULT '',
    campaign_status INTEGER NOT NULL,
    row_ids         UUID[] NOT NULL,
    enrollment_id   UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT automation_instantly_people CHECK (cardinality(row_ids) <= 1000),  -- instantly_enroll.MAX_ROWS
    CONSTRAINT automation_instantly_held_or_made CHECK ((enrollment_id IS NULL) = (cardinality(row_ids) > 0)),
    CONSTRAINT automation_instantly_enrollment_key UNIQUE (enrollment_id),
    CONSTRAINT automation_instantly_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_instantly_run_id_org_fkey FOREIGN KEY (run_id, organization_id)
        REFERENCES automation_runs (id, organization_id) ON DELETE CASCADE,
    -- CASCADE, never SET NULL: a deleted batch must not turn a run that made it back into one that holds people.
    CONSTRAINT automation_instantly_enrollment_id_org_fkey FOREIGN KEY (enrollment_id, organization_id)
        REFERENCES instantly_enrollments (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_automation_instantly_organization_id ON automation_instantly (organization_id);
CREATE INDEX idx_automation_instantly_project_id ON automation_instantly (project_id);
-- The beat's pickup reads the oldest held run of each project that can make a batch, for the workspace connected now,
-- never every held run.
CREATE INDEX automation_instantly_held ON automation_instantly (project_id, workspace_id, created_at)
    WHERE enrollment_id IS NULL;
