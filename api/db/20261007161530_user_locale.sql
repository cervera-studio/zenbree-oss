-- #377: the language of the user's service emails. The web writes it at sign-up (a Better Auth additional field)
-- and the bell stream keeps it in step with the language the app shows.
ALTER TABLE "user" ADD COLUMN locale text NOT NULL DEFAULT 'it' CHECK (locale IN ('it', 'en'));
