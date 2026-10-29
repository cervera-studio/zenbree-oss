-- The send window moves from one global setting to each mailbox (#348). The defaults are the values the global
-- setting had (09:00-18:00, Monday to Friday), so every existing mailbox keeps sending when it did.
ALTER TABLE user_oauth_connections
    ADD COLUMN send_window_start_hour SMALLINT NOT NULL DEFAULT 9,
    ADD COLUMN send_window_end_hour SMALLINT NOT NULL DEFAULT 18,
    ADD COLUMN send_window_days SMALLINT[] NOT NULL DEFAULT ARRAY[0, 1, 2, 3, 4]::SMALLINT[],
    ADD CONSTRAINT user_oauth_connections_send_window_hours
        CHECK (send_window_start_hour >= 0 AND send_window_start_hour < send_window_end_hour
               AND send_window_end_hour <= 24),
    ADD CONSTRAINT user_oauth_connections_send_window_days
        CHECK (cardinality(send_window_days) BETWEEN 1 AND 7
               AND send_window_days <@ ARRAY[0, 1, 2, 3, 4, 5, 6]::SMALLINT[]
               AND array_position(send_window_days, NULL) IS NULL);
