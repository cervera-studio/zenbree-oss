-- The back office's users list shows when each user was last active: the newest session they opened themselves
-- (app/admin/routes.py, users_overview). It runs as zenbree_system, which gets nothing on the five secret tables
-- (20261013195148_row_level_security.sql), so it gets the three columns that read needs and no more: never the
-- session token, its IP or its user agent. zenbree_app gets nothing: no member route reads sessions.
GRANT SELECT ("userId", "impersonatedBy", "updatedAt") ON session TO zenbree_system;
