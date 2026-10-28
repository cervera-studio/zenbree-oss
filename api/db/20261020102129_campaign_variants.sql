-- A/B copy variants (#934). A campaign tests while some step carries `b`, which lives in `steps`. b_percent is B's
-- share of the people who join; allocation_paused_at set: people who join from then on get copy A, outside the test.
ALTER TABLE outreach_campaigns
    ADD COLUMN b_percent smallint NOT NULL DEFAULT 50
        CHECK (b_percent BETWEEN 10 AND 90 AND b_percent % 10 = 0),
    ADD COLUMN allocation_paused_at timestamptz;

-- NULL: the campaign has no test, or the person joined while allocation was paused. Either way, copy A.
ALTER TABLE outreach_campaign_targets
    ADD COLUMN variant text CHECK (variant IN ('a', 'b'));

-- app.outreach.freeze counts the split of every join; a campaign with no test has no entry to read.
CREATE INDEX outreach_campaign_targets_variant ON outreach_campaign_targets (campaign_id, variant)
    WHERE variant IS NOT NULL;

-- A person keeps the copy they joined with: the variant is set once, by the freeze that activates them.
-- An INSERT is not checked: seed SQL and tests insert people with a variant.
CREATE FUNCTION outreach_target_variant_is_final() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.variant IS NULL AND OLD.status = 'draft' AND NEW.status = 'active'
        AND current_setting('zenbree.freezing', true) = 'on' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'outreach target %: its copy variant % is final, not %', OLD.id, OLD.variant, NEW.variant
        USING ERRCODE = 'integrity_constraint_violation';
END
$$;
CREATE TRIGGER outreach_target_variant_is_final BEFORE UPDATE OF variant ON outreach_campaign_targets
    FOR EACH ROW WHEN (OLD.variant IS DISTINCT FROM NEW.variant)
    EXECUTE FUNCTION outreach_target_variant_is_final();
