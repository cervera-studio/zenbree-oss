-- #1057: a message answered before the settle cleared drafts still offers its draft in the inbox.
UPDATE outreach_inbound_messages m SET draft_body = NULL
WHERE m.draft_body IS NOT NULL
  AND EXISTS (SELECT 1 FROM outreach_inbox_replies r WHERE r.inbound_id = m.id AND r.status = 'sent');
