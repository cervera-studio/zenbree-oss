-- Audience identity (#937): one reference for a person or an account across lists and connected systems.
--
-- A list row deduplicates inside its list only. This gives the same person in two lists, a HubSpot contact and an
-- external sequencer lead one identity, so an opt-out or a duplicate check follows every exact alias we know.
--
-- Visibility: an identity belongs to one project (a client) and is never read or matched across projects, so the
-- fields of another project stay private. A same-name match is never an alias: only an exact email, LinkedIn URL,
-- company domain or provider id is. Two identities that one row ties together are never merged by code: the pair
-- becomes an open conflict that a person resolves (merge or keep apart).
--
-- Provider ids: kind 'crm_object' (namespace 'hubspot:<portal>:contact' or ':company') and kind 'external_lead'
-- (namespace '<provider>:<workspace>', value = the lead id) link through audience_attach_alias, or through
-- audience_attach_new when no list row holds the person. #923 and #929 fill them; no schema change is needed.

CREATE TABLE audience_identities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    entity text NOT NULL CHECK (entity IN ('person', 'company')),
    -- set when a resolved conflict folded this identity into another: its aliases and rows moved, it stays as a
    -- tombstone so a conflict that names it keeps its record
    merged_into uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audience_identities_id_org_key UNIQUE (id, organization_id),
    CONSTRAINT audience_identities_project_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT audience_identities_merged_fkey FOREIGN KEY (merged_into, organization_id)
        REFERENCES audience_identities (id, organization_id)
);
CREATE INDEX idx_audience_identities_project ON audience_identities(project_id);
CREATE INDEX idx_audience_identities_merged ON audience_identities(merged_into) WHERE merged_into IS NOT NULL;
CREATE INDEX idx_audience_identities_organization_id ON audience_identities(organization_id);

-- Every value we have seen for an identity. Append-only: a changed cell adds an alias, and the source and time of an
-- alias are never rewritten, so a provider refresh cannot replace a trusted value. A value belongs to one identity
-- of its entity in a project: a company page and a person can share one LinkedIn URL, each in its own identity.
CREATE TABLE audience_aliases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    entity text NOT NULL CHECK (entity IN ('person', 'company')),
    kind text NOT NULL CHECK (kind IN ('email', 'linkedin_url', 'domain', 'crm_object', 'external_lead')),
    namespace text NOT NULL DEFAULT '',
    value text NOT NULL CHECK (value <> ''),
    source text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audience_aliases_value_key UNIQUE (project_id, entity, kind, namespace, value),
    CONSTRAINT audience_aliases_identity_fkey FOREIGN KEY (identity_id, organization_id)
        REFERENCES audience_identities (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT audience_aliases_project_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_audience_aliases_identity ON audience_aliases(identity_id);
CREATE INDEX idx_audience_aliases_organization_id ON audience_aliases(organization_id);

-- The identity of a list row. It goes with the row; the identity and its aliases stay, because history and opt-outs
-- hang on them.
CREATE TABLE audience_row_links (
    list_row_id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audience_row_links_row_fkey FOREIGN KEY (list_row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT audience_row_links_identity_fkey FOREIGN KEY (identity_id, organization_id)
        REFERENCES audience_identities (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_audience_row_links_identity ON audience_row_links(identity_id);
CREATE INDEX idx_audience_row_links_organization_id ON audience_row_links(organization_id);

-- Rows whose identity is not yet resolved or whose alias cells changed. The writer of the cells marks a row in its own
-- transaction (audience_mark_rows: one insert, no lock beyond the row it already holds) and never links there: a link
-- takes alias locks, and a delivery transaction that waited on them while holding row and credit locks deadlocked.
-- A task and the per-organization beat link the marked rows in short transactions and delete the mark.
CREATE TABLE audience_link_queue (
    list_row_id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    -- who wrote the values, when the row's own source cell does not say (a hand edit: 'manual')
    source text,
    -- failed links since the last write of the row: the link gives up after a few and the row stays unlinked
    attempts int NOT NULL DEFAULT 0,
    queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT audience_link_queue_row_fkey FOREIGN KEY (list_row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_audience_link_queue_organization_id ON audience_link_queue(organization_id, queued_at);
-- The beat's pick: per organization, the oldest mark that is not given up (attempts reaches the cap in app.audience.link.MAX_ATTEMPTS).
CREATE INDEX idx_audience_link_queue_due ON audience_link_queue(organization_id, queued_at) WHERE attempts < 5;

-- An ambiguous mapping: a row or provider id ties two identities that may be one person. identity_a < identity_b.
CREATE TABLE audience_conflicts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    identity_a uuid NOT NULL,
    identity_b uuid NOT NULL,
    list_row_id uuid,
    -- the values that tied them, each with the source and time of the alias it clashed with
    evidence jsonb NOT NULL DEFAULT '[]',
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'merged', 'kept_apart')),
    resolved_by uuid,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    seq bigint GENERATED ALWAYS AS IDENTITY,
    CONSTRAINT audience_conflicts_pair_key UNIQUE (identity_a, identity_b),
    CONSTRAINT audience_conflicts_order CHECK (identity_a < identity_b),
    CONSTRAINT audience_conflicts_a_fkey FOREIGN KEY (identity_a, organization_id)
        REFERENCES audience_identities (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT audience_conflicts_b_fkey FOREIGN KEY (identity_b, organization_id)
        REFERENCES audience_identities (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT audience_conflicts_project_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT audience_conflicts_row_fkey FOREIGN KEY (list_row_id, organization_id)
        REFERENCES list_rows (id, organization_id) ON DELETE SET NULL (list_row_id)
);
CREATE INDEX idx_audience_conflicts_b ON audience_conflicts(identity_b);
CREATE INDEX idx_audience_conflicts_seq ON audience_conflicts(project_id, status, seq);
CREATE INDEX idx_audience_conflicts_project ON audience_conflicts(project_id, status);
CREATE INDEX idx_audience_conflicts_row ON audience_conflicts(list_row_id) WHERE list_row_id IS NOT NULL;
CREATE INDEX idx_audience_conflicts_organization_id ON audience_conflicts(organization_id);

-- The stored form of one alias value, or NULL when the value cannot be one. It mirrors app.exclusions.rules.normalise
-- (norm_host, norm_url), so a followed alias matches the cell it came from exactly as an exclusion rule does;
-- tests/test_audience_sql.py compares them. A role address never names a person: it is shared by everyone behind it.
CREATE FUNCTION audience_alias_value(p_kind text, p_raw text) RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v text := lower(btrim(coalesce(p_raw, '')));
    shared text[] := ARRAY['facebook.com', 'fb.com', 'instagram.com', 'linkedin.com', 'twitter.com', 'x.com',
        'youtube.com', 'youtu.be', 'tiktok.com', 'pinterest.com', 'github.com', 'medium.com', 'google.com',
        'linktr.ee'];
    roles text[] := ARRAY['info', 'sales', 'contact', 'contacts', 'hello', 'hi', 'support', 'help', 'admin',
        'office', 'team', 'mail', 'enquiries', 'inquiries', 'marketing', 'billing', 'accounts', 'service',
        'customerservice', 'jobs', 'careers', 'hr', 'press', 'media', 'webmaster', 'postmaster', 'noreply',
        'no-reply', 'donotreply', 'do-not-reply', 'reception', 'general', 'booking', 'bookings', 'orders'];
    h text;
BEGIN
    IF p_kind = 'email' THEN
        IF v ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' AND length(v) <= 320 AND NOT split_part(v, '@', 1) = ANY(roles) THEN
            RETURN v;
        END IF;
        RETURN NULL;
    ELSIF p_kind = 'linkedin_url' THEN
        v := regexp_replace(regexp_replace(v, '^[a-z]+://(www\.)?', ''), '\?.*$', '');
        v := regexp_replace(v, '/+$', '');
        RETURN CASE WHEN position('linkedin.com/' IN v) > 0 AND length(v) <= 500 THEN v END;
    ELSIF p_kind = 'domain' THEN
        v := regexp_replace(ltrim(v, '@'), '^[a-z]+://', '');
        v := regexp_replace(split_part(split_part(v, '/', 1), '?', 1), '^www\.', '');
        IF v !~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$' OR length(v) > 253 THEN
            RETURN NULL;
        END IF;
        FOREACH h IN ARRAY shared LOOP
            IF v = h OR v LIKE '%.' || h THEN
                RETURN NULL;
            END IF;
        END LOOP;
        RETURN v;
    END IF;
    RETURN NULL;
END $$;

-- The kinds an identity of each entity can hold. Provider ids fit both.
CREATE FUNCTION audience_kind_fits(p_entity text, p_kind text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT p_kind IN ('linkedin_url', 'crm_object', 'external_lead')
        OR (p_entity = 'person' AND p_kind = 'email') OR (p_entity = 'company' AND p_kind = 'domain')
$$;

-- Whose page a stored LinkedIn URL is. A person's alias is a profile (/in/<slug>) and an account's a company, school
-- or showcase page with a slug: a company page on a person row, or a URL with no slug, names nobody, and joining on it
-- would put every employee of a company in one identity. app.audience.aliases.linkedin_fits is the same rule;
-- tests/test_audience_sql.py compares them.
CREATE FUNCTION audience_linkedin_fits(p_entity text, p_value text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_entity
        WHEN 'person' THEN p_value ~ '^([a-z0-9-]+\.)*linkedin\.com/in/[^/]+'
        ELSE p_value ~ '^([a-z0-9-]+\.)*linkedin\.com/(company|school|showcase)/[^/]+' END
$$;

-- The alias a cell gives an entity: the stored value, and for a LinkedIn URL only when it is that entity's page.
CREATE FUNCTION audience_entity_alias(p_entity text, p_kind text, p_raw text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN p_kind = 'linkedin_url' AND NOT coalesce(audience_linkedin_fits(p_entity, v), false) THEN NULL
                ELSE v END
      FROM (SELECT audience_alias_value(p_kind, p_raw) AS v) x
$$;

-- The stored value of an alias for an entity, or an error: the kind must fit the entity, a provider id needs its
-- namespace, and the other kinds must normalize.
CREATE FUNCTION audience_alias_target(p_entity text, p_kind text, p_namespace text, p_value text)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v text;
BEGIN
    IF NOT audience_kind_fits(p_entity, p_kind) THEN
        RAISE EXCEPTION 'a % identity cannot hold a % alias', p_entity, p_kind USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v := CASE WHEN p_kind IN ('crm_object', 'external_lead') THEN nullif(btrim(p_value), '')
              ELSE audience_entity_alias(p_entity, p_kind, p_value) END;
    IF v IS NULL OR (p_kind IN ('crm_object', 'external_lead') AND p_namespace = '') THEN
        RAISE EXCEPTION 'invalid alias' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v;
END $$;

-- The identity that a merge left standing: follow merged_into. The key-share lock pins each identity, so a merge
-- (which locks both with FOR UPDATE) and an alias insert cannot cross.
CREATE FUNCTION audience_live(p_identity uuid) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    cur uuid := p_identity;
    nxt uuid;
BEGIN
    LOOP
        SELECT merged_into INTO nxt FROM audience_identities WHERE id = cur FOR KEY SHARE;
        EXIT WHEN nxt IS NULL;
        cur := nxt;
    END LOOP;
    RETURN cur;
END $$;

-- Record a conflict between two identities (never a merge). A pair already recorded, open or resolved, stays.
CREATE FUNCTION audience_conflict(p_org uuid, p_project uuid, p_a uuid, p_b uuid, p_row uuid, p_evidence jsonb)
RETURNS void LANGUAGE sql AS $$
    INSERT INTO audience_conflicts (organization_id, project_id, identity_a, identity_b, list_row_id, evidence)
    VALUES (p_org, p_project, least(p_a, p_b), greatest(p_a, p_b), p_row, p_evidence)
    ON CONFLICT (identity_a, identity_b) DO NOTHING
$$;

-- Link one list row to its identity from the exact aliases in its cells (person: email, LinkedIn URL; company:
-- domain or website, LinkedIn URL). 0 identities hold them: a new one. 1: that one. 2 or more, or one that differs
-- from the row's own: conflicts, nothing attached, no merge. A row with no alias and no identity gets none: a name
-- is never an alias. Returns the row's identity, or NULL.
--
-- Called after the writer's commit, one row to a transaction (app.audience.link): never in a transaction that holds
-- row, project or credit locks. One pass, no retry inside: a turn that finds the row's identities changed under it
-- (an identity folded by a merge, or a value another turn took first) raises serialization_failure, which ends the
-- transaction and releases every lock, and app.audience.link runs the row again in a new one. So a link never waits for
-- a lock while it holds one it took in an earlier pass. Inside a pass it takes the identities it reads in id order (a
-- merge takes them in id order too) and then the aliases in (kind, value) order (two links take them in one order), so
-- no two transactions of this feature can wait on each other in a cycle.
CREATE FUNCTION audience_link_row(p_row uuid, p_source text DEFAULT NULL) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    r record;
    kinds text[];
    vals text[];
    src text;
    cur uuid;
    hits uuid[];
    cands uuid[];
    ident uuid;
    evidence jsonb;
    i int;
    j int;
BEGIN
    SELECT w.organization_id AS org, l.project_id AS project, l.entity, w.cells INTO r
      FROM list_rows w JOIN lists l ON l.id = w.list_id AND l.organization_id = w.organization_id WHERE w.id = p_row;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT array_agg(kind ORDER BY kind, value), array_agg(value ORDER BY kind, value) INTO kinds, vals FROM (
        SELECT DISTINCT kind, value FROM (
            SELECT 'email' AS kind, audience_alias_value('email', r.cells->>'email') AS value WHERE r.entity = 'person'
            UNION ALL SELECT 'linkedin_url', audience_entity_alias(r.entity, 'linkedin_url', r.cells->>'linkedin_url')
            UNION ALL SELECT 'domain', audience_alias_value('domain',
                coalesce(nullif(btrim(r.cells->>'domain'), ''), r.cells->>'website')) WHERE r.entity = 'company'
        ) x WHERE value IS NOT NULL) y;
    src := coalesce(p_source, nullif(btrim(r.cells->>'source'), ''), 'list');
    SELECT identity_id INTO cur FROM audience_row_links WHERE list_row_id = p_row;
    IF kinds IS NULL AND cur IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT array_agg(DISTINCT a.identity_id), coalesce(jsonb_agg(jsonb_build_object('kind', a.kind,
               'value', a.value, 'source', a.source, 'observed_at', a.observed_at,
               'identity_id', a.identity_id)), '[]')
      INTO hits, evidence
      FROM audience_aliases a JOIN unnest(coalesce(kinds, '{}'), coalesce(vals, '{}')) AS u(kind, value)
        ON a.kind = u.kind AND a.value = u.value AND a.namespace = ''
     WHERE a.project_id = r.project AND a.entity = r.entity;
    SELECT coalesce(array_agg(DISTINCT c), '{}') INTO cands FROM unnest(coalesce(hits, '{}') || cur) c
        WHERE c IS NOT NULL;
    IF cardinality(cands) > 0 THEN
        PERFORM 1 FROM audience_identities WHERE id = ANY(cands) ORDER BY id FOR KEY SHARE;
        -- An alias always sits on a live identity, so one folded since the read means a merge ran: read again.
        IF EXISTS (SELECT 1 FROM audience_identities WHERE id = ANY(cands) AND merged_into IS NOT NULL) THEN
            RAISE EXCEPTION 'audience identity merged during the link' USING ERRCODE = 'serialization_failure';
        END IF;
    END IF;
    IF cardinality(cands) > 1 THEN
        FOR i IN 1 .. cardinality(cands) LOOP
            FOR j IN i + 1 .. cardinality(cands) LOOP
                PERFORM audience_conflict(r.org, r.project, cands[i], cands[j], p_row, evidence);
            END LOOP;
        END LOOP;
        RETURN cur;
    END IF;
    IF cardinality(cands) = 0 THEN
        INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (r.org, r.project, r.entity)
        RETURNING id INTO ident;
    ELSE
        ident := cands[1];
    END IF;
    INSERT INTO audience_aliases (organization_id, project_id, identity_id, entity, kind, value, source)
    SELECT r.org, r.project, ident, r.entity, u.kind, u.value, src
      FROM unnest(coalesce(kinds, '{}'), coalesce(vals, '{}')) AS u(kind, value) ORDER BY u.kind, u.value
    ON CONFLICT (project_id, entity, kind, namespace, value) DO NOTHING;
    -- Another turn took a value between the read and the insert: a value of the row is on an identity it is not on.
    -- The error rolls the pass back, the new identity and the aliases added with it, and the next pass sees the
    -- other turn's identity and records the conflict.
    IF EXISTS (
        SELECT 1 FROM audience_aliases a JOIN unnest(coalesce(kinds, '{}'), coalesce(vals, '{}')) AS u(kind, value)
          ON a.kind = u.kind AND a.value = u.value AND a.namespace = ''
         WHERE a.project_id = r.project AND a.entity = r.entity AND a.identity_id <> ident) THEN
        RAISE EXCEPTION 'audience value taken during the link' USING ERRCODE = 'serialization_failure';
    END IF;
    INSERT INTO audience_row_links (list_row_id, organization_id, identity_id) VALUES (p_row, r.org, ident)
    ON CONFLICT (list_row_id) DO NOTHING;
    RETURN ident;
END $$;

-- Mark rows for the link task. A repeat refreshes the time, so a link that read the row before the new write does not
-- delete the mark (it deletes only the time it read).
CREATE FUNCTION audience_mark_rows(p_rows uuid[], p_source text DEFAULT NULL) RETURNS void LANGUAGE sql AS $$
    INSERT INTO audience_link_queue (list_row_id, organization_id, source)
    SELECT id, organization_id, p_source FROM list_rows WHERE id = ANY(p_rows) ORDER BY id
    ON CONFLICT (list_row_id) DO UPDATE SET queued_at = clock_timestamp(), attempts = 0,
        source = coalesce(excluded.source, audience_link_queue.source)
$$;

-- Attach a provider id (or any exact alias) to an identity. Free: attached, and a repeat is a no-op. Held by that
-- identity: nothing changes. Held by another one: a conflict, no merge. Returns 'attached', 'known' or 'conflict'.
-- A value that cannot be normalized, or a kind the identity's entity cannot hold, raises.
CREATE FUNCTION audience_attach_alias(p_identity uuid, p_kind text, p_namespace text, p_value text, p_source text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    d record;
    ident uuid := audience_live(p_identity);
    v text;
    added int;
    held record;
BEGIN
    SELECT organization_id AS org, project_id AS project, entity INTO d FROM audience_identities WHERE id = ident;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'audience identity not found' USING ERRCODE = 'no_data_found';
    END IF;
    v := audience_alias_target(d.entity, p_kind, p_namespace, p_value);
    INSERT INTO audience_aliases (organization_id, project_id, identity_id, entity, kind, namespace, value, source)
    VALUES (d.org, d.project, ident, d.entity, p_kind, p_namespace, v, p_source)
    ON CONFLICT (project_id, entity, kind, namespace, value) DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;
    IF added = 1 THEN
        RETURN 'attached';
    END IF;
    SELECT a.identity_id, a.kind, a.value, a.source, a.observed_at INTO held FROM audience_aliases a
     WHERE a.project_id = d.project AND a.entity = d.entity AND a.kind = p_kind AND a.namespace = p_namespace
       AND a.value = v;
    IF held.identity_id = ident THEN
        RETURN 'known';
    END IF;
    PERFORM audience_conflict(d.org, d.project, ident, held.identity_id, NULL, jsonb_build_array(
        jsonb_build_object('kind', held.kind, 'namespace', p_namespace, 'value', held.value,
            'source', held.source, 'observed_at', held.observed_at, 'identity_id', held.identity_id),
        jsonb_build_object('kind', p_kind, 'namespace', p_namespace, 'value', v, 'source', p_source,
            'identity_id', ident)));
    RETURN 'conflict';
END $$;

-- Link a person or account that no list row holds (an external lead): the identity that holds the alias in the
-- project, else a new one that takes it. Returns the identity and 'known' or 'attached'.
CREATE FUNCTION audience_attach_new(p_project uuid, p_entity text, p_kind text, p_namespace text, p_value text,
    p_source text, OUT identity_id uuid, OUT result text) LANGUAGE plpgsql AS $$
DECLARE
    org uuid;
    v text := audience_alias_target(p_entity, p_kind, p_namespace, p_value);
    held uuid;
    added int;
    attempt int;
BEGIN
    SELECT organization_id INTO org FROM projects WHERE id = p_project;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'project not found' USING ERRCODE = 'no_data_found';
    END IF;
    FOR attempt IN 1 .. 3 LOOP
        SELECT a.identity_id INTO held FROM audience_aliases a
         WHERE a.project_id = p_project AND a.entity = p_entity AND a.kind = p_kind AND a.namespace = p_namespace
           AND a.value = v;
        IF FOUND THEN
            identity_id := audience_live(held);
            result := 'known';
            RETURN;
        END IF;
        INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (org, p_project, p_entity)
        RETURNING id INTO identity_id;
        INSERT INTO audience_aliases (organization_id, project_id, identity_id, entity, kind, namespace, value, source)
        VALUES (org, p_project, identity_id, p_entity, p_kind, p_namespace, v, p_source)
        ON CONFLICT (project_id, entity, kind, namespace, value) DO NOTHING;
        GET DIAGNOSTICS added = ROW_COUNT;
        IF added = 1 THEN
            result := 'attached';
            RETURN;
        END IF;
        DELETE FROM audience_identities WHERE id = identity_id;
    END LOOP;
    RAISE EXCEPTION 'audience attach lost three races' USING ERRCODE = 'serialization_failure';
END $$;

-- Backfill, set-based. Every row with an exact alias, oldest first, as audience_link_row would take them in turn
-- (tests/test_audience_sql.py compares the two on random data). Cells are not touched, and rows, charges and
-- activities keep their ids.
--
-- Rows are grouped by the aliases they share. A group in which every row after the first shares an alias with an
-- earlier row is one identity, and is written in a few statements. A group where a later row bridges rows that did
-- not share an alias can become a conflict, so audience_link_row takes its rows one by one, in order.
CREATE TEMP TABLE bf_row ON COMMIT DROP AS
SELECT w.id AS row_id, w.organization_id AS org, l.project_id AS project, l.entity, w.cells,
       row_number() OVER (ORDER BY w.created_at, w.id) AS ord
  FROM list_rows w JOIN lists l ON l.id = w.list_id AND l.organization_id = w.organization_id;

CREATE TEMP TABLE bf_alias ON COMMIT DROP AS
SELECT DISTINCT r.row_id, r.project, r.entity, r.ord, a.kind, a.value
  FROM bf_row r CROSS JOIN LATERAL (
      SELECT 'email' AS kind, audience_alias_value('email', r.cells->>'email') AS value WHERE r.entity = 'person'
      UNION ALL SELECT 'linkedin_url', audience_entity_alias(r.entity, 'linkedin_url', r.cells->>'linkedin_url')
      UNION ALL SELECT 'domain', audience_alias_value('domain',
          coalesce(nullif(btrim(r.cells->>'domain'), ''), r.cells->>'website')) WHERE r.entity = 'company'
  ) a WHERE a.value IS NOT NULL;
CREATE INDEX ON bf_alias (project, entity, kind, value);
CREATE INDEX ON bf_alias (row_id);
CREATE UNIQUE INDEX ON bf_row (ord);
ANALYZE bf_row;
ANALYZE bf_alias;

-- label = the oldest row of the group. Union-find by hooking and pointer jumping: each round joins every root to the
-- smaller root next to it over the edges (a row, the oldest row that holds one of its aliases), then flattens the
-- trees. A chain of n rows takes about log n rounds, where passing the smallest label along it took n.
CREATE TEMP TABLE bf_edge ON COMMIT DROP AS
SELECT DISTINCT g.first AS lo, a.ord AS hi FROM bf_alias a JOIN (
    SELECT project, entity, kind, value, min(ord) AS first FROM bf_alias GROUP BY 1, 2, 3, 4) g
  ON g.project = a.project AND g.entity = a.entity AND g.kind = a.kind AND g.value = a.value
 WHERE a.ord > g.first;
CREATE INDEX ON bf_edge (lo);
CREATE INDEX ON bf_edge (hi);
CREATE TEMP TABLE bf_parent ON COMMIT DROP AS SELECT DISTINCT ord, ord AS p FROM bf_alias;
CREATE UNIQUE INDEX ON bf_parent (ord);
ANALYZE bf_edge;
DO $$
BEGIN
    LOOP
        UPDATE bf_parent t SET p = h.lo FROM (
            SELECT greatest(pe.p, pl.p) AS root, min(least(pe.p, pl.p)) AS lo FROM bf_edge e
              JOIN bf_parent pe ON pe.ord = e.hi JOIN bf_parent pl ON pl.ord = e.lo
             WHERE pl.p <> pe.p GROUP BY greatest(pe.p, pl.p)) h
         WHERE t.ord = h.root;
        EXIT WHEN NOT FOUND;
        LOOP
            UPDATE bf_parent t SET p = u.p FROM bf_parent u WHERE u.ord = t.p AND u.p <> t.p;
            EXIT WHEN NOT FOUND;
        END LOOP;
    END LOOP;
END $$;
CREATE TEMP TABLE bf_label ON COMMIT DROP AS SELECT r.row_id, t.p AS label FROM bf_parent t JOIN bf_row r ON r.ord = t.ord;
CREATE UNIQUE INDEX ON bf_label (row_id);
ANALYZE bf_label;

-- A group is irregular when a row that is not its first has no alias that an earlier row holds.
CREATE TEMP TABLE bf_irregular ON COMMIT DROP AS
SELECT DISTINCT l.label FROM bf_label l JOIN bf_row r ON r.row_id = l.row_id
 WHERE r.ord <> l.label AND NOT EXISTS (
     SELECT 1 FROM bf_alias a JOIN bf_alias e ON e.project = a.project AND e.entity = a.entity AND e.kind = a.kind
        AND e.value = a.value AND e.ord < a.ord WHERE a.row_id = l.row_id);
CREATE UNIQUE INDEX ON bf_irregular (label);

CREATE TEMP TABLE bf_identity ON COMMIT DROP AS
SELECT g.label, gen_random_uuid() AS id FROM (
    SELECT DISTINCT l.label FROM bf_label l WHERE NOT EXISTS (SELECT 1 FROM bf_irregular i WHERE i.label = l.label)
) g;
CREATE UNIQUE INDEX ON bf_identity (label);

INSERT INTO audience_identities (id, organization_id, project_id, entity)
SELECT i.id, r.org, r.project, r.entity FROM bf_identity i JOIN bf_row r ON r.ord = i.label;

INSERT INTO audience_aliases (organization_id, project_id, identity_id, entity, kind, value, source)
SELECT DISTINCT ON (a.project, a.entity, a.kind, a.value) r.org, a.project, i.id, a.entity, a.kind, a.value,
       coalesce(nullif(btrim(r.cells->>'source'), ''), 'list')
  FROM bf_alias a JOIN bf_label l ON l.row_id = a.row_id JOIN bf_identity i ON i.label = l.label
  JOIN bf_row r ON r.row_id = a.row_id
 ORDER BY a.project, a.entity, a.kind, a.value, a.ord
ON CONFLICT (project_id, entity, kind, namespace, value) DO NOTHING;

INSERT INTO audience_row_links (list_row_id, organization_id, identity_id)
SELECT r.row_id, r.org, i.id FROM bf_label l JOIN bf_identity i ON i.label = l.label JOIN bf_row r ON r.row_id = l.row_id;

ANALYZE audience_identities;
ANALYZE audience_aliases;
ANALYZE audience_row_links;

DO $$
DECLARE
    rid uuid;
BEGIN
    FOR rid IN SELECT r.row_id FROM bf_row r JOIN bf_label l ON l.row_id = r.row_id
                 JOIN bf_irregular i ON i.label = l.label ORDER BY r.ord LOOP
        PERFORM audience_link_row(rid);
    END LOOP;
END $$;
