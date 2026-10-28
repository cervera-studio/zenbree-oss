import Link from 'next/link';
import { useFormatter } from 'next-intl';
import type { ReactNode } from 'react';
import { DOT, type Tone } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/** The chip look, for a toggle button that filters in place. */
export const chipClass = (on: boolean) =>
  cn(
    'text-ui-strong inline-flex h-control-sm items-center gap-1.5 rounded-md border px-2.25 whitespace-nowrap transition-colors',
    'outline-none focus-visible:ring-2 focus-visible:ring-accent',
    on
      ? 'border-accent-tint bg-accent-tint text-accent-ink'
      : 'border-line text-soft hover:bg-raised',
  );

/** A filter or sort link above a list; `on` marks the one applied. */
export function FilterChip({
  href,
  on,
  count,
  dot,
  children,
}: {
  href: string;
  on: boolean;
  count?: number;
  /** The status the chip filters by, as a dot before the label. */
  dot?: Tone;
  children: ReactNode;
}) {
  const format = useFormatter();
  return (
    <Link href={href} aria-current={on ? 'true' : undefined} className={chipClass(on)}>
      {dot && <span aria-hidden className={cn('size-1.5 rounded-full', DOT[dot])} />}
      {children}
      {count !== undefined && (
        <span className="text-data-sm font-mono tabular-nums">
          {format.number(count, 'credits')}
        </span>
      )}
    </Link>
  );
}
