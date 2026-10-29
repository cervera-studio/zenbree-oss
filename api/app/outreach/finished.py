"""When an active campaign is finished (#218): the SQL predicate over the campaign ``c`` that the beat completes it by."""

from app.outreach import sending

# Nobody is left in a sequence: everyone replied, was excluded, is past the last step, or is stuck for good on a
# step that ended in a terminal failure (nothing advances past it). A paused person keeps the campaign open: they
# can be resumed. Draft people (a refill waiting for review) do not: a completed campaign drops pending refill. Drafts
# of an automation's review run do keep it open: the approval that promotes them has not come yet (#939). So do
# drafts that wait for a value (#933): a regenerate starts them, and it refuses a completed campaign.
FINISHED = f"""NOT EXISTS (
    SELECT 1 FROM outreach_campaign_targets t
     WHERE t.campaign_id = c.id AND t.status IN ('active', 'paused') AND t.next_step <= jsonb_array_length(c.steps)
       AND NOT EXISTS (SELECT 1 FROM outreach_campaign_sends f
                        WHERE f.campaign_id = c.id AND f.target_id = t.id AND f.step = t.next_step
                          AND (f.status = 'refused' OR (f.status = 'failed' AND f.attempts >= {sending.MAX_ATTEMPTS}))))
  AND NOT EXISTS (SELECT 1 FROM outreach_campaign_targets d
                    JOIN automation_enrollments e ON e.target_id = d.id
                    JOIN automation_runs r ON r.id = e.run_id
                    JOIN automations a ON a.id = r.automation_id AND a.version = r.version
                   WHERE d.campaign_id = c.id AND d.status = 'draft' AND r.review AND r.approved_at IS NULL
                     AND a.approved_version IS DISTINCT FROM r.version)
  AND NOT EXISTS (SELECT 1 FROM outreach_campaign_targets w
                   WHERE w.campaign_id = c.id AND w.status = 'draft' AND w.personalization IS NOT NULL)"""
