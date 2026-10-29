-- The approval counts leave out a held message (#931), so the tables the hold reads notify too. A stop's attempts and
-- a lead's sync columns are rewritten often and change no count, so those updates do not notify.
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON outreach_stops
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch_update AFTER UPDATE ON outreach_stops FOR EACH ROW
    WHEN ((OLD.project_id, OLD.email, OLD.status) IS DISTINCT FROM (NEW.project_id, NEW.email, NEW.status))
    EXECUTE FUNCTION home_touch('project');

CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON instantly_leads
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch_update AFTER UPDATE ON instantly_leads FOR EACH ROW
    WHEN ((OLD.project_id, OLD.email, OLD.outcome) IS DISTINCT FROM (NEW.project_id, NEW.email, NEW.outcome))
    EXECUTE FUNCTION home_touch('project');

CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON audience_aliases
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch_update AFTER UPDATE ON audience_aliases FOR EACH ROW
    WHEN ((OLD.project_id, OLD.identity_id, OLD.value) IS DISTINCT FROM (NEW.project_id, NEW.identity_id, NEW.value))
    EXECUTE FUNCTION home_touch('project');
