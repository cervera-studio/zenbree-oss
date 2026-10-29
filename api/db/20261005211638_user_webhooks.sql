-- Outbound webhook endpoints (#93), from the earlier implementation 20260725150000_user_webhooks_create.sql.
-- The signing secret signs every delivery, so a one-way hash (user_api_keys) is unusable: it is stored
-- Fernet-encrypted (app.integrations.token_crypto). Only the delivery path reads it; the API shows it once, on create.
CREATE TABLE user_webhooks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    -- https only: the signature protects integrity, not confidentiality. SSRF is checked in the app
    -- (DNS-resolved, so a CHECK cannot do it), on create and on every delivery.
    url         TEXT NOT NULL CHECK (url ~ '^https://' AND char_length(url) <= 2000),
    secret_encrypted TEXT NOT NULL,
    -- The subscription: an event added later never reaches an endpoint created before it existed.
    event_names TEXT[] NOT NULL DEFAULT '{}',
    label       TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 120),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ  -- soft delete; delivery filters IS NULL
);

CREATE INDEX idx_user_webhooks_owner ON user_webhooks(user_id);
