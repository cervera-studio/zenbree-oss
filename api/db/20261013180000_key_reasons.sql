-- The reason each creator cascade and each per-user key on organization data stays. Comments only: no behavior changes.
-- test_tenant_catalog_sql.py (R3, R4) fails for a new one without a reason, so a schema keyed to a person (#333, #473)
-- is a reviewed choice.

COMMENT ON CONSTRAINT csv_imports_user_id_fkey ON csv_imports IS
    'zb:creator-cascade: an import is private to the member who made it (every route reads it by user_id), so it goes with their account';

COMMENT ON CONSTRAINT low_credit_alerts_user_id_fkey ON low_credit_alerts IS
    'zb:creator-cascade: the row is that admin''s armed state for the low-credit notice, not data of the organization';

COMMENT ON CONSTRAINT user_api_keys_user_id_fkey ON user_api_keys IS
    'zb:creator-cascade: a key authenticates as the member who created it, so it must stop working with their account';

COMMENT ON INDEX uq_jobs_one_active_per_user IS
    'zb:per-user: a person runs one chat turn at a time, in any organization (POST /v1/chat answers 409 CHAT.JOB_RUNNING, docs/contracts-m1.md)';

COMMENT ON INDEX user_oauth_connections_hubspot_user_key IS
    'zb:per-user: a HubSpot grant is each user''s own, since their owner id decides what the gate blocks, so a user has one';

COMMENT ON INDEX outreach_inbound_messages_recipient_channel_provider_message_ke IS
    'zb:per-user: the owner is one person, found from the reply address (email) or from the matched campaign target (LinkedIn), so a provider retry is the same message wherever that person works';
