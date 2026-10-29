-- When a reply-driven opt-out (#75) added an exclusion. The daily cap on them per client counts these rows: an opt-out
-- to a deleted member's old address that matched nobody (#333) stores no inbound message, so counting messages let a
-- forged flood exclude unlimited addresses. NULL: added any other way (manual, CSV, link, onboarding).
ALTER TABLE exclusions ADD COLUMN reply_opt_out_at TIMESTAMPTZ;
CREATE INDEX idx_exclusions_reply_opt_out ON exclusions(project_id, reply_opt_out_at) WHERE reply_opt_out_at IS NOT NULL;
