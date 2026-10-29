ALTER TABLE outreach_campaign_targets
    ADD COLUMN personalization JSONB CHECK (jsonb_typeof(personalization) = 'object'),
    ADD COLUMN frozen_at TIMESTAMPTZ;

-- An INSERT is not checked: seed SQL and tests insert active people.
CREATE FUNCTION outreach_target_activates_by_freeze() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('zenbree.freezing', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'outreach target %: a draft person becomes active only through app.outreach.freeze.freeze',
            OLD.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER outreach_target_activates_by_freeze BEFORE UPDATE OF status ON outreach_campaign_targets
    FOR EACH ROW WHEN (OLD.status = 'draft' AND NEW.status = 'active')
    EXECUTE FUNCTION outreach_target_activates_by_freeze();
