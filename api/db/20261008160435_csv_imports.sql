-- A CSV import runs in the worker (#344): this row is its state, so the dialog can close and come back.
-- `content` is the file until the run ends; `discarded` is the CSV of the rows not imported, with the reason;
-- `seen_at` is when the user saw how it ended. The janitor in csv_run.py fails a stuck run and clears `discarded` after 7 days.
CREATE TABLE csv_imports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    entity          TEXT NOT NULL CHECK (entity IN ('company', 'person')),
    name            TEXT NOT NULL,
    mapping         JSONB,
    content         BYTEA,
    status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    total           INT NOT NULL DEFAULT 0,
    processed       INT NOT NULL DEFAULT 0,
    rows_added      INT NOT NULL DEFAULT 0,
    duplicates      INT NOT NULL DEFAULT 0,
    in_lists        INT NOT NULL DEFAULT 0,
    excluded        INT NOT NULL DEFAULT 0,
    skipped         INT NOT NULL DEFAULT 0,
    list_id         UUID REFERENCES lists(id) ON DELETE SET NULL,
    chat_session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    discarded       TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    seen_at         TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_csv_imports_user ON csv_imports (organization_id, user_id, created_at DESC);
