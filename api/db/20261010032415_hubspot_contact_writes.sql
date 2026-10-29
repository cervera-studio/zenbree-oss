-- E13.2 (#527): what Zenbree wrote in a user's HubSpot. One row per person and connected user, once their
-- contact (and its company, when one could be matched or created) is there: the sweep in app.crm.contacts
-- takes a person again only while they have no row, so a retry or a second campaign writes nothing twice.
CREATE TABLE crm_contact_writes (
    owner_id   UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    email      TEXT NOT NULL CHECK (email = lower(btrim(email)) AND email <> ''),
    contact_id TEXT NOT NULL,
    company_id TEXT,
    created    BOOLEAN NOT NULL,  -- Zenbree created the contact; false when it was already in HubSpot
    written_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, email)
);

-- Since when write-back is on. Only people written to after this moment get a contact: turning the switch on
-- does not copy the history of earlier campaigns into the CRM.
ALTER TABLE user_oauth_connections ADD COLUMN crm_write_back_since TIMESTAMPTZ;
UPDATE user_oauth_connections SET crm_write_back_since = now() WHERE crm_write_back;
