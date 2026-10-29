-- An opt-out reaches the person's other addresses and LinkedIn profiles (#1121). The exclusion starts from the opted-out
-- verdicts and probes these two indexes per verdict, so its cost follows the opt-outs, not the open targets.
-- A person identity's email aliases by value: the alias table's unique key leads with the client, and a verdict names
-- no client.
CREATE INDEX audience_aliases_person_email_idx ON audience_aliases (value, organization_id)
    WHERE entity = 'person' AND kind = 'email' AND namespace = '';
-- linkedin_alias is a stored column (20261017152209_outreach_held_keys.sql), so row-level security lets an equality on
-- it be an index condition.
CREATE INDEX outreach_campaign_targets_linkedin_alias_idx ON outreach_campaign_targets (linkedin_alias, organization_id)
    WHERE linkedin_alias IS NOT NULL;
