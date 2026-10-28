-- #417: a plan downgrade waits for the renewal; the billing page shows it and can cancel it.
-- Written by POST /v1/billing/plan, cleared by the subscription webhook once the plan reaches it.
ALTER TABLE subscriptions ADD COLUMN scheduled_plan_id TEXT;
