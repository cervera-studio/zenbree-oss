import Link from 'next/link';
import type { ReactNode } from 'react';
import type { Tone } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export type KeyValueItem = {
  label: string;
  value: ReactNode;
  /** A number or an id: the data font. */
  data?: boolean;
  /** A result that is good or bad (a margin). */
  tone?: Extract<Tone, 'positive' | 'negative'>;
  /** The value links to its own page. */
  href?: string;
  /** The full value, when `value` shortens it. */
  title?: string;
};

const TONE = { positive: 'text-positive', negative: 'text-negative' } as const;

/** Label and value rows, 24 px high: the label muted on the left, the value on the right. */
export function KeyValue({ items }: { items: readonly KeyValueItem[] }) {
  return (
    <dl className="flex flex-col">
      {items.map(({ label, value, data, tone, href, title }) => {
        const cls = cn(
          data ? 'text-data font-mono tabular-nums' : 'text-ui',
          href && 'text-accent-ink hover:underline',
          href && !data && 'text-ui-strong',
          tone && TONE[tone],
        );
        return (
          <div key={label} className="flex min-h-6 items-center gap-2">
            <dt className="text-ui text-muted flex-1">{label}</dt>
            <dd className="min-w-0 text-right">
              {href ? (
                <Link href={href} title={title} className={cls}>
                  {value}
                </Link>
              ) : (
                <span title={title} className={cls}>
                  {value}
                </span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
