-- Instantly campaign events (#930): the webhook Zenbree registers in a connected workspace, the reconciliation state
-- of each connection, and the facts (sent, bounced, reply, unsubscribed, interest) about the leads Zenbree enrolled.
--
-- zb.on_ddl gives each table row-level security on organization_id and gives zenbree_system SELECT only: the
-- webhook route and the beat find a connection as System, then write as ForOrg of its organization.

-- The composite foreign key of instantly_events needs it, as every tenant table has.
ALTER TABLE instantly_leads ADD CONSTRAINT instantly_leads_id_org_key UNIQUE (id, organization_id);

-- One webhook Zenbree created at Instantly for a connection. The id is the last segment of the callback URL; the
-- secret goes to Instantly once, as a custom header, and only its sha256 is kept. Another workspace, a webhook
-- Instantly disabled or a disconnect retires this row (a new key for the same workspace keeps it): a copy left at
-- Instantly then posts a secret no current row holds, gets 401, and Instantly disables it.
CREATE TABLE instantly_webhook_subscriptions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    -- The workspace the key opened at creation: a payload of another workspace is not recorded.
    workspace_id text NOT NULL CHECK (workspace_id <> ''),
    secret_sha256 bytea NOT NULL CHECK (octet_length(secret_sha256) = 32),
    remote_id text,  -- Instantly's webhook id; NULL while the create call has no answer
    -- creating: the create call has no answer yet. registered: Instantly holds it, no delivery has come yet.
    -- live: an authenticated delivery came. degraded: Instantly disabled it after failed deliveries.
    -- retired: its callbacks are refused.
    status text NOT NULL DEFAULT 'creating'
        CHECK (status IN ('creating', 'registered', 'live', 'degraded', 'retired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    checked_at timestamptz,
    last_delivery_at timestamptz,
    retired_at timestamptz,
    CONSTRAINT instantly_webhook_subscriptions_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_webhook_subscriptions_connection_id_org_fkey FOREIGN KEY (connection_id, organization_id)
        REFERENCES instantly_connections (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_instantly_webhook_subscriptions_organization_id ON instantly_webhook_subscriptions(organization_id);
CREATE INDEX idx_instantly_webhook_subscriptions_project_id ON instantly_webhook_subscriptions(project_id);
CREATE INDEX idx_instantly_webhook_subscriptions_connection_id ON instantly_webhook_subscriptions(connection_id);
-- One current webhook per connection: two would deliver every event twice.
CREATE UNIQUE INDEX instantly_webhook_subscriptions_one_current ON instantly_webhook_subscriptions(connection_id)
    WHERE status <> 'retired';

-- What the beat knows of one connection: whether webhooks are possible, its two cursors over the workspace's
-- emails, its place in the round over the held leads, its lease and its last result. Only
-- app.integrations.instantly_sync.reconcile writes it.
CREATE TABLE instantly_sync_state (
    connection_id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    -- The workspace the cursors below belong to: a reconnect to another workspace starts them again.
    workspace_id text NOT NULL,
    -- Why the connection has no webhook: plan (402), scope (403), refused (another 4xx), no_public_url (this
    -- deployment has no public https address). NULL while one is registered or not tried yet.
    webhook_refusal text CHECK (webhook_refusal IN ('plan', 'scope', 'refused', 'no_public_url')),
    webhook_checked_at timestamptz,
    -- Emails (GET /api/v2/emails) by Instantly's timestamp_created: everything created before the watermark is
    -- recorded. A pass reads from the watermark on; while it is part-way, the cursor is Instantly's
    -- next_starting_after and pass_started_at the watermark it sets when it ends.
    -- When Instantly last refused the email reads (403: a key made without emails:read). Sends and replies are not
    -- read then; the leads round still reads bounces, unsubscribes and labels.
    emails_refused_at timestamptz,
    sent_watermark timestamptz,
    sent_cursor text,
    sent_pass_started_at timestamptz,
    received_watermark timestamptz,
    received_cursor text,
    received_pass_started_at timestamptz,
    -- The round over the held leads (POST /api/v2/leads/list), one campaign at a time. A lead's status and label
    -- are recorded as of leads_round_done_at: the start of the last round that reached every campaign.
    leads_campaign_id text,
    leads_cursor text,  -- the last email of leads_campaign_id read: the held leads are asked for by email
    leads_round_started_at timestamptz,
    leads_round_done_at timestamptz,
    next_run_at timestamptz NOT NULL DEFAULT now(),
    claim_token uuid,
    claim_until timestamptz,
    last_attempt_at timestamptz,
    last_success_at timestamptz,
    last_error_code text,
    failing_since timestamptz,
    -- {reason: count} of the provider records left out (vocabulary.Ignored): an unmapped value shows as a number.
    ignored jsonb NOT NULL DEFAULT '{}',
    CONSTRAINT instantly_sync_state_connection_id_org_fkey FOREIGN KEY (connection_id, organization_id)
        REFERENCES instantly_connections (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_sync_state_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_instantly_sync_state_organization_id ON instantly_sync_state(organization_id);
CREATE INDEX idx_instantly_sync_state_project_id ON instantly_sync_state(project_id);
CREATE INDEX instantly_sync_state_due ON instantly_sync_state(next_run_at);

-- The order in which facts were recorded or changed: #932 and #942 page on it. It moves on an insert and on each
-- enrichment, under the connection's advisory lock, so a reader past a value never skips a later commit.
CREATE SEQUENCE instantly_events_changed_seq;

-- What happened in Instantly to a lead Zenbree enrolled. A row is never deleted and its identity never changes:
-- the trigger below allows only the enrichments of a match between the webhook and the API (an id set once, a reply
-- classified once, a more exact time). #931, #932 and #942 read it through app.integrations.instantly_sync.outcomes.
CREATE TABLE instantly_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),  -- the stable source id a consumer writes once
    changed bigint NOT NULL DEFAULT nextval('instantly_events_changed_seq'),
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    workspace_id text NOT NULL,
    campaign_id text NOT NULL,
    email text NOT NULL CHECK (email = lower(btrim(email)) AND email <> ''),
    kind text NOT NULL CHECK (kind IN ('sent', 'bounced', 'reply', 'unsubscribed', 'interest')),
    reply_nature text CHECK (reply_nature IN ('human', 'auto', 'unclassified')),
    label text CHECK (label IN ('interested', 'meeting_booked', 'meeting_completed', 'won', 'out_of_office',
                                'not_interested', 'wrong_person', 'lost', 'no_show')),
    step integer,
    -- The fact's identity in the connection: the first source's key (see mapping.py).
    fact_key text NOT NULL,
    push_key text,  -- the key of the webhook delivery that named it, set once
    provider_ref text,  -- Instantly's email id, set once
    message_id text,
    occurred_at timestamptz NOT NULL,
    -- event: Instantly's time of the event; record: the email's time; observed: when a lead's status showed it.
    time_basis text NOT NULL CHECK (time_basis IN ('event', 'record', 'observed')),
    received_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT instantly_events_kind_shape CHECK (
        (kind = 'reply') = (reply_nature IS NOT NULL) AND (kind = 'interest') = (label IS NOT NULL)),
    CONSTRAINT instantly_events_fact_key UNIQUE (connection_id, fact_key),
    CONSTRAINT instantly_events_project_id_org_fkey FOREIGN KEY (project_id, organization_id)
        REFERENCES projects (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_events_connection_id_org_fkey FOREIGN KEY (connection_id, organization_id)
        REFERENCES instantly_connections (id, organization_id) ON DELETE CASCADE,
    CONSTRAINT instantly_events_lead_id_org_fkey FOREIGN KEY (lead_id, organization_id)
        REFERENCES instantly_leads (id, organization_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX instantly_events_push_key ON instantly_events(connection_id, push_key) WHERE push_key IS NOT NULL;
CREATE UNIQUE INDEX instantly_events_provider_ref ON instantly_events(connection_id, provider_ref)
    WHERE provider_ref IS NOT NULL;
CREATE INDEX idx_instantly_events_organization_id ON instantly_events(organization_id);
CREATE INDEX idx_instantly_events_project_changed ON instantly_events(project_id, changed);
-- The foreign key's index, and the match of a webhook fact to an API record of the same lead.
CREATE INDEX idx_instantly_events_lead ON instantly_events(lead_id, kind, occurred_at);
-- One person's facts in a project: the stop check of #931.
CREATE INDEX idx_instantly_events_person ON instantly_events(project_id, email);

CREATE FUNCTION instantly_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    rank_old int := array_position(ARRAY['observed', 'record', 'event'], OLD.time_basis);
    rank_new int := array_position(ARRAY['observed', 'record', 'event'], NEW.time_basis);
BEGIN
    IF (NEW.id, NEW.organization_id, NEW.project_id, NEW.connection_id, NEW.lead_id, NEW.workspace_id,
        NEW.campaign_id, NEW.email, NEW.kind, NEW.label, NEW.fact_key, NEW.received_at)
       IS DISTINCT FROM
       (OLD.id, OLD.organization_id, OLD.project_id, OLD.connection_id, OLD.lead_id, OLD.workspace_id,
        OLD.campaign_id, OLD.email, OLD.kind, OLD.label, OLD.fact_key, OLD.received_at) THEN
        RAISE EXCEPTION 'instantly_events: the identity of a fact never changes';
    END IF;
    IF (OLD.push_key IS NOT NULL AND NEW.push_key IS DISTINCT FROM OLD.push_key)
       OR (OLD.provider_ref IS NOT NULL AND NEW.provider_ref IS DISTINCT FROM OLD.provider_ref)
       OR (OLD.message_id IS NOT NULL AND NEW.message_id IS DISTINCT FROM OLD.message_id)
       OR (OLD.step IS NOT NULL AND NEW.step IS DISTINCT FROM OLD.step) THEN
        RAISE EXCEPTION 'instantly_events: a provider id is set once';
    END IF;
    IF NEW.reply_nature IS DISTINCT FROM OLD.reply_nature AND OLD.reply_nature <> 'unclassified' THEN
        RAISE EXCEPTION 'instantly_events: only an unclassified reply is classified';
    END IF;
    IF rank_new < rank_old OR (rank_new = rank_old AND NEW.occurred_at <> OLD.occurred_at) THEN
        RAISE EXCEPTION 'instantly_events: a time changes only to a more exact one';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER instantly_events_guard BEFORE UPDATE ON instantly_events
    FOR EACH ROW EXECUTE FUNCTION instantly_events_guard();
