-- home_touch fired on every UPDATE, so a backfill of a column the Home does not read sent one notification per row (#1112).
-- An UPDATE now notifies only when a column that app/home/overview.py, nav.py or the approval counts read changes.
-- INSERT and DELETE are unchanged. The old and the new row still both notify (a row that moves refreshes both Homes),
-- so each list keeps the column the topic comes from.

DROP TRIGGER home_touch ON chat_sessions;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch_update AFTER UPDATE ON chat_sessions FOR EACH ROW
    WHEN ((OLD.project_id, OLD.organization_id, OLD.title, OLD.pinned_at, OLD.updated_at)
          IS DISTINCT FROM (NEW.project_id, NEW.organization_id, NEW.title, NEW.pinned_at, NEW.updated_at))
    EXECUTE FUNCTION home_touch('project');

DROP TRIGGER home_touch ON lists;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON lists
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch_update AFTER UPDATE ON lists FOR EACH ROW
    WHEN ((OLD.project_id, OLD.organization_id, OLD.chat_session_id, OLD.user_id, OLD.name, OLD.entity, OLD.mode, OLD.deleted_at)
          IS DISTINCT FROM (NEW.project_id, NEW.organization_id, NEW.chat_session_id, NEW.user_id, NEW.name, NEW.entity, NEW.mode, NEW.deleted_at))
    EXECUTE FUNCTION home_touch('project');

DROP TRIGGER home_touch ON list_rows;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON list_rows
    FOR EACH ROW EXECUTE FUNCTION home_touch('list');
CREATE TRIGGER home_touch_update AFTER UPDATE ON list_rows FOR EACH ROW
    WHEN ((OLD.list_id, OLD.excluded, coalesce(OLD.cells->>'email', '') <> '', OLD.cells->>'email_status')
          IS DISTINCT FROM (NEW.list_id, NEW.excluded, coalesce(NEW.cells->>'email', '') <> '', NEW.cells->>'email_status'))
    EXECUTE FUNCTION home_touch('list');

DROP TRIGGER home_touch ON outreach_campaigns;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON outreach_campaigns
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch_update AFTER UPDATE ON outreach_campaigns FOR EACH ROW
    WHEN ((OLD.project_id, OLD.organization_id, OLD.owner_id, OLD.list_id, OLD.name, OLD.status, OLD.steps, OLD.activated_at)
          IS DISTINCT FROM (NEW.project_id, NEW.organization_id, NEW.owner_id, NEW.list_id, NEW.name, NEW.status, NEW.steps, NEW.activated_at))
    EXECUTE FUNCTION home_touch('project');

DROP TRIGGER home_touch ON outreach_campaign_sends;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON outreach_campaign_sends
    FOR EACH ROW EXECUTE FUNCTION home_touch('campaign');
CREATE TRIGGER home_touch_update AFTER UPDATE ON outreach_campaign_sends FOR EACH ROW
    WHEN ((OLD.campaign_id, OLD.target_id, OLD.owner_id, OLD.step, OLD.channel, OLD.status, OLD.attempts, OLD.bounce_kind, OLD.delivered_at)
          IS DISTINCT FROM (NEW.campaign_id, NEW.target_id, NEW.owner_id, NEW.step, NEW.channel, NEW.status, NEW.attempts, NEW.bounce_kind, NEW.delivered_at))
    EXECUTE FUNCTION home_touch('campaign');

DROP TRIGGER home_touch ON outreach_campaign_targets;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON outreach_campaign_targets
    FOR EACH ROW EXECUTE FUNCTION home_touch('campaign');
CREATE TRIGGER home_touch_update AFTER UPDATE ON outreach_campaign_targets FOR EACH ROW
    WHEN ((OLD.campaign_id, OLD.owner_id, OLD.email, OLD.linkedin_alias, OLD.status, OLD.next_step, OLD.replied_at)
          IS DISTINCT FROM (NEW.campaign_id, NEW.owner_id, NEW.email, NEW.linkedin_alias, NEW.status, NEW.next_step, NEW.replied_at))
    EXECUTE FUNCTION home_touch('campaign');

DROP TRIGGER home_touch ON notifications;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON notifications
    FOR EACH ROW EXECUTE FUNCTION home_touch('reply');
CREATE TRIGGER home_touch_update AFTER UPDATE ON notifications FOR EACH ROW
    WHEN ((OLD.user_id, OLD.organization_id, OLD.kind, OLD.read_at, OLD.data)
          IS DISTINCT FROM (NEW.user_id, NEW.organization_id, NEW.kind, NEW.read_at, NEW.data))
    EXECUTE FUNCTION home_touch('reply');

DROP TRIGGER home_touch ON member;
CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON member
    FOR EACH ROW EXECUTE FUNCTION home_touch('member');
CREATE TRIGGER home_touch_update AFTER UPDATE ON member FOR EACH ROW
    WHEN ((OLD."userId", OLD."organizationId")
          IS DISTINCT FROM (NEW."userId", NEW."organizationId"))
    EXECUTE FUNCTION home_touch('member');
