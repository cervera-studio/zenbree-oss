import type { Automation, AutomationRun, AutomationRunDetail, RunStep, StepKind } from './api';

/** Who may pause, run, cancel and approve: the creator or an admin of the organization, as the API checks. */
export type Viewer = { id: string; isOrgAdmin: boolean };

export function canManage(viewer: Viewer, automation: Pick<Automation, 'userId'>): boolean {
  return viewer.isOrgAdmin || (automation.userId !== null && automation.userId === viewer.id);
}

/** The next run is a review run: the API decides it when a run starts, from the same two numbers. */
export function nextIsReview(automation: Pick<Automation, 'version' | 'approvedVersion'>): boolean {
  return automation.approvedVersion !== automation.version;
}

export function isReview(
  run: Pick<AutomationRun, 'review' | 'version' | 'status'>,
  automation: Pick<Automation, 'approvedVersion'>,
): boolean {
  return run.review ?? (run.status === 'queued' && automation.approvedVersion !== run.version);
}

/** `creditsSpent` is written only when a run closes normally. A running run, one the reaper closed
 *  (`unknown`) and one whose creator left mid-run still show 0 there, whatever their steps charged. */
export function spentIsFinal(run: Pick<AutomationRun, 'status' | 'errorCode'>): boolean {
  return run.status !== 'running' && run.status !== 'unknown' && run.errorCode !== 'creator_left';
}

/** What the steps charged: each step records its credits in the transaction that charges them. */
export function stepCredits(run: Pick<AutomationRunDetail, 'steps'>): number {
  return run.steps.reduce((sum, s) => sum + s.credits, 0);
}

const enrolledCount = (run: Pick<AutomationRunDetail, 'steps'>): number => {
  const step = run.steps.find((s) => s.kind === 'add_to_campaign' && s.status === 'succeeded');
  return typeof step?.output.enrolled === 'number' ? step.output.enrolled : 0;
};

const heldCount = (run: Pick<AutomationRunDetail, 'steps'>): number => {
  const step = run.steps.find((s) => s.kind === 'write_hubspot' && s.status === 'succeeded');
  return typeof step?.output.heldForApproval === 'number' ? step.output.heldForApproval : 0;
};

export function hasApprovableResult(run: Pick<AutomationRunDetail, 'status' | 'steps'>): boolean {
  return run.status === 'succeeded' || enrolledCount(run) > 0;
}

/**
 * What the approve control offers on a run, as `approval._approvable` and `approve` decide:
 * `approve` a finished review run of the current version (one that did not succeed only when it left
 * people in the campaign); `outdated` a review run that an edit left behind.
 */
export function approval(
  run: AutomationRunDetail,
  automation: Pick<Automation, 'version' | 'userId'>,
): 'approve' | 'retryHubspot' | 'hubspotPending' | 'outdated' | null {
  if (run.approvedAt) {
    const creatorGone = automation.userId === null;
    if (creatorGone) return null;
    const after = run.summary.hubspotAfterApproval;
    if (after) return after.errorCode ? 'retryHubspot' : null;
    return heldCount(run) > 0 ? 'hubspotPending' : null;
  }
  if (!run.review || run.status === 'queued' || run.status === 'running') return null;
  if (!hasApprovableResult(run)) return null;
  return run.version === automation.version ? 'approve' : 'outdated';
}

export function cancelOutcome(
  run: Pick<AutomationRun, 'status'>,
): 'cancelled' | 'cancelRunning' | 'cancelEnded' {
  if (run.status === 'cancelled') return 'cancelled';
  return run.status === 'running' ? 'cancelRunning' : 'cancelEnded';
}

export const CANCELLABLE: readonly AutomationRun['status'][] = ['queued', 'running'];

// The output counts each step records (`steps.py`, `hubspot.py`), in the order they read best.
// A key that one kind shares with another means the same there.
const OUTPUT = {
  find_people: [
    'accounts',
    'accountsWithoutDomain',
    'accountsExcluded',
    'peopleFound',
    'peopleExcluded',
    'peopleAdded',
  ],
  qualify: ['scored', 'kept', 'belowMinimum', 'notReached'],
  find_emails: [
    'searched',
    'found',
    'refusedByRules',
    'vendorDidNotAnswer',
    'notReached',
    'withContact',
  ],
  add_to_campaign: ['enrolled'],
  add_to_instantly: ['held'],
  write_hubspot: [
    'heldForApproval',
    'created',
    'existing',
    'alreadyWritten',
    'heldByHubspotGate',
    'refusedSince',
  ],
} as const satisfies Record<StepKind, readonly string[]>;
export type OutputKey = (typeof OUTPUT)[StepKind][number];

export function outputRows(step: Pick<RunStep, 'kind' | 'output'>): [OutputKey, number][] {
  return OUTPUT[step.kind].flatMap((key): [OutputKey, number][] => {
    const value = step.output[key];
    return typeof value === 'number' ? [[key, value]] : [];
  });
}

export const LEFT_OUT = [
  'excluded',
  'duplicate',
  'noContact',
  'inOtherCampaign',
  'heldByInstantly',
  'stopped',
  'overLimit',
] as const;
export const NOBODY = ['account_gone', 'no_new_person', 'all_refused'] as const;

export const REFUSED = [
  'excluded',
  'alreadyHandled',
  'inOtherCampaign',
  'heldByInstantly',
  'stopped',
] as const;

export function reasons<K extends string>(known: readonly K[], counts: unknown): [K, number][] {
  if (typeof counts !== 'object' || counts === null) return [];
  const map = counts as Record<string, unknown>;
  return known.flatMap((k): [K, number][] => {
    const n = map[k];
    return typeof n === 'number' && n > 0 ? [[k, n]] : [];
  });
}

// The reasons `instantly_verdicts.judge` leaves a person out, as the step's `skipped` keys arrive (camelCase).
export const INSTANTLY_SKIPPED = [
  'excluded',
  'noEmail',
  'duplicateInSelection',
  'inNativeCampaign',
  'repliedOrContacted',
  'alreadyInInstantly',
  'crmHold',
  'stopped',
] as const;

/** The statuses in which an Instantly review run is, or may become, approvable: `approval` offers it only once the
 *  run succeeds, so after a failure or a cancel none comes. */
export const INSTANTLY_APPROVABLE: readonly AutomationRun['status'][] = [
  'queued',
  'running',
  'succeeded',
];

export type RunPlan = {
  /** Null without an `add_to_instantly` step. `nobody` when the run succeeded holding nobody (`run.instantly` is
   *  null), as when the step found nobody to add: its approval still lets later runs add people with no review. A
   *  queued or running run may still hold people, and a run that failed or was cancelled judged nobody. */
  instantly: { nobody: boolean; campaignId: string | null } | null;
  hubspot: boolean;
  campaign: boolean;
};

/** The steps of the run's version: its own rows once it starts (one for each step of the definition), the
 *  automation's definition while it is queued. The campaign is the one the step read, else the saved one. */
export function runPlan(
  run: Pick<AutomationRunDetail, 'steps' | 'instantly' | 'status'>,
  automation: Pick<Automation, 'definition'>,
): RunPlan {
  const saved = automation.definition.steps;
  const kinds: StepKind[] =
    run.steps.length > 0 ? run.steps.map((s) => s.kind) : saved.map((s) => s.type);
  const hubspot = kinds.includes('write_hubspot');
  const campaign = kinds.includes('add_to_campaign');
  if (!kinds.includes('add_to_instantly')) return { instantly: null, hubspot, campaign };
  const read = run.steps.find((s) => s.kind === 'add_to_instantly')?.output.campaign;
  const readId =
    typeof read === 'object' && read !== null && 'id' in read && typeof read.id === 'string'
      ? read.id
      : null;
  const savedId = saved.find((s) => s.type === 'add_to_instantly')?.campaignId ?? null;
  return {
    instantly: {
      nobody: run.instantly === null && run.status === 'succeeded',
      campaignId: run.instantly?.campaign.id ?? readId ?? savedId,
    },
    hubspot,
    campaign,
  };
}

/** Whether the run's Instantly campaign can send once the approval hands it the people, read live: a campaign
 *  missing from the workspace's list cannot be checked, like a read that failed. */
export function instantlyOnApproval(
  campaigns: readonly { id: string; maySend: boolean }[] | null,
  campaignId: string,
): 'sends' | 'quiet' | 'unreadable' {
  const found = campaigns?.find((c) => c.id === campaignId);
  if (!found) return 'unreadable';
  return found.maySend ? 'sends' : 'quiet';
}

/** Instantly takes the run's people with no one acting: the page follows it until the batch ends. */
export function instantlyMoving(
  instantly: AutomationRunDetail['instantly'],
  batch: 'queued' | 'running' | 'completed' | 'cancelled' | 'failed' | null,
): boolean {
  if (!instantly) return false;
  if (instantly.batchId) return batch === 'queued' || batch === 'running';
  return instantly.waitingFor === 'starting' || instantly.waitingFor === 'other_batch';
}

export const ENROLLED_AS = ['draft', 'active'] as const;

export const SKIP_DECISIONS = [
  'previous_step_failed',
  'run_ended',
  'no_new_people',
  'no_people_left',
  'cancelled',
  'automation_paused',
  'reservation_expired',
  'run_not_running',
] as const;

export const ERROR_CODES = [
  'vendor_failed',
  'vendor_disabled',
  'step_crashed',
  'campaign_ended',
  'instantly_campaign_not_found',
  'instantly_not_connected',
  'instantly_refused',
  'instantly_unreachable',
  'hubspot_off',
  'hubspot_token',
  'hubspot_failed',
  'write_crashed',
  'cancelled',
  'automation_paused',
  'automation_edited',
  'reservation_expired',
  'run_not_running',
  'creator_deleted',
  'user_banned',
  'creator_left',
  'invalid_configuration',
  'project_cap_reached',
  'insufficient_credits',
  'worker_outcome_unknown',
  'credit_cap_reached',
] as const;

const MAY_HAVE_WRITTEN = ['hubspot_failed'] as const;

export function mayHaveWritten(step: Pick<RunStep, 'errorCode'>): boolean {
  return known(MAY_HAVE_WRITTEN, step.errorCode);
}

export function known<K extends string>(list: readonly K[], value: string | null): value is K {
  return value !== null && (list as readonly string[]).includes(value);
}
