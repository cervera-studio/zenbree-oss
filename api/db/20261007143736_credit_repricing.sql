-- Credit repricing (2026-10-07): 1 credit = 1 email, ~€0.05 (was ~€0.015). Balances keep their euro value
-- (× 0.3, rounded up so nobody loses a credit to rounding) and a new wallet opens with 25 credits.
-- ponytail: the ledger history and any hold still open stay in old units; prod had none open at the switch.
ALTER TABLE user_credits ALTER COLUMN credits_remaining SET DEFAULT 25;
UPDATE user_credits SET credits_remaining = ceil(credits_remaining * 0.3), updated_at = now()
WHERE credits_remaining > 0;
