-- #346: an out-of-office reply pauses the person until the day after they are back, and a «Non ora» reply keeps
-- a reminder that brings the thread back to «Da leggere» on the day the person asked for.

-- When a paused person's sequence restarts (the dispatcher beat sets them `active` again). Set exactly while the
-- person is `paused`. Nothing set `paused` before this migration; a row that did anyway restarts on the next beat.
ALTER TABLE outreach_campaign_targets ADD COLUMN paused_until TIMESTAMPTZ;
UPDATE outreach_campaign_targets SET paused_until = now() WHERE status = 'paused';
ALTER TABLE outreach_campaign_targets ADD CONSTRAINT outreach_campaign_targets_paused_until_check
    CHECK ((status = 'paused') = (paused_until IS NOT NULL));
CREATE INDEX outreach_campaign_targets_paused_until_idx ON outreach_campaign_targets(paused_until)
    WHERE status = 'paused';

-- One per reply labelled `not_now`, by the model or by hand; relabelling it away deletes the reminder. The owner
-- and the project are the message's. surfaced_at: when the beat marked the message unread again, once.
CREATE TABLE outreach_reminders (
    message_id UUID PRIMARY KEY REFERENCES outreach_inbound_messages(id) ON DELETE CASCADE,
    remind_on DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    surfaced_at TIMESTAMPTZ
);
CREATE INDEX outreach_reminders_due_idx ON outreach_reminders(remind_on) WHERE surfaced_at IS NULL;
