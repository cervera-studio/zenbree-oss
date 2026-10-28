import type { Tone } from '@/components/ui/badge';

/** Instantly's own campaign status numbers (`instantly_client.CAMPAIGN_STATUS`), named as the keys of
 *  `lists.instantly.campaignStatus`. The preview and the run carry only the number. */
const STATUS: Record<number, Exclude<CampaignStatusKey, 'other'>> = {
  0: 'draft',
  1: 'active',
  2: 'paused',
  3: 'completed',
  4: 'running_subsequences',
  [-99]: 'account_suspended',
  [-1]: 'accounts_unhealthy',
  [-2]: 'bounce_protect',
};
export type CampaignStatusKey =
  | 'draft'
  | 'active'
  | 'paused'
  | 'completed'
  | 'running_subsequences'
  | 'account_suspended'
  | 'accounts_unhealthy'
  | 'bounce_protect'
  | 'other';

export function campaignStatusKey(status: number): CampaignStatusKey {
  return STATUS[status] ?? 'other';
}

export const OUTCOMES = [
  'pending',
  'publishing',
  'accepted',
  'skipped',
  'failed',
  'uncertain',
] as const;
type Outcome = (typeof OUTCOMES)[number];

export const OUTCOME_TONE: Record<Outcome, Tone> = {
  pending: 'neutral',
  publishing: 'accent',
  accepted: 'positive',
  skipped: 'neutral',
  failed: 'negative',
  uncertain: 'warning',
};

// Mirrors `_PERMANENT` in api/app/automations/instantly_step.py: a campaign read that fails with one of these fails
// the same way on every run, with that step error, until the user fixes the connection. No screen may call it an
// outage that the next run gets past.
const FAILS_EVERY_RUN: Readonly<Record<string, string>> = {
  'INSTANTLY.DISABLED': 'vendor_disabled',
  'INSTANTLY.NOT_CONNECTED': 'instantly_not_connected',
  'INTEGRATIONS.CRYPTO_FAILED': 'instantly_not_connected',
  'INTEGRATIONS.NOT_CONFIGURED': 'instantly_not_connected',
  'INSTANTLY.NOT_FOUND': 'instantly_campaign_not_found',
  'INSTANTLY.INVALID_KEY': 'instantly_refused',
  'INSTANTLY.PLAN_REQUIRED': 'instantly_refused',
  'INSTANTLY.MISSING_SCOPE': 'instantly_refused',
  'INSTANTLY.REJECTED': 'instantly_refused',
};

export function failsEveryRun(code: unknown): code is string {
  return typeof code === 'string' && Object.hasOwn(FAILS_EVERY_RUN, code);
}

/** The step error a campaign read that fails every run ends the step with. */
export function everyRunStepCode(code: string): string | null {
  return failsEveryRun(code) ? (FAILS_EVERY_RUN[code] ?? null) : null;
}

/** The stored key cannot be decrypted: the step reports it as `instantly_not_connected`, and only this vendor code
 *  says the fix is to connect again or to give the server its encryption key. */
export function keyUnreadable(code: unknown): code is string {
  return failsEveryRun(code) && code.startsWith('INTEGRATIONS.');
}
