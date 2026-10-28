-- One Unipile account belongs to one connection (#317). Its webhooks route by the account id alone, so a
-- second connection of the same LinkedIn login let new_relation and status events write to two tenants.

-- No connect from the previous image lands a new duplicate between the UPDATE and the index.
LOCK TABLE user_oauth_connections IN SHARE ROW EXCLUSIVE MODE;

-- Rows that already share an id keep it on one connection: a working one before a broken one, then the
-- oldest, the one connect would have kept. The others lose it and show as broken, so their owner sees
-- LinkedIn needs a reconnect.
UPDATE user_oauth_connections c
SET provider_account_id = NULL,
    status = CASE WHEN c.status = 'disconnected' THEN c.status ELSE 'error' END,
    status_detail = CASE WHEN c.status = 'disconnected' THEN c.status_detail ELSE 'ACCOUNT_IN_USE_ELSEWHERE' END
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY provider, provider_account_id
        ORDER BY status <> 'connected', status = 'disconnected', connected_at NULLS LAST, created_at, id
    ) AS n
    FROM user_oauth_connections
    WHERE provider_account_id IS NOT NULL
) d
WHERE c.id = d.id AND d.n > 1;

DROP INDEX idx_user_oauth_connections_provider_account;
CREATE UNIQUE INDEX idx_user_oauth_connections_provider_account
    ON user_oauth_connections(provider, provider_account_id)
    WHERE provider_account_id IS NOT NULL;
