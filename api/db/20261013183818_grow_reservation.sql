-- One hold per holder run: a run that needs more grows its hold instead of taking a second one, so the hold id
-- names the run. Grows a 'reserved' hold by p_amount from the wallet, under the project lock and the hard cap
-- check of reserve_project_credits. Returns the new amount, or NULL with nothing changed when the hold is no
-- longer reserved, the wallet is short or the cap room is.
-- Lock order: project, hold, wallet. reserve takes project then wallet; settle, release and the reaper take
-- hold then wallet; none takes them the other way round.
CREATE FUNCTION grow_reservation(p_hold_id UUID, p_amount INT)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    pid       UUID;
    cap       INT;
    cap_mode  TEXT;
    rsv       credit_transactions%ROWTYPE;
    month     TIMESTAMPTZ := date_trunc('month', NOW());
    carried   INT;
    wallet    INT;
    grown     INT;
BEGIN
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'grow_reservation: amount % is not positive', p_amount;
    END IF;

    SELECT project_id INTO pid FROM credit_transactions WHERE id = p_hold_id;
    IF pid IS NOT NULL THEN
        SELECT credit_cap, credit_cap_mode INTO cap, cap_mode FROM projects WHERE id = pid FOR UPDATE;
    END IF;

    SELECT * INTO rsv FROM credit_transactions
        WHERE id = p_hold_id AND type = 'reservation' AND status = 'reserved'
        FOR UPDATE;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    -- project_month_spend counts a hold in the month it was taken. A hold from an earlier month moves into this
    -- one when it grows (created_at below), so this month's cap must also have room for what it already holds.
    carried := CASE WHEN rsv.created_at < month THEN rsv.amount ELSE 0 END;
    IF cap_mode = 'hard' AND project_month_spend(pid) + carried + p_amount > cap THEN
        RETURN NULL;
    END IF;

    UPDATE user_credits
        SET credits_remaining = credits_remaining - p_amount,
            updated_at = NOW()
        WHERE organization_id = rsv.organization_id
          AND credits_remaining >= p_amount
        RETURNING credits_remaining INTO wallet;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    -- The ledger line reads as one hold of the final amount, with the wallet as it is after this growth: other
    -- holds and top-ups may have moved it since the take.
    UPDATE credit_transactions
        SET amount = amount + p_amount,
            balance_after = wallet,
            created_at = greatest(created_at, month)
        WHERE id = p_hold_id
        RETURNING amount INTO grown;

    RETURN grown;
END;
$$;
