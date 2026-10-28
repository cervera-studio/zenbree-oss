-- #747: delivery-status notifications (DSN) read from the sending mailbox mark the send they answer.
ALTER TABLE outreach_campaign_sends
    ADD COLUMN bounced_at timestamptz,
    ADD COLUMN bounce_kind text CHECK (bounce_kind IN ('hard', 'soft')),
    -- the DSN's RFC 3463 status, e.g. 5.1.1
    ADD COLUMN bounce_status text CHECK (char_length(bounce_status) <= 20),
    ADD CONSTRAINT outreach_campaign_sends_bounce_check CHECK ((bounce_kind IS NULL) = (bounced_at IS NULL));

-- Gmail's push names a history id: history.list reads from the last one this mailbox read.
ALTER TABLE user_oauth_connections ADD COLUMN inbound_history_id text CHECK (char_length(inbound_history_id) <= 40);

-- A hard bounce excludes the address: origin 'bounced', which 20261017125629_instantly_votes already allows.
