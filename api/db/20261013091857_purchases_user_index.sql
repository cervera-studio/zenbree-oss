-- #692: the admin user detail and chats views, and the ON DELETE SET NULL of a user delete, find purchases by user_id.
CREATE INDEX idx_purchases_user ON purchases(user_id);
