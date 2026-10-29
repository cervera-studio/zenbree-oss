-- One row per browser: the push service's endpoint is the identity, so a browser that signs in as another user
-- moves the row to that user instead of alerting two people.
CREATE TABLE push_subscriptions (
    endpoint   TEXT PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX push_subscriptions_user_idx ON push_subscriptions (user_id);
