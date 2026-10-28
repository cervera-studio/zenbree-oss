-- pg_notify is delivered on commit, dropped on rollback, and identical payloads in one transaction are delivered
-- once, so row-level triggers still give one notification per transaction and topic.
-- The cost: a transaction that queued a notification takes Postgres's notify-queue lock at commit, so concurrent
-- writers to these tables commit one at a time. Accepted at today's scale; past it, move the fan-out off the commit
-- path.

-- NULL when a cascade already removed the parent: the parent's own trigger covers that row.
CREATE FUNCTION home_topic(scope text, key text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT CASE scope
        WHEN 'project'  THEN 'p:' || key
        WHEN 'session'  THEN 'p:' || (SELECT project_id::text FROM chat_sessions WHERE id = key::uuid)
        WHEN 'list'     THEN 'p:' || (SELECT project_id::text FROM lists WHERE id = key::uuid)
        WHEN 'campaign' THEN 'p:' || (SELECT project_id::text FROM outreach_campaigns WHERE id = key::uuid)
        WHEN 'reply'    THEN 'u:' || key
        WHEN 'member'   THEN 'u:' || key
    END
$$;

-- Each branch reads one key column, never the whole row: list_rows.cells can be large. Both the old and the new
-- row notify, so a row that moves between projects refreshes both Homes.
CREATE FUNCTION home_touch() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_key text;
    new_key text;
BEGIN
    CASE TG_ARGV[0]
        WHEN 'project' THEN old_key := OLD.project_id; new_key := NEW.project_id;
        WHEN 'session' THEN old_key := OLD.chat_session_id; new_key := NEW.chat_session_id;
        WHEN 'list' THEN old_key := OLD.list_id; new_key := NEW.list_id;
        WHEN 'campaign' THEN old_key := OLD.campaign_id; new_key := NEW.campaign_id;
        WHEN 'reply' THEN
            IF OLD.kind IN ('reply', 'reply_urgent') THEN old_key := OLD.user_id; END IF;
            IF NEW.kind IN ('reply', 'reply_urgent') THEN new_key := NEW.user_id; END IF;
        WHEN 'member' THEN old_key := OLD."userId"; new_key := NEW."userId";
    END CASE;
    PERFORM pg_notify('home', t)
       FROM (VALUES (home_topic(TG_ARGV[0], old_key)), (home_topic(TG_ARGV[0], new_key))) v(t)
      WHERE t IS NOT NULL;
    RETURN NULL;
END
$$;

CREATE TRIGGER home_touch AFTER INSERT OR DELETE ON jobs FOR EACH ROW EXECUTE FUNCTION home_touch('session');
-- Only what the overview shows: a credit reservation or a phase change rewrites the row several times per tool
-- call and must not recompute every open Home.
-- ponytail: a stale job revived by a phase-only write reappears in `running` on its next stage, not at once.
CREATE TRIGGER home_touch_update AFTER UPDATE ON jobs FOR EACH ROW
    WHEN ((OLD.status, OLD.stages, OLD.progress_done, OLD.progress_total, OLD.list_id, OLD.chat_session_id)
          IS DISTINCT FROM (NEW.status, NEW.stages, NEW.progress_done, NEW.progress_total, NEW.list_id, NEW.chat_session_id))
    EXECUTE FUNCTION home_touch('session');

CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON lists
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON list_rows
    FOR EACH ROW EXECUTE FUNCTION home_touch('list');
CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON outreach_campaigns
    FOR EACH ROW EXECUTE FUNCTION home_touch('project');
CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON outreach_campaign_sends
    FOR EACH ROW EXECUTE FUNCTION home_touch('campaign');
CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON outreach_campaign_targets
    FOR EACH ROW EXECUTE FUNCTION home_touch('campaign');
CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON notifications
    FOR EACH ROW EXECUTE FUNCTION home_touch('reply');
CREATE TRIGGER home_touch AFTER INSERT OR UPDATE OR DELETE ON member
    FOR EACH ROW EXECUTE FUNCTION home_touch('member');
