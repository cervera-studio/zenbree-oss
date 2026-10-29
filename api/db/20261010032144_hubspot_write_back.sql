-- E13.2 (#526): the user's choice to let Zenbree write in their HubSpot. Off for every existing connection:
-- none was asked. It only counts while the grant carries the write scopes (app.crm.write_back).
ALTER TABLE user_oauth_connections ADD COLUMN crm_write_back BOOLEAN NOT NULL DEFAULT false;
