-- E13.3 (#647): watchlists and signals. A watchlist watches the accounts of a company list for a kind of change
-- (first: new job postings in chosen functions) on a cadence, and records each change once as a signal.

CREATE TABLE watchlists (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Who made it: runs spend as them. NULL once they deleted their account: the watchlist stays, and stops.
    user_id     UUID REFERENCES "user"(id) ON DELETE SET NULL,
    list_id     UUID NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    name        TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    job_titles  TEXT[] NOT NULL CHECK (cardinality(job_titles) BETWEEN 1 AND 20),
    cadence     TEXT NOT NULL CHECK (cadence IN ('daily', 'weekly')),
    next_run_at TIMESTAMPTZ NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX watchlists_due ON watchlists (next_run_at) WHERE enabled;
CREATE INDEX watchlists_list ON watchlists (list_id);

CREATE TABLE watchlist_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id     UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    scheduled_for    TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown')),
    reservation_id   UUID REFERENCES credit_transactions(id),
    credits_spent    INTEGER NOT NULL DEFAULT 0 CHECK (credits_spent BETWEEN 0 AND 100),
    accounts_checked INTEGER NOT NULL DEFAULT 0,
    accounts_skipped INTEGER NOT NULL DEFAULT 0,
    -- Accounts this run did not reach (the credit cap, the time, a vendor failure): first in the next one.
    accounts_left    INTEGER NOT NULL DEFAULT 0,
    signals_new      INTEGER NOT NULL DEFAULT 0,
    error_code       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    UNIQUE (watchlist_id, scheduled_for)
);
CREATE UNIQUE INDEX watchlist_runs_one_live ON watchlist_runs (watchlist_id) WHERE status IN ('queued', 'running');

-- Where each account is in the rotation, and what a run learned about it.
CREATE TABLE watchlist_accounts (
    watchlist_id UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    row_id       UUID NOT NULL REFERENCES list_rows(id) ON DELETE CASCADE,
    checked_at   TIMESTAMPTZ NOT NULL,
    vendor_id    TEXT,  -- the account at the vendor, resolved once from its domain
    skip_reason  TEXT,  -- why the last run could not check it; NULL when it could
    PRIMARY KEY (watchlist_id, row_id)
);

CREATE TABLE signals (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id  UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    row_id        UUID NOT NULL REFERENCES list_rows(id) ON DELETE CASCADE,
    run_id        UUID REFERENCES watchlist_runs(id) ON DELETE SET NULL,
    type          TEXT NOT NULL CHECK (type IN ('job_posting')),
    -- What makes two observations the same change: a posting's link, or its title and place.
    key           TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 1000),
    data          JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(data) = 'object'),
    score         REAL NOT NULL CHECK (score > 0),  -- at occurred_at; it halves every 30 days (signal_score)
    occurred_at   TIMESTAMPTZ NOT NULL,             -- when it happened, as far as the source says
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (watchlist_id, row_id, type, key)
);
CREATE INDEX signals_watchlist ON signals (watchlist_id, occurred_at DESC);

-- Decision on #122: a signal's score halves every 30 days.
CREATE FUNCTION signal_score(p_score REAL, p_occurred_at TIMESTAMPTZ, p_now TIMESTAMPTZ DEFAULT now())
RETURNS NUMERIC LANGUAGE sql IMMUTABLE AS $$
    SELECT round((p_score * power(0.5, greatest(extract(epoch FROM p_now - p_occurred_at), 0) / 86400.0 / 30))::numeric, 1);
$$;
