// The active campaign page (C3): send days and each person's state, from the API's raw facts.
import type { Tone } from '@/components/ui/badge';
import type { Target } from './api';

/** Monday=0, as the API stores `send_window_days`: «lun–ven» for a run, «lun, mer» otherwise. */
export function sendDays(days: number[], locale: string): string {
  const name = (d: number) =>
    // 1 January 2024 was a Monday.
    new Intl.DateTimeFormat(locale, { weekday: 'short', timeZone: 'UTC' }).format(
      new Date(Date.UTC(2024, 0, 1 + d)),
    );
  const sorted = [...days].sort((a, b) => a - b);
  const [first, last] = [sorted[0], sorted.at(-1)];
  const run = sorted.every((d, i) => i === 0 || d === (sorted[i - 1] ?? d) + 1);
  if (run && sorted.length > 2 && first !== undefined && last !== undefined) {
    return `${name(first)}–${name(last)}`;
  }
  return sorted.map(name).join(', ');
}

export const PERSON_STATES = {
  missingValue: 'warning',
  invalidEmail: 'negative',
  replied: 'accent',
  excluded: 'negative',
  paused: 'neutral',
  awaiting: 'warning',
  done: 'positive',
  waiting: 'neutral',
} as const satisfies Record<string, Tone>;
export type PersonState = keyof typeof PERSON_STATES;

export function personState(t: Target, stepCount: number): PersonState {
  if (t.status === 'draft' && t.missing.length > 0) return 'missingValue';
  if (t.invalidEmail) return 'invalidEmail';
  if (t.status === 'replied') return 'replied';
  if (t.status === 'excluded') return 'excluded';
  if (t.status === 'paused') return 'paused';
  if (t.awaitingApproval) return 'awaiting';
  if (stepCount > 0 && t.nextStep > stepCount) return 'done';
  return 'waiting';
}

export type HoldReason = NonNullable<Target['hold']>;

export const HOLD_BADGES = {
  stop_failed: 'negative',
  stop_pending: 'warning',
  stopped: 'neutral',
  held_by_instantly: 'accent',
} as const satisfies Record<HoldReason, Tone>;

export function holdBadge(t: Target): HoldReason | null {
  return t.hold ?? (t.emailSender === 'instantly' ? 'held_by_instantly' : null);
}

/** Steps already behind the person: the dots filled in. */
export function stepsDone(t: Target, stepCount: number): number {
  return Math.max(0, Math.min(t.nextStep - 1, stepCount));
}

export type Activity =
  | { kind: 'paused'; until: string; messageId: string | null }
  | {
      kind: 'message';
      messageId: string;
      messageKind: NonNullable<Target['messageKind']>;
      label: Target['messageLabel'];
      at: string;
    }
  | { kind: 'missing'; tokens: string[] }
  | { kind: 'ready'; step: number }
  | { kind: 'invalidEmail' }
  | { kind: 'sent'; step: number; at: string }
  | { kind: 'none' };

export function activity(t: Target): Activity {
  if (t.status === 'paused' && t.pausedUntil) {
    return { kind: 'paused', until: t.pausedUntil, messageId: t.messageId };
  }
  if (t.messageId && t.messageKind && t.messageAt) {
    return {
      kind: 'message',
      messageId: t.messageId,
      messageKind: t.messageKind,
      label: t.messageLabel,
      at: t.messageAt,
    };
  }
  if (t.status === 'draft' && t.missing.length > 0) return { kind: 'missing', tokens: t.missing };
  if (t.awaitingApproval) return { kind: 'ready', step: t.nextStep };
  if (t.invalidEmail) return { kind: 'invalidEmail' };
  if (t.lastSentStep && t.lastSentAt)
    return { kind: 'sent', step: t.lastSentStep, at: t.lastSentAt };
  return { kind: 'none' };
}
