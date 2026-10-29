import type { Tone } from '@/components/ui/badge';
import type { Transaction } from './schemas';

export const TX_TONES = {
  deduction: 'neutral',
  reservation: 'neutral',
  adjustment: 'neutral',
  refund: 'positive',
  bonus: 'positive',
  renewal: 'accent',
  purchase: 'accent',
  expiry: 'warning',
} as const satisfies Record<string, Tone>;

export type TxType = keyof typeof TX_TONES;

const isTxType = (v: string): v is TxType => Object.hasOwn(TX_TONES, v);

export function txType(tx: Pick<Transaction, 'type'>): TxType | null {
  return isTxType(tx.type) ? tx.type : null;
}

// What a ledger row is, from `type` and `reference_type` (the API's free-text `description` is English and
// carries ids). `api/tests/test_ledger_kinds.py` fails when the API writes a reference type missing here.
const KINDS = {
  'reservation:job': 'jobHold',
  'reservation:enrich_phone': 'phoneLookup',
  'reservation:ai_column': 'aiColumnFill',
  'reservation:saved_search': 'scheduledSearch',
  'reservation:watchlist': 'watchlistRun',
  'reservation:automation': 'automationRun',
  // A settled hold is rewritten to a deduction (commit_reservation, settle_reservation_with_slack).
  'deduction:job': 'jobUsed',
  'deduction:enrich_phone': 'phoneLookup',
  'deduction:ai_column': 'aiColumnFill',
  'deduction:saved_search': 'scheduledSearch',
  'deduction:watchlist': 'watchlistRun',
  'deduction:automation': 'automationRun',
  'refund:reservation.slack': 'slackRefund',
  // #747: a hard bounce of a verified address gives its email credit back
  'refund:email_bounce': 'bounceRefund',
  'purchase:stripe_checkout': 'topup',
  'purchase:stripe_auto_topup': 'autoTopup',
  'renewal:stripe_plan_period': 'plan',
  'renewal:stripe_plan_upgrade': 'plan',
  'expiry:plan_expiry': 'planExpiry',
} as const;

export type TxKind = (typeof KINDS)[keyof typeof KINDS] | 'released';

export function txKind(tx: { type: string; referenceType: string | null }): TxKind | null {
  const kind = KINDS[`${tx.type}:${tx.referenceType}` as keyof typeof KINDS];
  if (kind) return kind;
  // A released hold copies its reservation's reference type, so any other refund is a release.
  return tx.type === 'refund' ? 'released' : null;
}
