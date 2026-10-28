-- The organization's money outlives the member who moved it (#323, F1 of the #328 review). Since #306 the wallet,
-- its ledger, purchases, Stripe customer, subscriptions and auto top-up belong to the organization, and user_id
-- only says who opened, spent or bought. It still cascaded with the user: deleting an account deleted the
-- organization's wallet row and every ledger row that member made, which erased its audit history and reset
-- project_month_spend (a hard monthly cap could be bypassed). Now a deleted user leaves user_id NULL.
-- Forward-only; the previous image never writes NULL and reads user_id only through LEFT JOINs or per-user filters.
DO $$
DECLARE
    t TEXT;
    fk TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['user_credits', 'credit_transactions', 'purchases', 'billing_customers', 'subscriptions',
                             'auto_topups']
    LOOP
        -- The baseline's inline REFERENCES: the name is Postgres's default, looked up rather than assumed.
        FOR fk IN
            SELECT c.conname FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = t::regclass AND c.contype = 'f' AND a.attname = 'user_id'
              AND c.confrelid = '"user"'::regclass
        LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', t, fk);
        END LOOP;
        EXECUTE format('ALTER TABLE %I ALTER COLUMN user_id DROP NOT NULL', t);
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE SET NULL',
            t, t || '_user_id_fkey'
        );
    END LOOP;
END;
$$;
