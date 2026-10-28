-- #931: a native reply or opt-out queues the Instantly stops of the person (app/outreach/coordination.py).

-- True when the lead's own Instantly event caused the stop: the stop is confirmed by reading the lead, never by deleting
-- it, as a delete erases the person's reply thread in Instantly. A native signal is no lead's event.
ALTER TABLE outreach_stops ADD COLUMN keep_lead boolean NOT NULL DEFAULT false;

-- OPT_OUT (app/outreach/replies.py, the unsubscribe routes): an opt-out queues the stops in every project it reaches,
-- after it locks those projects FOR SHARE so that an enrollment in flight commits first. A row lock needs UPDATE on one
-- column; id is one no code path writes.
GRANT INSERT ON outreach_stops TO zenbree_system;
GRANT UPDATE (id) ON projects TO zenbree_system;
