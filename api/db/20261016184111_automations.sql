-- Automations (#939): a bounded, ordered definition (find people, qualify, find emails, add to a campaign, write to
-- HubSpot) started by a manual run or a schedule, run by the durable run model of saved searches and watchlists.

CREATE TABLE automations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    project_id      UUID NOT NULL,
    -- Who made it: runs spend as them. NULL once they deleted their account: the automation stays, and stops.
    user_id         UUID REFERENCES "user"(id) ON DELETE SET NULL,
    name            TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    trigger_kind    TEXT NOT NULL CHECK (trigger_kind IN ('manual', 'schedule')),
    cadence         TEXT CHECK (cadence IN ('daily', 'weekly')),
    next_run_at     TIMESTAMPTZ,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    -- Bumped by every edit of the definition or the credit cap; a run is pinned to the version it was queued at.
    version         INTEGER NOT NULL DEFAULT 1,
    -- The version whose first run the user approved. A run of any other version puts its people in review.
    approved_version INTEGER,
    credit_cap      INTEGER NOT NULL CHECK (credit_cap BETWEEN 1 AND 100),
    definition      JSONB NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
    -- Where the find-people step stopped in the accounts list (list_rows.seq): the next run goes on from there.
    accounts_seq    BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT automations_id_org_key UNIQUE (id, organization_id),
    CONSTRAINT automations_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automations_schedule_has_cadence CHECK ((trigger_kind = 'schedule') = (cadence IS NOT NULL))
);
CREATE INDEX idx_automations_organization_id ON automations (organization_id);
CREATE INDEX idx_automations_project ON automations (project_id, created_at);
CREATE INDEX idx_automations_user_id ON automations (user_id);
CREATE INDEX automations_due ON automations (next_run_at) WHERE enabled AND trigger_kind = 'schedule';

CREATE TABLE automation_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    automation_id   UUID NOT NULL,
    version         INTEGER NOT NULL,
    -- The definition the run was queued with: an edit never changes a run that is queued or running.
    definition      JSONB NOT NULL,
    credit_cap      INTEGER NOT NULL CHECK (credit_cap BETWEEN 1 AND 100),
    trigger_kind    TEXT NOT NULL CHECK (trigger_kind IN ('manual', 'schedule')),
    scheduled_for   TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown')),
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    -- Set at the claim: the people of this run enter the campaign as draft (true) or with its normal status.
    review          BOOLEAN,
    approved_at     TIMESTAMPTZ,
    approved_by     UUID REFERENCES "user"(id) ON DELETE SET NULL,
    reservation_id  UUID,
    credits_spent   INTEGER NOT NULL DEFAULT 0 CHECK (credits_spent BETWEEN 0 AND 100),
    -- The people the run works on, taken once the find-people step has run.
    row_ids         UUID[] NOT NULL DEFAULT '{}',
    -- Who the HubSpot step held in a run under review: approving the run writes them, then this is emptied.
    hubspot_row_ids UUID[] NOT NULL DEFAULT '{}',
    -- Set while a task writes them, so two approvals never write one person at the same time.
    hubspot_claimed_at TIMESTAMPTZ,
    summary         JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(summary) = 'object'),
    error_code      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    CONSTRAINT automation_runs_id_org_key UNIQUE (id, organization_id),
    CONSTRAINT automation_runs_spent_within_cap CHECK (credits_spent <= credit_cap),
    CONSTRAINT automation_runs_scheduled_key UNIQUE (automation_id, scheduled_for),
    CONSTRAINT automation_runs_automation_id_org_fkey FOREIGN KEY (automation_id, organization_id)
        REFERENCES automations (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_runs_reservation_id_org_fkey FOREIGN KEY (reservation_id, organization_id)
        REFERENCES credit_transactions (id, organization_id)
);
CREATE UNIQUE INDEX automation_runs_one_live ON automation_runs (automation_id) WHERE status IN ('queued', 'running');
CREATE INDEX idx_automation_runs_organization_id ON automation_runs (organization_id);
CREATE INDEX idx_automation_runs_reservation_id ON automation_runs (reservation_id);
CREATE INDEX idx_automation_runs_approved_by ON automation_runs (approved_by);

-- One row per step of a run, written at the claim and updated as the step goes. ``input`` and ``output`` hold counts and
-- ids, never a person's name, email or phone.
CREATE TABLE automation_run_steps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    run_id          UUID NOT NULL,
    position        SMALLINT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('find_people', 'qualify', 'find_emails', 'add_to_campaign', 'write_hubspot')),
    -- uncertain: the worker died while the step ran, so what it did outside our database is not known.
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'succeeded', 'skipped', 'failed', 'uncertain')),
    decision        TEXT,
    input           JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(input) = 'object'),
    output          JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(output) = 'object'),
    credits         INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
    attempts        SMALLINT NOT NULL DEFAULT 0,
    error_code      TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    CONSTRAINT automation_run_steps_run_position_key UNIQUE (run_id, position),
    CONSTRAINT automation_run_steps_run_id_org_fkey FOREIGN KEY (run_id, organization_id)
        REFERENCES automation_runs (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_automation_run_steps_organization_id ON automation_run_steps (organization_id);

-- The people an automation has taken in: each is worked on once, so a later run never pays for them again.
CREATE TABLE automation_rows (
    automation_id   UUID NOT NULL,
    row_id          UUID NOT NULL,
    organization_id UUID NOT NULL,
    run_id          UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (automation_id, row_id),
    CONSTRAINT automation_rows_automation_id_org_fkey FOREIGN KEY (automation_id, organization_id)
        REFERENCES automations (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_rows_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_rows_run_id_org_fkey FOREIGN KEY (run_id, organization_id)
        REFERENCES automation_runs (id, organization_id) ON DELETE SET NULL (run_id)
);
CREATE INDEX idx_automation_rows_row_id ON automation_rows (row_id);
CREATE INDEX idx_automation_rows_run_id ON automation_rows (run_id);
CREATE INDEX idx_automation_rows_organization_id ON automation_rows (organization_id);

-- What the qualify step scored, against the brief of one version of one automation. The chat's own relevance_score
-- on the row is for the chat's brief and is never read here. A row exists once the person is scored and billed.
CREATE TABLE automation_scores (
    automation_id   UUID NOT NULL,
    version         INTEGER NOT NULL,
    row_id          UUID NOT NULL,
    organization_id UUID NOT NULL,
    score           SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    PRIMARY KEY (automation_id, version, row_id),
    CONSTRAINT automation_scores_automation_id_org_fkey FOREIGN KEY (automation_id, organization_id)
        REFERENCES automations (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_scores_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_automation_scores_row_id ON automation_scores (row_id);
CREATE INDEX idx_automation_scores_organization_id ON automation_scores (organization_id);

-- The campaign people a run added: approving the run finds them here, and they are never added twice.
CREATE TABLE automation_enrollments (
    run_id          UUID NOT NULL,
    target_id       UUID NOT NULL,
    organization_id UUID NOT NULL,
    PRIMARY KEY (run_id, target_id),
    CONSTRAINT automation_enrollments_run_id_org_fkey FOREIGN KEY (run_id, organization_id)
        REFERENCES automation_runs (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_enrollments_target_id_org_fkey FOREIGN KEY (target_id, organization_id)
        REFERENCES outreach_campaign_targets (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_automation_enrollments_target_id ON automation_enrollments (target_id);
CREATE INDEX idx_automation_enrollments_organization_id ON automation_enrollments (organization_id);
