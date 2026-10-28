import { Check, Loader2, Minus, TriangleAlert, X } from 'lucide-react';
import type { SummaryState } from '@/lib/chat/tool-summary';
import { cn } from '@/lib/utils';

export type CallState = 'running' | SummaryState;

const STATE_ICON = {
  running: Loader2,
  done: Check,
  none: Minus,
  retry: TriangleAlert,
  error: X,
} satisfies Record<CallState, unknown>;

const STATE_TONE: Record<CallState, string> = {
  running: 'text-accent-ink animate-spin',
  done: 'text-positive',
  none: 'text-muted',
  retry: 'text-warning',
  error: 'text-negative',
};

/** The state of a tool call, as the chat chip and the admin transcript draw it. */
export function SummaryStateIcon({ state, label }: { state: CallState; label: string }) {
  const Icon = STATE_ICON[state];
  return <Icon aria-label={label} className={cn('size-3.5 shrink-0', STATE_TONE[state])} />;
}
