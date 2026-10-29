-- #699: English is the base language, and Spanish and French join Italian and English.
-- Existing rows keep the language they have. The default is for a row written without one: the web always
-- names it at sign-up, from the visitor's browser.
ALTER TABLE "user" DROP CONSTRAINT user_locale_check;
ALTER TABLE "user" ADD CONSTRAINT user_locale_check CHECK (locale IN ('en', 'it', 'es', 'fr'));
ALTER TABLE "user" ALTER COLUMN locale SET DEFAULT 'en';
