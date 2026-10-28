-- Where an exclusion came from, for the settings table. `source` stays the profile lock (IS NOT NULL = the profile
-- owns it and may delete it), so the origin is its own column.
ALTER TABLE exclusions ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'
    CHECK (origin IN ('profile', 'unsubscribed', 'own_domain', 'manual'));
-- A link unsubscribe from before this column cannot be told from a manual add; it reads as manual.
UPDATE exclusions SET origin = CASE WHEN source IS NOT NULL THEN 'profile' ELSE 'unsubscribed' END
WHERE source IS NOT NULL OR reply_opt_out_at IS NOT NULL;

-- Last 4 characters of the key, to recognise it in the list. Keys issued before this have none.
ALTER TABLE user_api_keys ADD COLUMN last4 TEXT CHECK (char_length(last4) = 4);
