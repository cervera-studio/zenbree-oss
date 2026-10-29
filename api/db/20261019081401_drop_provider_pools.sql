-- A campaign sends from the accounts in outreach_campaign_senders, and a conversation keeps the account in
-- outreach_campaign_targets.sender_id (#935). The provider names below only mirrored them, and no code reads them.
-- outreach_campaign_sends.sender_provider and outreach_inbox_replies.sender_provider stay: they record what sent.
--
-- Rollback floor: the previous image selects outreach_campaigns.sender_pool for every campaign read and writes
-- outreach_campaign_targets.sender_provider when it claims a first email, so on this schema both fail.
ALTER TABLE outreach_campaigns DROP COLUMN sender_pool;
ALTER TABLE outreach_campaign_targets DROP COLUMN sender_provider;
