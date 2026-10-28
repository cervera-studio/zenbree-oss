-- E13.2 (#528): the sends and replies Zenbree logged on a HubSpot contact's timeline. One row per send or inbound
-- message: the sweep in app.crm.activities takes each once. The row is written before HubSpot is called
-- (activity_id NULL), for two reasons. The read gate must already know the touch is Zenbree's when HubSpot moves
-- the contact's "Last contacted" to it, or the person's next step would be held for the cooldown. And a call whose
-- answer was lost is checked against the contact before it is repeated, so the timeline does not show a send twice.
CREATE TABLE crm_activity_writes (
    kind         TEXT NOT NULL CHECK (kind IN ('send', 'reply')),
    source_id    UUID NOT NULL,  -- outreach_campaign_sends.id or outreach_inbound_messages.id; outlives that row
    owner_id     UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    email        TEXT NOT NULL CHECK (email = lower(btrim(email)) AND email <> ''),
    touched_at   TIMESTAMPTZ NOT NULL,  -- the activity's time in HubSpot: when the message was delivered or received
    activity_id  TEXT,                  -- NULL until HubSpot has it; '' when it has it and the id was lost
    attempts     SMALLINT NOT NULL DEFAULT 0,  -- refusals of the activity itself; the sweep gives up after a few
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, source_id)
);
CREATE INDEX crm_activity_writes_person_idx ON crm_activity_writes (owner_id, email, touched_at DESC);
