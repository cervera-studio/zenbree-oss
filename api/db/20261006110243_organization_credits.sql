-- Credits, billing and Stripe on the organization (#306). One wallet per organization: user_credits,
-- credit_transactions, purchases, billing_customers, subscriptions and auto_topups gain organization_id,
-- backfilled from the user's own organization (the one they founded, slug = their id: own_organization()),
-- since every wallet so far was its user's. Every existing customer keeps their balance, Stripe customer and
-- subscription, and what a member spent in a team before this stays on their own wallet, which paid for it.
-- user_id stays as who opened, spent or bought it.
--
-- Every write names the organization (the caller's active one, or the chat's or list's): no fill trigger, and
-- the user-keyed credit functions are dropped, since a user in several organizations (#308) has no single wallet.
-- The previous image's credit calls and inserts therefore fail on this schema (no deploy runs between the two).

-- Its last user was this migration's tables, which take no fill (#308 dropped the other triggers).
DROP FUNCTION fill_organization_id();

-- The organization the user founded (slug = their id, #305/#308), never just their oldest membership: a wallet
-- that was a user's stays theirs, and two users never map to one wallet. Found by slug even without a member
-- row; created with the user as admin when missing, as default_organization() does for a user with none.
CREATE FUNCTION own_organization(p_user_id UUID) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE
    org UUID;
BEGIN
    SELECT id INTO org FROM organization WHERE slug = p_user_id::text;
    IF org IS NOT NULL THEN
        RETURN org;
    END IF;
    INSERT INTO organization (name, slug, "createdAt")
    SELECT coalesce(nullif(btrim(u.name), ''), u.email), u.id::text, u."createdAt" FROM "user" u WHERE u.id = p_user_id
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO org;
    IF org IS NULL THEN  -- no such user, or a concurrent call created it first
        RETURN (SELECT id FROM organization WHERE slug = p_user_id::text);
    END IF;
    INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (org, p_user_id, 'admin', now());
    RETURN org;
END;
$$;

CREATE TEMP TABLE user_org ON COMMIT DROP AS SELECT id AS user_id, own_organization(id) AS org FROM "user";

-- ── The wallet: one row per organization ───────────────────────────────────
-- onboarding_free_used stays on it and keeps being counted per user (app/onboarding, per user until #307).

ALTER TABLE user_credits ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE user_credits c SET organization_id = o.org FROM user_org o WHERE o.user_id = c.user_id;
ALTER TABLE user_credits ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE user_credits DROP CONSTRAINT user_credits_pkey;
ALTER TABLE user_credits ADD PRIMARY KEY (organization_id);
CREATE INDEX idx_user_credits_user ON user_credits(user_id);

ALTER TABLE credit_transactions ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE credit_transactions t SET organization_id = o.org FROM user_org o WHERE o.user_id = t.user_id;
ALTER TABLE credit_transactions ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_credit_tx_org_created ON credit_transactions(organization_id, created_at DESC);
-- "What has this organization already paid for this piece of work" (AI column fills, slack refunds).
CREATE INDEX idx_credit_tx_org_reference ON credit_transactions (organization_id, reference_type, reference_id)
    WHERE reference_id IS NOT NULL;

-- ── Stripe: the customer, its subscription and purchases belong to the organization ──

ALTER TABLE billing_customers ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE billing_customers b SET organization_id = o.org FROM user_org o WHERE o.user_id = b.user_id;
ALTER TABLE billing_customers ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE billing_customers DROP CONSTRAINT billing_customers_pkey;
ALTER TABLE billing_customers ADD PRIMARY KEY (organization_id);

ALTER TABLE subscriptions ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE subscriptions s SET organization_id = o.org FROM user_org o WHERE o.user_id = s.user_id;
ALTER TABLE subscriptions ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_subscriptions_org ON subscriptions(organization_id);

ALTER TABLE purchases ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE purchases p SET organization_id = o.org FROM user_org o WHERE o.user_id = p.user_id;
ALTER TABLE purchases ALTER COLUMN organization_id SET NOT NULL;

-- user_id: the admin who saved the settings last. One row per organization; an admin of several has one in each.
ALTER TABLE auto_topups ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE auto_topups a SET organization_id = o.org FROM user_org o WHERE o.user_id = a.user_id;
ALTER TABLE auto_topups ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE auto_topups DROP CONSTRAINT auto_topups_pkey;
ALTER TABLE auto_topups ADD PRIMARY KEY (organization_id);

-- ── Credit functions take the organization (whose wallet) and the user (who) ──
-- Same bodies as the baseline, keyed by organization. The hold functions (commit, release, settle, mark,
-- reap) still take the reservation id and follow its organization.

CREATE FUNCTION deduct_credits(
    p_organization_id UUID,
    p_user_id        UUID,
    p_amount         INT,
    p_reference_type TEXT  DEFAULT NULL,
    p_reference_id   TEXT  DEFAULT NULL,
    p_tool_name      TEXT  DEFAULT NULL,
    p_session_id     UUID  DEFAULT NULL,
    p_description    TEXT  DEFAULT NULL
)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    new_balance INT;
BEGIN
    UPDATE user_credits
    SET credits_remaining = credits_remaining - p_amount,
        updated_at = NOW()
    WHERE organization_id = p_organization_id
      AND credits_remaining >= p_amount
    RETURNING credits_remaining INTO new_balance;

    IF NOT FOUND THEN
        RETURN -1;
    END IF;

    INSERT INTO credit_transactions (
        organization_id, user_id, type, amount, balance_after,
        reference_type, reference_id, description,
        tool_name, session_id
    ) VALUES (
        p_organization_id, p_user_id, 'deduction', p_amount, new_balance,
        p_reference_type, p_reference_id, p_description,
        p_tool_name, p_session_id
    );

    RETURN new_balance;
END;
$$;

CREATE FUNCTION increment_credits(
    p_organization_id UUID,
    p_user_id        UUID,
    p_amount         INT,
    p_type           TEXT  DEFAULT 'refund',
    p_reference_type TEXT  DEFAULT NULL,
    p_reference_id   TEXT  DEFAULT NULL,
    p_tool_name      TEXT  DEFAULT NULL,
    p_session_id     UUID  DEFAULT NULL,
    p_description    TEXT  DEFAULT NULL
)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    new_balance INT;
BEGIN
    IF p_type NOT IN ('refund', 'renewal', 'purchase', 'bonus', 'adjustment') THEN
        RAISE EXCEPTION 'increment_credits: p_type must be one of refund/renewal/purchase/bonus/adjustment, got %', p_type;
    END IF;

    UPDATE user_credits
    SET credits_remaining = credits_remaining + p_amount,
        updated_at = NOW()
    WHERE organization_id = p_organization_id
    RETURNING credits_remaining INTO new_balance;

    IF NOT FOUND THEN
        -- Defensive: the signup hook should have created the row.
        INSERT INTO user_credits (organization_id, user_id, credits_remaining, created_at, updated_at)
        VALUES (p_organization_id, p_user_id, p_amount, NOW(), NOW())
        RETURNING credits_remaining INTO new_balance;
    END IF;

    INSERT INTO credit_transactions (
        organization_id, user_id, type, amount, balance_after,
        reference_type, reference_id, description,
        tool_name, session_id
    ) VALUES (
        p_organization_id, p_user_id, p_type, p_amount, new_balance,
        p_reference_type, p_reference_id, p_description,
        p_tool_name, p_session_id
    );

    RETURN new_balance;
END;
$$;

CREATE FUNCTION reserve_credits(
    p_organization_id UUID,
    p_user_id        UUID,
    p_amount         INT,
    p_reference_type TEXT  DEFAULT NULL,
    p_reference_id   TEXT  DEFAULT NULL,
    p_tool_name      TEXT  DEFAULT NULL,
    p_session_id     UUID  DEFAULT NULL,
    p_description    TEXT  DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    new_balance     INT;
    reservation_id  UUID;
BEGIN
    UPDATE user_credits
    SET credits_remaining = credits_remaining - p_amount,
        updated_at = NOW()
    WHERE organization_id = p_organization_id
      AND credits_remaining >= p_amount
    RETURNING credits_remaining INTO new_balance;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    INSERT INTO credit_transactions (
        organization_id, user_id, type, amount, balance_after, status,
        reference_type, reference_id, description,
        tool_name, session_id
    ) VALUES (
        p_organization_id, p_user_id, 'reservation', p_amount, new_balance, 'reserved',
        p_reference_type, p_reference_id, p_description,
        p_tool_name, p_session_id
    )
    RETURNING id INTO reservation_id;

    RETURN reservation_id;
END;
$$;

DROP FUNCTION deduct_credits(UUID, INT, TEXT, TEXT, TEXT, UUID, TEXT);
DROP FUNCTION increment_credits(UUID, INT, TEXT, TEXT, TEXT, TEXT, UUID, TEXT);
DROP FUNCTION reserve_credits(UUID, INT, TEXT, TEXT, TEXT, UUID, TEXT);

CREATE OR REPLACE FUNCTION release_reservation(p_reservation_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    rsv             credit_transactions%ROWTYPE;
    new_balance     INT;
BEGIN
    -- Row lock: concurrent commit/release can't double-process it.
    SELECT * INTO rsv FROM credit_transactions
        WHERE id = p_reservation_id
          AND status = 'reserved'
          AND type = 'reservation'
        FOR UPDATE;

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    UPDATE credit_transactions
        SET status = 'released'
        WHERE id = p_reservation_id;

    UPDATE user_credits
        SET credits_remaining = credits_remaining + rsv.amount,
            updated_at = NOW()
        WHERE organization_id = rsv.organization_id
        RETURNING credits_remaining INTO new_balance;

    INSERT INTO credit_transactions (
        organization_id, user_id, type, amount, balance_after,
        reference_type, reference_id, description,
        tool_name, session_id
    ) VALUES (
        rsv.organization_id, rsv.user_id, 'refund', rsv.amount, new_balance,
        rsv.reference_type, rsv.reference_id,
        'Released reservation ' || p_reservation_id::TEXT,
        rsv.tool_name, rsv.session_id
    );

    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION settle_reservation_with_slack(
    p_reservation_id UUID,
    p_refund_amount INT
)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    rsv credit_transactions%ROWTYPE;
    existing_refund credit_transactions%ROWTYPE;
    actual_refund INT := 0;
    final_balance INT;
BEGIN
    SELECT * INTO rsv
    FROM credit_transactions
    WHERE id = p_reservation_id
    FOR UPDATE;

    IF NOT FOUND OR rsv.status = 'released' THEN
        RETURN NULL;
    END IF;

    IF p_refund_amount < 0 OR p_refund_amount > rsv.amount THEN
        RAISE EXCEPTION
            'settle_reservation_with_slack: refund % outside reservation amount %',
            p_refund_amount, rsv.amount;
    END IF;

    IF rsv.settlement_net_credits IS NOT NULL THEN
        IF rsv.settlement_refund_amount <> p_refund_amount THEN
            RAISE EXCEPTION
                'settle_reservation_with_slack: retry amount % differs from prior refund %',
                p_refund_amount, rsv.settlement_refund_amount;
        END IF;
        RETURN jsonb_build_object(
            'net_credits', rsv.settlement_net_credits,
            'remaining', rsv.settlement_balance_after
        );
    END IF;

    IF rsv.status IN ('reserved', 'settling') AND rsv.type = 'reservation' THEN
        UPDATE credit_transactions
        SET status = 'committed', type = 'deduction'
        WHERE id = p_reservation_id;
    ELSIF NOT (rsv.status = 'committed' AND rsv.type = 'deduction') THEN
        RETURN NULL;
    END IF;

    SELECT * INTO existing_refund
    FROM credit_transactions
    WHERE organization_id = rsv.organization_id
      AND type = 'refund'
      AND reference_type = 'reservation.slack'
      AND reference_id = p_reservation_id::TEXT
    LIMIT 1;

    IF FOUND THEN
        IF existing_refund.amount <> p_refund_amount THEN
            RAISE EXCEPTION
                'settle_reservation_with_slack: retry amount % differs from prior refund %',
                p_refund_amount, existing_refund.amount;
        END IF;
        actual_refund := existing_refund.amount;
        final_balance := existing_refund.balance_after;
    ELSIF p_refund_amount > 0 THEN
        UPDATE user_credits
        SET credits_remaining = credits_remaining + p_refund_amount,
            updated_at = NOW()
        WHERE organization_id = rsv.organization_id
        RETURNING credits_remaining INTO final_balance;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'settle_reservation_with_slack: balance missing for organization %',
                rsv.organization_id;
        END IF;

        INSERT INTO credit_transactions (
            organization_id, user_id, type, amount, balance_after,
            reference_type, reference_id, description,
            tool_name, session_id
        ) VALUES (
            rsv.organization_id, rsv.user_id, 'refund', p_refund_amount, final_balance,
            'reservation.slack', p_reservation_id::TEXT,
            'Refund: reserved worst case, billed fewer rows',
            rsv.tool_name, rsv.session_id
        );
        actual_refund := p_refund_amount;
    END IF;

    IF p_refund_amount = 0 THEN
        SELECT credits_remaining INTO final_balance
        FROM user_credits
        WHERE organization_id = rsv.organization_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'settle_reservation_with_slack: balance missing for organization %',
                rsv.organization_id;
        END IF;
    END IF;

    UPDATE credit_transactions
    SET settlement_refund_amount = actual_refund,
        settlement_net_credits = rsv.amount - actual_refund,
        settlement_balance_after = final_balance
    WHERE id = p_reservation_id;

    RETURN jsonb_build_object(
        'net_credits', rsv.amount - actual_refund,
        'remaining', final_balance
    );
END;
$$;
