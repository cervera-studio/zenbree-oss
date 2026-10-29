-- Every notification names its organization (#702, PR 3 of the organization-scoping run).
--
-- A NULL organization_id meant "every organization of the user". Row-level security gives a notification to the
-- organization in organization_id, so a NULL row would match no organization and disappear. Place each NULL row from
-- its own data, delete the ones that name no organization, then make the column NOT NULL.
--
-- Rollback floor: the previous image's notify_turn can still insert a NULL organization_id for a teammate's chat
-- while a deploy is in flight. The NOT NULL then rejects that insert; run_job catches it (loop.py, "agent.notify_failed")
-- and logs, so the turn finishes and only that notification is lost.
--
-- A uuid parsed out of href must not fail the migration: the regular expression admits only a well-formed uuid, and
-- the id is compared as text, so a malformed href stays unplaced and falls to the rules below it.

-- 1. /billing/switch?org=<uuid>: that organization.
UPDATE notifications n SET organization_id = o.id
FROM organization o
WHERE n.organization_id IS NULL
  AND n.href ~* '^/billing/switch\?org=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND o.id::text = lower(substring(n.href from '^/billing/switch\?org=(.+)$'));

-- 2. /?s=<chat session id>: the chat's organization.
UPDATE notifications n SET organization_id = c.organization_id
FROM chat_sessions c
WHERE n.organization_id IS NULL
  AND n.href ~* '^/\?s=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND c.id::text = lower(substring(n.href from '^/\?s=(.+)$'));

-- 3. /inbox?m=<inbound message id>: the message's organization.
UPDATE notifications n SET organization_id = m.organization_id
FROM outreach_inbound_messages m
WHERE n.organization_id IS NULL
  AND n.href ~* '^/inbox\?m=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND m.id::text = lower(substring(n.href from '^/inbox\?m=(.+)$'));

-- 4. /campaigns/<id>: the campaign's organization.
UPDATE notifications n SET organization_id = c.organization_id
FROM outreach_campaigns c
WHERE n.organization_id IS NULL
  AND n.href ~* '^/campaigns/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND c.id::text = lower(substring(n.href from '^/campaigns/(.+)$'));

-- 4b. data.list_id: the list's organization. A turn notification carries it since 2026-10-07; this closes the row of
-- a #702 turn whose chat is gone and whose user is in several organizations. The same text compare as above.
UPDATE notifications n SET organization_id = l.organization_id
FROM lists l
WHERE n.organization_id IS NULL
  AND n.data->>'list_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND l.id::text = lower(n.data->>'list_id');

-- 5. A user with exactly one organization: that organization. A NULL row was visible in every organization of its
-- user, with no ban test, so for this user nothing changes. A member row is not unique per organization, so count
-- organizations.
UPDATE notifications n SET organization_id = one.organization_id
FROM (
    SELECT m."userId" AS user_id, (array_agg(DISTINCT m."organizationId"))[1] AS organization_id
    FROM member m
    GROUP BY m."userId" HAVING count(DISTINCT m."organizationId") = 1
) one
WHERE n.organization_id IS NULL AND n.user_id = one.user_id;

-- 6. The rest names no organization: the user has several or none, and the data places none. Approved by the owner
-- on 2026-10-13.
DELETE FROM notifications WHERE organization_id IS NULL;

ALTER TABLE notifications ALTER COLUMN organization_id SET NOT NULL;

COMMENT ON TABLE notifications IS 'zb:personal-in-org user_id: a person''s notifications; organization_id is the organization an item is about';
