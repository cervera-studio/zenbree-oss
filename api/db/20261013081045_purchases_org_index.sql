-- #688: the admin users and organization pages, and the organization delete cascade, find purchases by organization_id.
CREATE INDEX idx_purchases_org ON purchases(organization_id);
