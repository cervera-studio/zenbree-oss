import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** The tones of a badge, a tag and a dot, named after the design colours. */
export type Tone = 'neutral' | 'accent' | 'warning' | 'negative' | 'positive';

/** The tone as text colour alone, with no fill (a stat note, a negative margin). */
export const TEXT: Record<Tone, string> = {
  neutral: 'text-soft',
  accent: 'text-accent-ink',
  warning: 'text-warning',
  negative: 'text-negative',
  positive: 'text-positive',
};

export const TINT: Record<Tone, string> = {
  neutral: cn('bg-hair', TEXT.neutral),
  accent: cn('bg-accent-tint', TEXT.accent),
  warning: cn('bg-warning-tint', TEXT.warning),
  negative: cn('bg-negative-tint', TEXT.negative),
  positive: cn('bg-positive-tint', TEXT.positive),
};

/** A status dot's fill, shared with the filter chips and the job stages. */
export const DOT: Record<Tone, string> = {
  neutral: 'bg-neutral-dot',
  accent: 'bg-accent',
  warning: 'bg-warning',
  negative: 'bg-negative',
  positive: 'bg-positive',
};

/** A short state or category label on the tone's tint, with an optional leading dot. */
export function Badge({
  tone,
  dot = false,
  className,
  title,
  children,
}: {
  tone: Tone;
  dot?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex h-5 items-center gap-1.25 rounded-sm px-1.5 text-xs font-bold whitespace-nowrap',
        TINT[tone],
        className,
      )}
    >
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** A value label in the data font (a role, a vendor that is off). */
export function Tag({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex h-4.5 items-center rounded-sm px-1.25 font-mono text-data-sm tabular-nums whitespace-nowrap',
        TINT[tone],
      )}
    >
      {children}
    </span>
  );
}

/** A quiet state in a table cell: a dot and soft text, no fill (Utenti › Stato). */
export function StatusDot({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="text-soft inline-flex items-center gap-1.5 text-small whitespace-nowrap">
      <span aria-hidden className={cn('size-1.75 shrink-0 rounded-full', DOT[tone])} />
      {children}
    </span>
  );
}
