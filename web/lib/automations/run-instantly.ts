import 'server-only';

import {
  getInstantlyBatch,
  getInstantlyCampaigns,
  type InstantlyBatch,
} from '@/lib/integrations/instantly';
import type { Automation, AutomationRunDetail } from './api';
import { approval, instantlyMoving, instantlyOnApproval, runPlan } from './run-view';

export type RunInstantlyView = {
  /** Whether the campaign sends once an approval hands it the held people; `none` when no approval is offered. */
  onApproval: 'sends' | 'quiet' | 'unreadable' | 'none';
  batch: InstantlyBatch | 'unreadable' | null;
  /** The page refreshes itself while Instantly takes the people with no one acting. */
  moving: boolean;
};

/** The run's Instantly hand-off as the run page shows it, read now. Instantly is read only when the page needs it:
 *  the batch once there is one, the campaign while the approval that sends to it is offered. That approval also
 *  covers a run that holds nobody: later runs of the version add people with no review. */
export async function readRunInstantly(
  run: AutomationRunDetail,
  automation: Pick<Automation, 'version' | 'userId' | 'definition'>,
): Promise<RunInstantlyView> {
  const held = run.instantly;
  if (held?.batchId) {
    const read = await getInstantlyBatch(held.batchId);
    return {
      onApproval: 'none',
      batch: read.success ? read.data : 'unreadable',
      moving: instantlyMoving(held, read.success ? read.data.status : null),
    };
  }
  const moving = instantlyMoving(held, null);
  const step = runPlan(run, automation).instantly;
  if (!step || approval(run, automation) !== 'approve') {
    return { onApproval: 'none', batch: null, moving };
  }
  if (!step.campaignId) return { onApproval: 'unreadable', batch: null, moving };
  const campaigns = await getInstantlyCampaigns();
  const live = campaigns.success ? campaigns.data.campaigns : null;
  return { onApproval: instantlyOnApproval(live, step.campaignId), batch: null, moving };
}
