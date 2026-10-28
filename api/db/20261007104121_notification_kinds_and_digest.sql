-- #343: four new kinds, and the digest's frequency and hour replace the on/off switch.
-- Rollback floor: the previous image reads notification_settings.digest, which this drops.

ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check CHECK (kind IN (
    'reply_urgent', 'reply', 'search_done', 'low_credits', 'payment_failed',
    'approval_needed', 'search_failed', 'agent_question',
    'linkedin_accepted', 'bounces_high', 'mailbox_error', 'plan_renewed'
));

ALTER TABLE notification_settings
    ADD COLUMN digest_frequency TEXT NOT NULL DEFAULT 'daily' CHECK (digest_frequency IN ('daily', 'weekly', 'off')),
    ADD COLUMN digest_hour SMALLINT NOT NULL DEFAULT 9 CHECK (digest_hour BETWEEN 0 AND 23);

-- A user who turned the digest off keeps it off; the rest keep today's schedule (every working day at 9).
UPDATE notification_settings SET digest_frequency = 'off' WHERE NOT digest;
ALTER TABLE notification_settings DROP COLUMN digest;
