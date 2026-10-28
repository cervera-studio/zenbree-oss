-- #538: in flexible billing mode a portal cancellation sets cancel_at and leaves cancel_at_period_end false.
-- Stripe sets cancel_at for a cancellation at period end too, so cancel_at alone says when a plan ends.
ALTER TABLE subscriptions ADD COLUMN cancel_at TIMESTAMPTZ;
UPDATE subscriptions SET cancel_at = current_period_end WHERE cancel_at_period_end;
