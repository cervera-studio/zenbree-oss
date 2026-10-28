-- Delivery history of the outbound webhooks (#943): one row per attempt, so an admin can see why an endpoint
-- fails without reading worker logs. It holds no payload, no URL and no response body: the data of
-- `sequence.stopped` and `contact.replied` carries an email, a name and a LinkedIn URL, and a Zapier-style URL
-- carries a token. The newest rows of an endpoint are kept (the writer prunes in its own transaction), so the
-- table stays bounded without a beat task that would cross organizations.
ALTER TABLE user_webhooks ADD UNIQUE (id, organization_id);
-- A receiver's 410 stops the deliveries but keeps the endpoint listed, with its history, until an admin deletes it.
-- An admin's delete stays `revoked_at`, and removes the history.
ALTER TABLE user_webhooks ADD COLUMN gone_at timestamptz;

CREATE TABLE webhook_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    webhook_id uuid NOT NULL,
    -- the `webhook-id` header: stable across the retries of one event, so the receiver and this list agree
    event_id text NOT NULL,
    event_type text NOT NULL,
    attempt int NOT NULL CHECK (attempt >= 1),
    attempted_at timestamptz NOT NULL DEFAULT now(),
    status_code int,
    category text CHECK (category IN ('timeout', 'blocked', 'unreachable', 'rate_limited', 'server_error', 'rejected', 'gone')),
    -- true when a retry is queued after this attempt
    will_retry boolean NOT NULL DEFAULT false,
    CONSTRAINT webhook_deliveries_webhook_id_org_fkey FOREIGN KEY (webhook_id, organization_id)
        REFERENCES user_webhooks (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_webhook_deliveries_endpoint ON webhook_deliveries(webhook_id, attempted_at DESC);
CREATE INDEX idx_webhook_deliveries_organization_id ON webhook_deliveries(organization_id);
