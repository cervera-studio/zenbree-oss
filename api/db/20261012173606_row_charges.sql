-- #660: a per-row charge is one record with one state, written only by app/billing/row_charges.py. Until now it
-- lived in list_rows.cell_meta: {pending_job: <job id>} on each cell a held charge bought, and {status: 'refunded'}
-- on each cell a turn took back.

-- job_id and tool_call_id are null on a record moved from a 'refunded' entry, which named neither. job_id is set
-- null, not cascaded, when a deleted user takes their jobs: the organization's rows stay (#323), and a lost
-- 'refunded' record would let an email batch buy that address again.
CREATE TABLE row_charges (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid REFERENCES jobs(id) ON DELETE SET NULL,
    tool_call_id text,
    row_id       uuid NOT NULL REFERENCES list_rows(id) ON DELETE CASCADE,
    keys         text[] NOT NULL,
    credits      int NOT NULL CHECK (credits >= 0),
    state        text NOT NULL CHECK (state IN ('held', 'charged', 'refunded')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    settled_at   timestamptz
);
-- The latest record of a row's key (the list rows, the email batch); a job's records (the end-of-turn settle, a
-- deleted job); the held records the janitor reads.
CREATE INDEX row_charges_row ON row_charges (row_id, created_at DESC);
CREATE INDEX row_charges_job ON row_charges (job_id);
CREATE INDEX row_charges_held ON row_charges (job_id) WHERE state = 'held';

-- One record per row and job of its pending_job marks: held while that job still runs (the rule the list used to
-- hide them), charged otherwise. The amount was never stored. A mark of a deleted job keeps no job.
INSERT INTO row_charges (job_id, row_id, keys, credits, state, settled_at)
SELECT j.id, r.id, array_agg(m.key ORDER BY m.key), 0,
       CASE WHEN l.live THEN 'held' ELSE 'charged' END, CASE WHEN l.live THEN NULL ELSE now() END
  FROM list_rows r
 CROSS JOIN jsonb_each(r.cell_meta) m
  LEFT JOIN jobs j ON j.id::text = m.value->>'pending_job'
 CROSS JOIN LATERAL (SELECT coalesce(j.status = 'running' AND j.updated_at > now() - interval '20 minutes', false)
                            AS live) l
 WHERE jsonb_typeof(m.value) = 'object' AND m.value ? 'pending_job'
 GROUP BY r.id, j.id, m.value->>'pending_job', l.live;

-- One refunded record per row of its 'refunded' entries.
INSERT INTO row_charges (row_id, keys, credits, state, settled_at)
SELECT r.id, array_agg(m.key ORDER BY m.key), 0, 'refunded', now()
  FROM list_rows r
 CROSS JOIN jsonb_each(r.cell_meta) m
 WHERE m.value->>'status' = 'refunded'
 GROUP BY r.id;

-- A 'refunded' entry goes whole: the web rejects an entry without a status. A pending one keeps its status.
UPDATE list_rows r
   SET cell_meta = (
       SELECT coalesce(jsonb_object_agg(m.key, CASE WHEN jsonb_typeof(m.value) = 'object' THEN m.value - 'pending_job'
                                                    ELSE m.value END), '{}')
         FROM jsonb_each(r.cell_meta) m
        WHERE m.value->>'status' IS DISTINCT FROM 'refunded')
 WHERE EXISTS (SELECT 1 FROM jsonb_each(r.cell_meta) m
                WHERE (jsonb_typeof(m.value) = 'object' AND m.value ? 'pending_job')
                   OR m.value->>'status' = 'refunded');
