import { ArrowRight } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { Panel } from '@/components/ui/panel';

/** A row of a home panel: the design's `list-row`, 38 px with a hairline below. */
export const ROW = 'border-hair flex min-h-row items-center gap-2.5 border-b px-3 last:border-b-0';

/** The accent text link at the end of a panel head or row ("Tutte le liste →"). */
export function GoLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="text-accent-ink text-small inline-flex items-center gap-1 font-bold whitespace-nowrap hover:underline"
    >
      {children}
      <ArrowRight aria-hidden className="size-3.5" />
    </Link>
  );
}

/** A `Panel` of list rows, clipped to its rounded frame so a row's hover fill stays inside. */
export function HomePanel({
  title,
  meta,
  action,
  children,
}: {
  title: string;
  meta?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Panel title={title} meta={meta} action={action} flush className="min-w-0 overflow-hidden">
      {children}
    </Panel>
  );
}
