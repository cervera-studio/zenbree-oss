-- E13.4 (#123): a campaign step that creates a HubSpot task instead of sending. Its claim is a row like any
-- other step's; provider_receipt_id holds the task's id.
ALTER TABLE outreach_campaign_sends DROP CONSTRAINT outreach_campaign_sends_channel_check;
ALTER TABLE outreach_campaign_sends ADD CONSTRAINT outreach_campaign_sends_channel_check
    CHECK (channel IN ('email', 'linkedin_connect', 'linkedin_message', 'hubspot_task'));
