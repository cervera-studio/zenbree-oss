-- Each per-row charge names the hold that pays for it, so a hold's spend is the sum of its records, and the owner
-- and the reaper settle a hold by one rule (settle_hold). Until now the reaper settled only job holds, from
-- jobs.credits_spent, and released every other hold whole.

-- Null on a record that no hold pays: one moved from a 'refunded' cell_meta entry, or one whose job is gone or
-- took no hold. Set null, not cascaded, for the reason job_id is: a lost 'refunded' record would let an email batch
-- buy that address again. The key carries the organization (tenant rule R2, test_tenant_catalog_sql.py), and its
-- delete nulls hold_id only, so the record keeps its organization.
ALTER TABLE row_charges ADD COLUMN hold_id uuid;
ALTER TABLE row_charges ADD CONSTRAINT row_charges_hold_id_org_fkey FOREIGN KEY (hold_id, organization_id)
    REFERENCES credit_transactions (id, organization_id) ON DELETE SET NULL (hold_id);
CREATE INDEX row_charges_hold ON row_charges (hold_id);

-- The deploy that first applies this stops the workers before it (owner decision, 2026-10-13; docs/migrations.md),
-- so every reserved job hold belongs to a turn that will never close it. A job from before one growing hold could
-- take several holds, and its records name none of them: settle each by the rule the reaper used until now
-- (jobs.credits_spent, filled into the job's holds in the order it took them) before the records name a hold.
DO $$
DECLARE
    rsv  credit_transactions%ROWTYPE;
    used INT;
BEGIN
    FOR rsv IN
        SELECT * FROM credit_transactions
            WHERE status = 'reserved' AND type = 'reservation' AND reference_type = 'job'
            ORDER BY created_at, id
            FOR UPDATE
    LOOP
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
        IF COALESCE(used, 0) > 0 THEN
            PERFORM settle_reservation_with_slack(rsv.id, rsv.amount - used);
        ELSE
            PERFORM release_reservation(rsv.id);
        END IF;
    END LOOP;
END;
$$;

-- jobs.reservation_id is the job's first hold: before one growing hold it was never overwritten. It has no foreign
-- key, so only a hold that exists in the record's organization is named.
UPDATE row_charges c SET hold_id = h.id FROM jobs j, credit_transactions h
 WHERE j.id = c.job_id AND h.id = j.reservation_id AND h.organization_id = c.organization_id;

-- A pending cell now hides only while its hold is reserved, and only settle_hold makes a held charge final. A held
-- record with no reserved hold would stay held for good: it is final now, as the janitor would have made it.
UPDATE row_charges c SET state = 'charged', settled_at = now()
 WHERE c.state = 'held'
   AND NOT EXISTS (SELECT 1 FROM credit_transactions h WHERE h.id = c.hold_id AND h.status = 'reserved');

-- Closes a reserved hold from its records: its held charges become final (a live holder refunds its unfinished
-- rows before it calls this), and it keeps what they cost, or is released when they cost nothing. Returns that
-- spend, or NULL with nothing changed when the hold is no longer reserved, so a second call is a no-op.
-- Lock order: hold, row_charges (by id, as finalize and the turn's refund_unfinished take them), wallet.
CREATE OR REPLACE FUNCTION settle_hold(p_hold_id UUID)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    rsv   credit_transactions%ROWTYPE;
    spent INT;
BEGIN
    SELECT * INTO rsv FROM credit_transactions
        WHERE id = p_hold_id AND type = 'reservation' AND status = 'reserved'
        FOR UPDATE;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    UPDATE row_charges SET state = 'charged', settled_at = now()
        WHERE id IN (SELECT id FROM row_charges WHERE hold_id = p_hold_id AND state = 'held' ORDER BY id FOR UPDATE);

    -- Capped at the hold: a holder records only what its hold covers, and a refund past the amount would raise and
    -- stop the reaper for every hold.
    SELECT LEAST(rsv.amount, coalesce(sum(credits), 0)) INTO spent
        FROM row_charges WHERE hold_id = p_hold_id AND state <> 'refunded';

    IF spent > 0 THEN
        PERFORM settle_reservation_with_slack(p_hold_id, rsv.amount - spent);
    ELSE
        PERFORM release_reservation(p_hold_id);
    END IF;
    RETURN spent;
END;
$$;

-- #415, and every other holder: a holder killed before it closed its hold (hard time limit, deploy restart, OOM)
-- is closed by the rule that closes a live one.
CREATE OR REPLACE FUNCTION reap_stale_reservations_ids(p_older_than_minutes INT)
RETURNS SETOF UUID
LANGUAGE plpgsql
AS $$
DECLARE
    rid UUID;
BEGIN
    FOR rid IN
        SELECT id FROM credit_transactions
            WHERE status = 'reserved'
              AND type = 'reservation'
              AND created_at < NOW() - (p_older_than_minutes || ' minutes')::INTERVAL
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED
    LOOP
        IF settle_hold(rid) IS NOT NULL THEN
            RETURN NEXT rid;
        END IF;
    END LOOP;

    RETURN;
END;
$$;
