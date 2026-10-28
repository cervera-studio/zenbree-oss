-- The campaign report (#942) reads a project's sends, replies and Instantly facts by time range: each index makes
-- that read a range scan of the window instead of every row the campaign or the project ever had.
CREATE INDEX outreach_campaign_sends_delivered_idx ON outreach_campaign_sends (campaign_id, delivered_at)
    WHERE status = 'sent';
CREATE INDEX outreach_inbound_messages_campaign_reply_idx ON outreach_inbound_messages (campaign_id, received_at)
    WHERE kind = 'reply';
CREATE INDEX instantly_events_project_time_idx ON instantly_events (project_id, occurred_at);
-- A send or reply with no target is a person known only by address, which the report groups in one pass over these
-- rows of the campaign. The address is not a key: under row-level security lower(btrim(...)) is never an index
-- condition, because neither function is leakproof.
CREATE INDEX outreach_campaign_sends_no_target_idx ON outreach_campaign_sends (campaign_id, delivered_at)
    WHERE status = 'sent' AND target_id IS NULL;
CREATE INDEX outreach_inbound_messages_no_target_reply_idx ON outreach_inbound_messages (campaign_id, received_at)
    WHERE kind = 'reply' AND target_id IS NULL;
