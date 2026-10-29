-- #932: the outbox of HubSpot timeline writes that are not a native send or inbound message (#528). One row per
-- source fact; a source records it once, the writer in app.crm.timeline drains it. Organization data: a fact whose
-- owner is gone still has a home and shows on the status card. Why a pending row waits (grant revoked, owner left,
-- hold, kill switch) is never stored: it is read from the state that owns it.
CREATE TABLE crm_timeline_events (
    source          text NOT NULL CHECK (source IN ('instantly')),
    source_id       uuid NOT NULL,              -- the source row's id; outlives it, so no foreign key
    organization_id uuid NOT NULL,
    project_id      uuid NOT NULL,
    owner_id        uuid REFERENCES "user"(id) ON DELETE SET NULL,   -- whose grant writes; NULL: skipped
    kind            text NOT NULL CHECK (kind IN ('outgoing_email', 'incoming_email', 'opt_out')),
    email           text NOT NULL CHECK (email = lower(btrim(email)) AND email <> ''),
    occurred_at     timestamptz NOT NULL,       -- the latest time the source gave; sharpens until the first claim
    dedupe_key      text NOT NULL,              -- one physical event inside a project
    message_id      text CHECK (message_id <> '' AND char_length(message_id) <= 500),  -- normalised
    person          jsonb NOT NULL DEFAULT '{}',  -- fill-empty contact properties, as of the record
    context         jsonb NOT NULL DEFAULT '{}',  -- provider, campaign, step, project, list, subject, body
    state           text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'claimed', 'logged', 'uncertain', 'failed', 'skipped')),
    code            text CHECK (code IN ('CRM_TIMELINE.NO_OWNER', 'CRM_TIMELINE.OUTSIDE_WRITE_BACK',
        'CRM_TIMELINE.DUPLICATE', 'CRM_TIMELINE.NATIVE_DUPLICATE', 'CRM_TIMELINE.REFUSED',
        'CRM_TIMELINE.CONTACT_REFUSED', 'CRM_TIMELINE.ANSWER_LOST')),  -- for skipped, failed and uncertain
    request         jsonb,                      -- {object, hs_timestamp, direction}: frozen at the first claim
    portal_id       text,                       -- the portal the last claim went to
    activity_id     text,                       -- HubSpot's id; '' when it landed and the id was lost
    attempts        smallint NOT NULL DEFAULT 0,  -- refusals (4xx) of the contact or the activity
    claimed_at      timestamptz,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    settled_at      timestamptz,
    PRIMARY KEY (source, source_id),
    CONSTRAINT crm_timeline_events_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CHECK ((state IN ('skipped', 'failed', 'uncertain')) = (code IS NOT NULL)),
    CHECK (state NOT IN ('claimed', 'logged', 'uncertain') OR request IS NOT NULL),
    CHECK (state <> 'logged' OR activity_id IS NOT NULL),
    CHECK (state <> 'claimed' OR claimed_at IS NOT NULL)
);
CREATE INDEX idx_crm_timeline_events_organization_id ON crm_timeline_events (organization_id);
CREATE INDEX crm_timeline_events_project ON crm_timeline_events (project_id, state);
CREATE INDEX crm_timeline_events_owner ON crm_timeline_events (owner_id, email, occurred_at DESC);
CREATE INDEX crm_timeline_events_due ON crm_timeline_events (occurred_at) WHERE state IN ('pending', 'claimed');
CREATE INDEX crm_timeline_events_message ON crm_timeline_events (message_id) WHERE message_id IS NOT NULL;
-- One physical event once per project. A skipped row sits outside it, so the loser of a dedupe is still recorded.
CREATE UNIQUE INDEX crm_timeline_events_once ON crm_timeline_events (project_id, dedupe_key) WHERE state <> 'skipped';

-- The native duplicate check of the drain (queries.py) compares this exact expression for each open row of each sweep.
CREATE INDEX outreach_inbound_messages_normalised_message_id
    ON outreach_inbound_messages ((nullif(lower(btrim(rfc_message_id, '<> ')), '')));

-- What a retry or a lost answer compares against must not move under it: the identity never changes, the request
-- and the activity id are set once, and the time moves only until the first claim froze it into the request.
CREATE FUNCTION crm_timeline_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.source, NEW.source_id, NEW.organization_id, NEW.project_id, NEW.email, NEW.kind, NEW.dedupe_key)
       IS DISTINCT FROM
       (OLD.source, OLD.source_id, OLD.organization_id, OLD.project_id, OLD.email, OLD.kind, OLD.dedupe_key) THEN
        RAISE EXCEPTION 'crm_timeline_events: the identity of a fact never changes';
    END IF;
    IF OLD.request IS NOT NULL AND NEW.request IS DISTINCT FROM OLD.request THEN
        RAISE EXCEPTION 'crm_timeline_events: a frozen request never changes';
    END IF;
    IF OLD.activity_id IS NOT NULL AND NEW.activity_id IS DISTINCT FROM OLD.activity_id THEN
        RAISE EXCEPTION 'crm_timeline_events: an activity id is set once';
    END IF;
    IF OLD.request IS NOT NULL AND NEW.occurred_at <> OLD.occurred_at THEN
        RAISE EXCEPTION 'crm_timeline_events: the time of a claimed fact never changes';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER crm_timeline_events_guard BEFORE UPDATE ON crm_timeline_events
    FOR EACH ROW EXECUTE FUNCTION crm_timeline_events_guard();

-- Where each source's feed has been read up to, per project. Moved in the transaction that records the page.
CREATE TABLE crm_timeline_cursors (
    project_id      uuid NOT NULL,
    source          text NOT NULL CHECK (source IN ('instantly')),
    organization_id uuid NOT NULL,
    after           bigint NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, source),
    CONSTRAINT crm_timeline_cursors_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_crm_timeline_cursors_organization_id ON crm_timeline_cursors (organization_id);
