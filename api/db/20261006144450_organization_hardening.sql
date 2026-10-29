-- Organization hardening (#323). Webhooks belong to the organization, notifications remember theirs, the
-- low-credit alert is armed per organization, and invitation emails are counted for the throttle.
-- Forward-only: the previous image's webhook insert names no organization and fails on NOT NULL (creating a
-- webhook is then a 500 until this image is back; delivery and everything else keep working).

-- ── webhooks: the organization's, managed by its admins; user_id stays who created one ──
-- Every webhook so far was its creator's, in the organization they founded (#308 made them admin-only).
ALTER TABLE user_webhooks ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;
UPDATE user_webhooks SET organization_id = own_organization(user_id);
ALTER TABLE user_webhooks ALTER COLUMN organization_id SET NOT NULL;
CREATE INDEX idx_user_webhooks_org ON user_webhooks(organization_id) WHERE revoked_at IS NULL;

-- ── notifications: the organization an item is about, so a removed member stops seeing it ──
-- NULL: rows from before this, and items about no organization; those stay visible.
ALTER TABLE notifications ADD COLUMN organization_id UUID REFERENCES organization(id) ON DELETE CASCADE;

-- ── the low-credit alert, armed per admin and organization (notification_settings.low_credits_armed was
-- per user, shared by every organization they administer; the column stays, unused) ──
CREATE TABLE low_credit_alerts (
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    armed           BOOLEAN NOT NULL,
    PRIMARY KEY (organization_id, user_id)
);
INSERT INTO low_credit_alerts (organization_id, user_id, armed)
SELECT m."organizationId", m."userId", s.low_credits_armed
FROM member m JOIN notification_settings s ON s.user_id = m."userId"
WHERE m.role = 'admin';

-- ── invitation emails sent, for the per-organization and per-address throttle (web/lib/team.ts) ──
CREATE TABLE invitation_sends (
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_invitation_sends_org ON invitation_sends(organization_id, sent_at);
