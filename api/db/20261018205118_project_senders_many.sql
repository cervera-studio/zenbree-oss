-- A client keeps one LinkedIn account: the LinkedIn tools act with the client's one account.
--
-- Rollback floor: the previous image links with ON CONFLICT (project_id, provider) and no WHERE. Postgres cannot infer
-- the partial index below from that, so on this schema every connect and link of that image fails with a 500.
ALTER TABLE project_senders DROP CONSTRAINT project_senders_project_provider_key;
CREATE UNIQUE INDEX project_senders_single_provider_key ON project_senders (project_id, provider)
    WHERE provider NOT IN ('google', 'microsoft');
