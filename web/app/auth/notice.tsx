import type { ReactNode } from 'react';
import { DOT, TEXT, TINT, type Tone } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

type NoticeTone = Extract<Tone, 'accent' | 'positive' | 'negative'>;

// A negative notice interrupts (alert); the others are announced politely (status).
const ROLES: Record<NoticeTone, 'status' | 'alert'> = {
  accent: 'status',
  positive: 'status',
  negative: 'alert',
};

export function Notice({
  tone,
  title,
  children,
}: {
  tone: NoticeTone;
  title?: string;
  children: ReactNode;
}) {
  return (
    <div role={ROLES[tone]} className={cn('flex flex-col gap-1 rounded-lg p-3', TINT[tone])}>
      {title && (
        <strong className={cn('text-ui-strong flex items-center gap-2', TEXT[tone])}>
          <span aria-hidden className={cn('size-1.75 shrink-0 rounded-full', DOT[tone])} />
          {title}
        </strong>
      )}
      <div className="text-ui flex flex-col gap-1 text-ink">{children}</div>
    </div>
  );
}
