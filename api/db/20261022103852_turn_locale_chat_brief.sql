-- The language the user saw when they sent the turn (POST /v1/chat `locale`). The turn writes in it
-- and never saves it on the user (#795). NULL is the user's saved language. The agent reads it against
-- app.core.locale, so no check here lists the languages a second time.
ALTER TABLE jobs ADD COLUMN locale text;

-- The brief a chat opened with when its first message shows only a summary (onboarding's first
-- list). The agent reads it in place of that message.
ALTER TABLE chat_sessions ADD COLUMN brief text;
