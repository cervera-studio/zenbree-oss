-- A run owns its batch while it holds this token: a worker that slept past its heartbeat cannot end, or keep alive,
-- the run that a retry started after it (#929).
ALTER TABLE instantly_enrollments ADD COLUMN run_token uuid;
