-- #341: a deleted list or AI column is hidden, not dropped, so the undo toast can restore it with its rows.
-- ponytail: nothing purges them; a janitor that hard-deletes rows older than N days if storage matters.
ALTER TABLE lists ADD COLUMN deleted_at timestamptz;
ALTER TABLE list_columns ADD COLUMN deleted_at timestamptz;
