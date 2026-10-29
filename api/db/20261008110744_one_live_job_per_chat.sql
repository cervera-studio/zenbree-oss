-- #473: one live job per chat, as per user, so two members cannot run turns on one chat at once. A chat that
-- already has several keeps its newest; the older ones fail, as the stale check in _start_turn fails them.
UPDATE jobs j SET status = 'failed', error = 'superseded', finished_at = now(), updated_at = now()
    WHERE j.status IN ('queued', 'running')
      AND EXISTS (
          SELECT 1 FROM jobs n
              WHERE n.chat_session_id = j.chat_session_id
                AND n.status IN ('queued', 'running')
                AND (n.created_at, n.id) > (j.created_at, j.id)
      );

CREATE UNIQUE INDEX uq_jobs_one_active_per_chat ON jobs(chat_session_id) WHERE status IN ('queued', 'running');
