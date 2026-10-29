-- The system role writes only what it is granted (PR 14 of the organization-scoping run).
--
-- A System(reason) scope runs as zenbree_system, which bypasses row-level security. Until now it also held INSERT,
-- UPDATE and DELETE on every tenant relation, so a System block that forgot its organization could write into every
-- organization. This takes those writes away and grants back only what the System code paths write: INSERT on a
-- table, UPDATE by column, DELETE where a reason deletes. SELECT stays: crossing organizations to read is what a
-- reason is for. tests/test_system_grants_sql.py lists the same grants with their reasons and fails on any other.
--
-- A tenant relation is a table or view in public that is not labelled zb:unscoped. The unscoped tables (Better Auth,
-- settings, webhook_events and the cost tables) keep the grants of 20261013195148_row_level_security.sql.
--
-- No function in public or zb is SECURITY DEFINER, so the credit functions (increment_credits, deduct_credits,
-- settle_hold, release_reservation, settle_reservation_with_slack, commit_reservation) write as the caller: their
-- tables are in the grants below for the reasons that call them. A row lock (FOR UPDATE) needs UPDATE on one column,
-- which each locked table here has.
--
-- keep_campaign_reply_key, the AFTER DELETE trigger of outreach_campaigns, also runs as the caller. No System path
-- deletes a campaign or a project (only DELETE /v1/campaigns/{id} and DELETE /v1/projects/{id}, both as the member),
-- so zenbree_system holds no DELETE on outreach_campaigns and the trigger never runs as it.

DO $$
DECLARE
    t regclass;
BEGIN
    FOR t IN
        SELECT c.oid::regclass FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v')
          AND c.relname <> 'schema_migrations'
          AND coalesce(obj_description(c.oid, 'pg_class'), '') !~ '^zb:unscoped (identity|global|cost):'
    LOOP
        -- A table-wide REVOKE also takes the column grants, so the grants below come after it.
        EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON %s FROM zenbree_system', t);
    END LOOP;
END $$;

-- A new relation: zenbree_system reads it and writes nothing. A migration whose table a System path writes grants
-- that write itself, with its reason, and adds it to SYSTEM_WRITES in tests/test_system_grants_sql.py.
CREATE OR REPLACE FUNCTION zb.grant(t regclass) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    rel pg_class%ROWTYPE;
    guard text := current_setting('zb.scoping', true);
BEGIN
    SELECT * INTO rel FROM pg_class WHERE oid = t;
    IF rel.relnamespace <> 'public'::regnamespace
       OR rel.relname IN ('jwks', 'account', 'session', 'verification', 'schema_migrations') THEN
        RETURN;
    END IF;
    PERFORM set_config('zb.scoping', 'on', true);
    IF rel.relkind = 'S' THEN
        EXECUTE format('GRANT USAGE, SELECT, UPDATE ON SEQUENCE %s TO zenbree_app, zenbree_system', t);
    ELSIF rel.relkind IN ('r', 'p', 'v') THEN
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO zenbree_app', t);
        EXECUTE format('GRANT SELECT ON %s TO zenbree_system', t);
    ELSIF rel.relkind = 'm' THEN
        EXECUTE format('GRANT SELECT ON %s TO zenbree_system', t);  -- zenbree_app: zb.scope decides
    END IF;
    PERFORM set_config('zb.scoping', coalesce(guard, ''), true);
END $$;

-- RESERVATION_REAPER (app/worker.py): reap_stale_reservations_ids -> settle_hold -> settle_reservation_with_slack or
-- release_reservation, and commit_settling_reservations -> commit_reservation, close stale holds in every
-- organization. lookup_claims.delete_dead drops the claims of jobs that are no longer live.
GRANT INSERT, UPDATE (status, type, settlement_refund_amount, settlement_net_credits, settlement_balance_after)
    ON credit_transactions TO zenbree_system;
GRANT UPDATE (state, settled_at) ON row_charges TO zenbree_system;
GRANT DELETE ON lookup_claims TO zenbree_system;

-- RESERVATION_REAPER, and STRIPE_WEBHOOK and BACK_OFFICE through increment_credits and deduct_credits: the wallet.
-- INSERT: increment_credits opens a wallet the signup hook did not (a paid plan, an admin grant).
GRANT INSERT, UPDATE (credits_remaining, updated_at) ON user_credits TO zenbree_system;

-- STRIPE_WEBHOOK (app/billing/routes.py process_event): purchases, the subscription mirror, a refund, the auto top-up
-- switch and claim, and the plan_renewed notice to the organization's admins.
GRANT INSERT, UPDATE (refunded_cents) ON purchases TO zenbree_system;
GRANT INSERT, UPDATE (plan_id, status, current_period_end, cancel_at_period_end, cancel_at, event_created,
                      updated_at, scheduled_plan_id) ON subscriptions TO zenbree_system;
GRANT UPDATE (enabled, awaiting_card, last_error, charging_since, updated_at) ON auto_topups TO zenbree_system;
GRANT INSERT ON notifications TO zenbree_system;

-- ACCOUNT_DELETE (app/auth/account_delete.py), and BACK_OFFICE before an admin deletes a user: the sole-member
-- organizations stop billing (subscriptions.status, auto_topups.enabled above), and release_senders_of disconnects
-- the account's senders, unlinks them from every client and keeps the creator on their campaigns.
GRANT DELETE ON project_senders TO zenbree_system;
GRANT UPDATE (status, status_detail, refresh_token_encrypted, granted_scopes, updated_at, provider_account_id)
    ON user_oauth_connections TO zenbree_system;
GRANT UPDATE (former_owner_id) ON outreach_campaigns TO zenbree_system;

-- STALE_JOBS (app/agent/routes.py): a new turn fails its user's dead jobs in every organization.
GRANT UPDATE (status, error, finished_at, updated_at) ON jobs TO zenbree_system;

-- CSV_REAPER (app/lists/csv_run.py reap): a stuck import fails, an old one drops its file.
GRANT UPDATE (status, error, content, discarded, updated_at) ON csv_imports TO zenbree_system;

-- DIGESTS (app/notifications/service.py take_digests): the digest takes its queue.
GRANT UPDATE (email_pending, emailed_at) ON notifications TO zenbree_system;

-- PUSH_TAKEOVER (app/notifications/push.py subscribe): a browser endpoint moves to the user who proves its secret.
GRANT INSERT, UPDATE (user_id, p256dh, auth, created_at) ON push_subscriptions TO zenbree_system;

-- OPT_OUT (app/outreach/replies.py, the unsubscribe routes, the campaign beat): an opt-out stops the person in every
-- organization. The message is filed, the targets and their pending sends stop, the person is excluded in every
-- client that wrote to them (taking over an onboarding rule), and the page's undo lifts what the link added.
GRANT INSERT ON outreach_inbound_messages TO zenbree_system;
GRANT UPDATE (status, replied_at, paused_until) ON outreach_campaign_targets TO zenbree_system;
GRANT UPDATE (status, error_code) ON outreach_campaign_sends TO zenbree_system;
GRANT INSERT, UPDATE (source, origin), DELETE ON exclusions TO zenbree_system;
