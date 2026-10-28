CREATE INDEX outreach_stops_due_idx ON outreach_stops (connection_id, next_attempt_at) WHERE status = 'pending';
