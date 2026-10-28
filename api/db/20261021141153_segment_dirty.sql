-- Segments (#938) keep their members current as data changes. A write that can change who matches leaves a mark, and
-- the evaluate_segments beat (app.segments.drain) judges the marked people again.
--
-- The marks are separate facts: no unique key, so a writer only appends and never waits on another writer
-- (audience_link_queue documents the deadlock that cross-row locks caused). Each trigger is statement-level with a
-- transition table, so a bulk write costs one INSERT, and writes marks only for a project with a live published
-- segment, so a project without one pays a scan of the changed rows and nothing more. Postgres refuses a transition
-- table on a trigger with more than one event or an UPDATE OF list, so each trigger has one event and compares the
-- old and new rows itself.
--
-- No foreign keys: a key check on each of 100k marks would cost more than the marks. A mark outlives its row, and
-- the drain drops the marks whose row, identity or project is gone.

CREATE TABLE segment_dirty (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    -- a row write: the drain resolves the row to its identity when it judges it
    list_row_id uuid,
    -- a link change: the identity that gained or lost a row
    identity_id uuid,
    -- a person whose cell is under a held charge is judged again after this
    not_before timestamptz NOT NULL DEFAULT now(),
    -- a mark the drain writes again for a held person: it does not count toward FULL_EVAL_AT, or a long fill would
    -- force a full evaluation every minute
    retry boolean NOT NULL DEFAULT false,
    CONSTRAINT segment_dirty_one_target CHECK ((list_row_id IS NULL) <> (identity_id IS NULL))
);
CREATE INDEX idx_segment_dirty_due ON segment_dirty (organization_id, project_id, id);

-- full_due_at is the catch-up after a publish or a rule edit: a write whose statement ran before it committed can
-- commit after that evaluation read the rows, and it left no mark for a segment it did not see.
ALTER TABLE segments ADD COLUMN full_due_at timestamptz, ADD COLUMN full_now_at timestamptz;
CREATE INDEX idx_segments_full_owed ON segments ((least(full_due_at, full_now_at)))
    WHERE least(full_due_at, full_now_at) IS NOT NULL AND published_at IS NOT NULL AND deleted_at IS NULL;

CREATE FUNCTION segment_mark_rows() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO segment_dirty (organization_id, project_id, list_row_id)
    SELECT l.organization_id, l.project_id, n.id
    FROM newtab n JOIN oldtab o ON o.id = n.id JOIN lists l ON l.id = n.list_id
    -- cell_meta: a cell whose fill failed reads as missing
    WHERE (n.cells, n.cell_meta, n.relevance_score, n.excluded, n.list_id)
          IS DISTINCT FROM (o.cells, o.cell_meta, o.relevance_score, o.excluded, o.list_id)
      AND l.project_id IN (SELECT s.project_id FROM segments s WHERE s.published_at IS NOT NULL AND s.deleted_at IS NULL);
    RETURN NULL;
END $$;

-- The identity's own project: in a cascaded delete of rows, the row is gone before its link.
CREATE FUNCTION segment_mark_links() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        INSERT INTO segment_dirty (organization_id, project_id, identity_id)
        SELECT i.organization_id, i.project_id, i.id
        FROM (SELECT DISTINCT identity_id FROM newtab) n JOIN audience_identities i ON i.id = n.identity_id
        WHERE i.project_id IN (
            SELECT s.project_id FROM segments s WHERE s.published_at IS NOT NULL AND s.deleted_at IS NULL);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        INSERT INTO segment_dirty (organization_id, project_id, identity_id)
        SELECT i.organization_id, i.project_id, i.id
        FROM (SELECT DISTINCT identity_id FROM oldtab) o JOIN audience_identities i ON i.id = o.identity_id
        WHERE i.project_id IN (
            SELECT s.project_id FROM segments s WHERE s.published_at IS NOT NULL AND s.deleted_at IS NULL);
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION segment_mark_lists() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO segment_dirty (organization_id, project_id, identity_id)
    SELECT DISTINCT n.organization_id, n.project_id, a.identity_id
    FROM newtab n JOIN oldtab o ON o.id = n.id
    JOIN list_rows r ON r.list_id = n.id JOIN audience_row_links a ON a.list_row_id = r.id
    WHERE n.deleted_at IS DISTINCT FROM o.deleted_at
      AND n.project_id IN (SELECT s.project_id FROM segments s WHERE s.published_at IS NOT NULL AND s.deleted_at IS NULL);
    RETURN NULL;
END $$;

CREATE TRIGGER segment_mark_rows AFTER UPDATE ON list_rows
    REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION segment_mark_rows();
CREATE TRIGGER segment_mark_links_added AFTER INSERT ON audience_row_links
    REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION segment_mark_links();
CREATE TRIGGER segment_mark_links_moved AFTER UPDATE ON audience_row_links
    REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION segment_mark_links();
CREATE TRIGGER segment_mark_links_removed AFTER DELETE ON audience_row_links
    REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION segment_mark_links();
CREATE TRIGGER segment_mark_lists AFTER UPDATE ON lists
    REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION segment_mark_lists();

-- The admin switch that pauses member updates (app.segments.drain): listed in the back office, off.
INSERT INTO settings (key, value) VALUES ('segments_evaluation_paused', 'false') ON CONFLICT (key) DO NOTHING;
