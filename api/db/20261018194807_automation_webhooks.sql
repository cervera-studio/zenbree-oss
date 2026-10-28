-- Inbound webhook trigger of automations (#1031): one signed call names one person of the list and starts one run.

-- The inline CHECKs of 20261016184111_automations.sql carry Postgres' default names.
ALTER TABLE automations DROP CONSTRAINT automations_trigger_kind_check,
    ADD CONSTRAINT automations_trigger_kind_check CHECK (trigger_kind IN ('manual', 'schedule', 'webhook'));
-- A webhook run works on one person of the list. No FK: a deleted row must not fail its delete or turn the run into a
-- batch run (SET NULL would). The gate reads the row by id, so a deleted one leaves the run with nobody.
ALTER TABLE automation_runs DROP CONSTRAINT automation_runs_trigger_kind_check,
    ADD CONSTRAINT automation_runs_trigger_kind_check CHECK (trigger_kind IN ('manual', 'schedule', 'webhook')),
    ADD COLUMN target_row_id UUID,
    ADD CONSTRAINT automation_runs_webhook_has_target CHECK ((trigger_kind = 'webhook') = (target_row_id IS NOT NULL));

-- The hook of a webhook automation. Its own table, so `SELECT * FROM automations` never carries the ciphertext. The
-- row exists from the first secret until the trigger leaves `webhook`.
CREATE TABLE automation_webhooks (
    automation_id     UUID PRIMARY KEY,
    organization_id   UUID NOT NULL,
    hook_id           UUID NOT NULL DEFAULT gen_random_uuid(),  -- the URL's id: random, never the automation id
    secret_ciphertext TEXT NOT NULL,                            -- Fernet (token_crypto) of the whsec_ secret
    rotated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT automation_webhooks_hook_key UNIQUE (hook_id),
    CONSTRAINT automation_webhooks_automation_id_org_fkey FOREIGN KEY (automation_id, organization_id)
        REFERENCES automations (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_automation_webhooks_organization_id ON automation_webhooks (organization_id);

-- One signed call: the idempotency ledger, the queue, and the run page's "call that started this run".
CREATE TABLE automation_deliveries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    automation_id   UUID NOT NULL,
    webhook_id      TEXT NOT NULL CHECK (length(webhook_id) BETWEEN 1 AND 200),
    state           TEXT NOT NULL CHECK (state IN ('refused', 'pending', 'started', 'dropped')),
    code            TEXT,
    matched_by      TEXT CHECK (matched_by IN ('linkedin_url', 'email')),
    row_id          UUID,
    run_id          UUID,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,
    CONSTRAINT automation_deliveries_once UNIQUE (automation_id, webhook_id),
    CONSTRAINT automation_deliveries_run_key UNIQUE (run_id),  -- one run has at most one delivery
    CONSTRAINT automation_deliveries_shape CHECK (CASE state
        WHEN 'refused' THEN code IS NOT NULL AND run_id IS NULL AND decided_at IS NOT NULL
        WHEN 'pending' THEN code IS NULL AND run_id IS NULL AND decided_at IS NULL
        WHEN 'started' THEN code IS NULL AND decided_at IS NOT NULL
        WHEN 'dropped' THEN code IS NOT NULL AND run_id IS NULL AND decided_at IS NOT NULL END),
    CONSTRAINT automation_deliveries_automation_id_org_fkey FOREIGN KEY (automation_id, organization_id)
        REFERENCES automations (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_deliveries_row_id_org_fkey FOREIGN KEY (row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE SET NULL (row_id),
    CONSTRAINT automation_deliveries_run_id_org_fkey FOREIGN KEY (run_id, organization_id)
        REFERENCES automation_runs (id, organization_id) ON DELETE SET NULL (run_id)
);
-- automation_id (once) and run_id (run_key) lead their unique indexes; row_id and organization_id need their own.
CREATE INDEX idx_automation_deliveries_row_id ON automation_deliveries (row_id);
CREATE INDEX idx_automation_deliveries_organization_id ON automation_deliveries (organization_id);
CREATE UNIQUE INDEX automation_deliveries_one_pending ON automation_deliveries (automation_id, row_id)
    WHERE state = 'pending';
CREATE INDEX automation_deliveries_queue ON automation_deliveries (automation_id, received_at) WHERE state = 'pending';
-- The beat's tidy finds old calls across all automations, so these two lead with the time.
CREATE INDEX automation_deliveries_expiry ON automation_deliveries (received_at) WHERE state = 'pending';
CREATE INDEX automation_deliveries_prune ON automation_deliveries (decided_at) WHERE state IN ('refused', 'dropped');
