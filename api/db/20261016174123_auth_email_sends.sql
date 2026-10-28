-- Auth emails sent (verification, password reset, change of address), for the per-address cap (#981,
-- web/lib/auth-email-throttle.ts). Better Auth only limits them per IP.
CREATE TABLE auth_email_sends (
    email   TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_auth_email_sends_email ON auth_email_sends(email, sent_at);
COMMENT ON TABLE auth_email_sends IS 'zb:unscoped global: sent to an address before any account or organization is known';

-- RETENTION (app/core/retention.py, the daily llm_usage_ttl_cleanup task) deletes the rows of addresses the web does not mail again.
GRANT DELETE ON auth_email_sends TO zenbree_system;
