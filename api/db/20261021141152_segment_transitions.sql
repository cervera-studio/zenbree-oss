CREATE TABLE segment_transitions (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id uuid NOT NULL,
    segment_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('enter', 'baseline', 'exit', 'merged')),
    episode int NOT NULL CHECK (episode >= 1),
    list_row_id uuid,
    segment_version int NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT segment_transitions_segment_fkey FOREIGN KEY (segment_id, organization_id)
        REFERENCES segments (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT segment_transitions_row_fkey FOREIGN KEY (list_row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE SET NULL (list_row_id)
);
-- A publish writes one record per member, so each key and index here costs a check or an insert per member: 100,000
-- members cost about 1 s for the identity key and the history, identity and organization indexes, and nothing reads
-- them. identity_id has no key: an identity is deleted only with its project, whose delete reaches these records
-- through the segment, or as a new identity that lost a race and so has no record.
--
-- The idempotency key. An exit or a merge has no join key (NULL), so it never conflicts. Not partial, so the delete
-- of a segment finds its records through it.
CREATE UNIQUE INDEX segment_transitions_one_join_per_episode
    ON segment_transitions (segment_id, identity_id, episode, (CASE WHEN kind IN ('enter', 'baseline') THEN true END));
-- entries_after
CREATE INDEX idx_segment_transitions_feed ON segment_transitions (segment_id, seq) WHERE kind = 'enter';
CREATE INDEX idx_segment_transitions_row ON segment_transitions (list_row_id) WHERE list_row_id IS NOT NULL;

-- History is never rewritten. A deleted row or segment still reaches it through the keys above, which run as the owner.
REVOKE UPDATE, DELETE ON segment_transitions FROM zenbree_app;

INSERT INTO segment_transitions
    (organization_id, segment_id, identity_id, kind, episode, list_row_id, segment_version, created_at)
SELECT m.organization_id, m.segment_id, m.identity_id, 'baseline', m.episode, m.list_row_id, s.version, m.entered_at
FROM segment_members m JOIN segments s ON s.id = m.segment_id
WHERE m.exited_at IS NULL
ORDER BY m.entered_at, m.segment_id, m.identity_id;
