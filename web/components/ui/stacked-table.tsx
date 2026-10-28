import type { ComponentProps, ReactNode } from 'react';
import { Table } from '@/components/ui/table';
import { cn } from '@/lib/utils';

const STACKED = cn(
  'phone:block phone:[&_thead]:hidden phone:[&_tbody]:flex phone:[&_tbody]:flex-col phone:[&_tbody]:gap-2',
  'phone:[&_tr]:grid phone:[&_tr]:grid-cols-[minmax(0,1fr)_auto] phone:[&_tr]:items-center phone:[&_tr]:gap-x-3 phone:[&_tr]:gap-y-1.5',
  'phone:[&_tr]:rounded-lg phone:[&_tr]:border! phone:[&_tr]:border-line phone:[&_tr]:bg-ground phone:[&_tr]:p-3.5',
  'phone:[&_td]:min-w-0 phone:[&_td]:p-0 phone:[&_td]:whitespace-normal phone:[&_td]:wrap-anywhere',
);

/** `frame` draws the data-table frame and raised header on desktop only; the phone keeps the cards. */
export function StackedTable({
  className,
  frame = false,
  ...props
}: Omit<ComponentProps<'table'>, 'frame'> & { frame?: boolean }) {
  return <Table frame={frame && 'desktop'} className={cn(STACKED, className)} {...props} />;
}

/** A stacked cell's column name. The phone hides the header row, and Safari drops table semantics
 * from a restyled table, so the cell names its column itself: aloud by default, on screen if visible. */
export function CellLabel({
  children,
  visible = false,
}: {
  children: ReactNode;
  visible?: boolean;
}) {
  return (
    <span className={cn('hidden phone:inline', !visible && 'phone:sr-only')}>
      {children}
      {': '}
    </span>
  );
}
