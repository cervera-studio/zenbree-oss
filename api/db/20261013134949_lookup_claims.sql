-- A chat turn's open email or phone lookup: the rows an enrich call is searching, from before its first vendor call
-- until the transaction that stores its answers. app/lists/lookup_claims.py owns the table; a live job's claim reads
-- as "searching" (CHARGES_SQL). A dead job's claim stops counting at once, and the janitor deletes it later.
CREATE TABLE lookup_claims (
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    tool_call_id text NOT NULL,
    row_id uuid NOT NULL REFERENCES list_rows(id) ON DELETE CASCADE,
    key text NOT NULL CHECK (key IN ('email', 'phone')),
    PRIMARY KEY (row_id, key, tool_call_id)
);
-- Serves the job_id foreign key and release(): without it a release scans the whole table.
CREATE INDEX idx_lookup_claims_job ON lookup_claims(job_id, tool_call_id);
