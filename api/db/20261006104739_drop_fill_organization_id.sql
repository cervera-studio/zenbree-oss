-- #308: users can belong to several organizations, so a row that names none can no longer take its creator's
-- one. Every insert of the app names organization_id (the caller's active organization, or the parent chat's
-- or list's), and the previous image (#305) does too, so the fill triggers of 20261006080037_organizations.sql
-- go: an insert that forgets it now fails on NOT NULL instead of landing in the creator's default organization.
-- The function stays: #306's credit tables use it until #306 drops their triggers too.
DROP TRIGGER fill_organization_id ON projects;
DROP TRIGGER fill_organization_id ON chat_sessions;
DROP TRIGGER fill_organization_id ON lists;
DROP TRIGGER fill_organization_id ON outreach_campaigns;
DROP TRIGGER fill_organization_id ON user_api_keys;
