-- The two rules on deleting an account (#341), held by the database too: the web asks the API first
-- (app/auth/account_delete.py: the refusal the user reads, and the Stripe cancel), and this trigger is the guarantee
-- when anything deletes a "user" row without that step.
-- 1. The last admin of an organization that has other members is never deleted.
-- 2. A user who is the only member of an organization still billing (a live subscription, statuses as LIVE_STATUSES
--    in app/billing/routes.py, or auto top-up on) is not deleted until that billing is stopped.
CREATE FUNCTION guard_account_delete() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM member m
        WHERE m."userId" = OLD.id AND m.role = 'admin'
          AND EXISTS (SELECT 1 FROM member x WHERE x."organizationId" = m."organizationId" AND x."userId" <> OLD.id)
          AND NOT EXISTS (SELECT 1 FROM member x WHERE x."organizationId" = m."organizationId"
                          AND x."userId" <> OLD.id AND x.role = 'admin')
    ) THEN
        RAISE EXCEPTION 'ACCOUNT.LAST_ADMIN: user % is the last admin of an organization with other members', OLD.id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM member m
        WHERE m."userId" = OLD.id
          AND NOT EXISTS (SELECT 1 FROM member x WHERE x."organizationId" = m."organizationId" AND x."userId" <> OLD.id)
          AND (EXISTS (SELECT 1 FROM subscriptions s WHERE s.organization_id = m."organizationId"
                       AND s.status IN ('active', 'trialing', 'past_due', 'unpaid', 'incomplete'))
               OR EXISTS (SELECT 1 FROM auto_topups a WHERE a.organization_id = m."organizationId" AND a.enabled))
    ) THEN
        RAISE EXCEPTION 'ACCOUNT.STILL_BILLING: user % is the only member of an organization still billing', OLD.id;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER guard_account_delete BEFORE DELETE ON "user"
    FOR EACH ROW EXECUTE FUNCTION guard_account_delete();
