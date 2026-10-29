-- Signal trigger of automations (#1030): an automation names a watchlist of its project and a minimum score. When the
-- live scores of an account's signals that it has not used yet reach the minimum, one run starts for that account and
-- uses those signals, once each (automation_signals). No existing row is a signal automation or a signal run, so
-- every new column is NULL on the existing rows and every new CHECK holds on them.

-- The inline CHECKs of 20261016184111_automations.sql carry Postgres' default names.
ALTER TABLE automations DROP CONSTRAINT automations_trigger_kind_check,
    ADD CONSTRAINT automations_trigger_kind_check CHECK (trigger_kind IN ('manual', 'schedule', 'webhook', 'signal')),
    -- NULL on a signal automation once its watchlist was deleted: a problem the automation shows, never a crash.
    ADD COLUMN watchlist_id UUID,
    ADD COLUMN signal_min_score REAL CHECK (signal_min_score > 0 AND signal_min_score <= 10000),
    -- Only signals first seen from here on start runs: set when the trigger becomes signal, when its watchlist changes
    -- and when the automation is resumed.
    ADD COLUMN signals_since TIMESTAMPTZ,
    -- When an evaluation last found no ready account, NULL when none did since signals_since or min_score changed:
    -- only a new signal can make an account ready after it, so the beat evaluates nothing until one arrives.
    ADD COLUMN signals_checked_at TIMESTAMPTZ,
    -- When the beat last tried to start a run. It tries those tried longest ago first, so automations that cannot
    -- start (a problem of the checks) never hold the whole batch.
    ADD COLUMN signals_tried_at TIMESTAMPTZ,
    ADD CONSTRAINT automations_watchlist_id_org_fkey FOREIGN KEY (watchlist_id, organization_id)
        REFERENCES watchlists (id, organization_id) ON DELETE SET NULL (watchlist_id),
    ADD CONSTRAINT automations_signal_trigger CHECK (
        (trigger_kind = 'signal') = (signal_min_score IS NOT NULL AND signals_since IS NOT NULL)
        AND (trigger_kind = 'signal'
            OR (watchlist_id IS NULL AND signals_checked_at IS NULL AND signals_tried_at IS NULL)));
CREATE INDEX idx_automations_watchlist_id ON automations (watchlist_id);

-- A signal run works on the account of its signals. No FK, as target_row_id: a deleted account row must not fail its
-- delete or turn the run into a run over the whole list.
ALTER TABLE automation_runs DROP CONSTRAINT automation_runs_trigger_kind_check,
    ADD CONSTRAINT automation_runs_trigger_kind_check
        CHECK (trigger_kind IN ('manual', 'schedule', 'webhook', 'signal')),
    ADD COLUMN target_account_id UUID,
    ADD CONSTRAINT automation_runs_signal_has_account
        CHECK ((trigger_kind = 'signal') = (target_account_id IS NOT NULL));

CREATE INDEX signals_watchlist_first_seen ON signals (watchlist_id, first_seen_at);

-- The signals each automation holds or used, written with the run they started (app.automations.signal_trigger, its
-- only writer). The key makes a signal start at most one run of an automation, whatever beat or retry comes. A run that
-- ends before it takes anyone deletes its rows (signal_trigger.settle), unless the user cancelled it or it was its
-- account's last failed run. Apart from signals.enrolled_at, which is the campaign link's (#648).
CREATE TABLE automation_signals (
    automation_id   UUID NOT NULL,
    signal_id       UUID NOT NULL,
    organization_id UUID NOT NULL,
    run_id          UUID,
    score           REAL NOT NULL,  -- the live score when the run started
    -- When the run took its people, succeeded, was cancelled by the user or was the last failed run of its account;
    -- NULL while the run holds the signal.
    used_at         TIMESTAMPTZ,
    PRIMARY KEY (automation_id, signal_id),
    CONSTRAINT automation_signals_automation_id_org_fkey FOREIGN KEY (automation_id, organization_id)
        REFERENCES automations (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_signals_signal_id_org_fkey FOREIGN KEY (signal_id, organization_id)
        REFERENCES signals (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT automation_signals_run_id_org_fkey FOREIGN KEY (run_id, organization_id)
        REFERENCES automation_runs (id, organization_id) ON DELETE SET NULL (run_id)
);
CREATE INDEX idx_automation_signals_signal_id ON automation_signals (signal_id);
CREATE INDEX idx_automation_signals_run_id ON automation_signals (run_id);
CREATE INDEX idx_automation_signals_organization_id ON automation_signals (organization_id);

-- The accounts of an automation whose last runs failed before they took anyone (signal_trigger.settle). The beat starts
-- no run for such an account before retry_at, so a lasting failure, such as a vendor that stays down, neither fails a
-- run each minute nor holds the automation's other accounts. A run that uses the account's signals deletes the row, and
-- so does a new count (signals_since). No FK on account_id, as automation_runs.target_account_id.
CREATE TABLE automation_signal_waits (
    automation_id   UUID NOT NULL,
    account_id      UUID NOT NULL,
    organization_id UUID NOT NULL,
    failed_runs     INT NOT NULL CHECK (failed_runs > 0),  -- in a row, since the account's signals were last used
    retry_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (automation_id, account_id),
    CONSTRAINT automation_signal_waits_automation_id_org_fkey FOREIGN KEY (automation_id, organization_id)
        REFERENCES automations (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_automation_signal_waits_retry_at ON automation_signal_waits (automation_id, retry_at);
CREATE INDEX idx_automation_signal_waits_organization_id ON automation_signal_waits (organization_id);
