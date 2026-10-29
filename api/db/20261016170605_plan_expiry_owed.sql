-- #962: credits held by an open hold at a renewal are not spent yet, and spending uses the allowance first, so
-- the renewal expires them first and records that per hold (plan_period.expire_plan_credits): ``held`` is what the
-- hold held then, ``owed`` the part of it that is expired allowance. When the hold returns credits, they pay that
-- debt, less what the hold spent of its renewal amount (credits it grew afterwards came from the new wallet).
CREATE TABLE plan_expiry_owed (
    hold_id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    held int NOT NULL CHECK (held > 0),
    owed int NOT NULL CHECK (owed >= 0 AND owed <= held),
    CONSTRAINT plan_expiry_owed_hold_id_org_fkey FOREIGN KEY (hold_id, organization_id)
        REFERENCES credit_transactions (id, organization_id) ON DELETE CASCADE
);
CREATE INDEX idx_plan_expiry_owed_organization_id ON plan_expiry_owed(organization_id);

-- STRIPE_WEBHOOK records the debt at a renewal (zenbree_system reads a new table and writes nothing).
GRANT INSERT, UPDATE ON plan_expiry_owed TO zenbree_system;

CREATE FUNCTION pay_plan_expiry_owed() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    uuid_pattern CONSTANT text := '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
    hold         uuid;
    owed_amount  int;
    held_amount  int;
    pay          int;
    new_balance  int;
BEGIN
    IF NEW.reference_type = 'reservation.slack' THEN
        hold := substring(NEW.reference_id FROM '^(' || uuid_pattern || ')$')::uuid;
    ELSE
        hold := substring(NEW.description FROM '^Released reservation (' || uuid_pattern || ')$')::uuid;
    END IF;
    IF hold IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT owed, held INTO owed_amount, held_amount FROM plan_expiry_owed
        WHERE hold_id = hold AND organization_id = NEW.organization_id;
    IF NOT FOUND OR owed_amount = 0 THEN
        RETURN NULL;
    END IF;

    -- What the hold spent of its renewal amount used up the expired allowance first.
    pay := GREATEST(0, owed_amount - GREATEST(0, held_amount - NEW.amount));
    IF pay > 0 THEN
        UPDATE user_credits SET credits_remaining = credits_remaining - pay, updated_at = now()
            WHERE organization_id = NEW.organization_id
            RETURNING credits_remaining INTO new_balance;
        INSERT INTO credit_transactions (organization_id, user_id, type, amount, balance_after, reference_type, reference_id, description)
            VALUES (NEW.organization_id, NEW.user_id, 'deduction', pay, new_balance, 'plan_expiry', hold::text,
                    'Unused plan credits expired when their hold returned');
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER pay_plan_expiry_owed AFTER INSERT ON credit_transactions
    FOR EACH ROW WHEN (NEW.type = 'refund') EXECUTE FUNCTION pay_plan_expiry_owed();
