-- The campaign beat applies the stored opt-outs of every departed campaign owner once a minute
-- (due.exclude_opted_out_of_departed_owners). It reads each departed owner's opted-out verdicts: a few rows among
-- every verdict the gate ever stored. The reason is a literal there, so a generic plan can use this index too.
CREATE INDEX crm_gate_checks_opted_out_idx ON crm_gate_checks (owner_id) WHERE reason = 'HUBSPOT_OPTED_OUT';
