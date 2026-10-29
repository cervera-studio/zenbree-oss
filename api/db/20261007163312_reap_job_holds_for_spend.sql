-- #415: a job killed before it settled (hard time limit, deploy restart, OOM) spent what jobs.credits_spent
-- records. Its stale holds keep that spend, filled in the order they were taken, and refund the rest. Any other
-- stale hold is still released whole.
CREATE OR REPLACE FUNCTION reap_stale_reservations_ids(p_older_than_minutes INT)
RETURNS SETOF UUID
LANGUAGE plpgsql
AS $$
DECLARE
    rsv  credit_transactions%ROWTYPE;
    used INT;
BEGIN
    FOR rsv IN
        SELECT * FROM credit_transactions
            WHERE status = 'reserved'
              AND type = 'reservation'
              AND created_at < NOW() - (p_older_than_minutes || ' minutes')::INTERVAL
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED
    LOOP
        used := 0;
        IF rsv.reference_type = 'job' THEN
            SELECT LEAST(rsv.amount, GREATEST(0, j.credits_spent - COALESCE((
                       SELECT sum(h.amount) FROM credit_transactions h
                           WHERE h.organization_id = rsv.organization_id
                             AND h.reference_type = 'job'
                             AND h.reference_id = rsv.reference_id
                             AND h.type IN ('reservation', 'deduction')
                             AND (h.created_at, h.id) < (rsv.created_at, rsv.id)
                   ), 0)))
                INTO used
                FROM jobs j WHERE j.id = rsv.reference_id::UUID;
        END IF;

        IF COALESCE(used, 0) > 0 THEN
            IF settle_reservation_with_slack(rsv.id, rsv.amount - used) IS NOT NULL THEN
                RETURN NEXT rsv.id;
            END IF;
        ELSIF release_reservation(rsv.id) THEN
            RETURN NEXT rsv.id;
        END IF;
    END LOOP;

    RETURN;
END;
$$;
