import type * as React from 'react';

import { cn } from '@/lib/utils';

// The design data table: a framed box, a raised header, hairline rows and columns, numbers
// right-aligned in the data font. Only box properties reach the cells: a descendant selector
// would outrank a caller's text colour on a cell (a red margin, an amber delay).
const FRAMED = cn(
  'text-ui text-soft',
  '[&_thead_tr]:border-line [&_tbody_tr]:border-hair',
  '[&_th]:h-row-head [&_th]:bg-raised [&_th]:px-2.5 [&_th]:font-bold',
  '[&_td]:h-row [&_td]:px-2.5 [&_td]:py-0',
  '[&_td:not(:last-child)]:border-r [&_td:not(:last-child)]:border-hair',
  '[&_th:not(:last-child)]:border-r [&_th:not(:last-child)]:border-hair',
);

// The same look for a `StackedTable`: the phone turns each row into a card, and the table rules
// would draw column lines and a fixed row height inside it, so they start at `desktop:`.
const FRAMED_FROM_DESKTOP = cn(
  'desktop:text-ui desktop:text-soft',
  'desktop:[&_thead_tr]:border-line desktop:[&_tbody_tr]:border-hair',
  'desktop:[&_th]:h-row-head desktop:[&_th]:bg-raised desktop:[&_th]:px-2.5 desktop:[&_th]:font-bold',
  'desktop:[&_td]:h-row desktop:[&_td]:px-2.5 desktop:[&_td]:py-0',
  'desktop:[&_td:not(:last-child)]:border-r desktop:[&_td:not(:last-child)]:border-hair',
  'desktop:[&_th:not(:last-child)]:border-r desktop:[&_th:not(:last-child)]:border-hair',
);

const FRAME_BOX = 'rounded-lg border border-line bg-ground';
const FRAME_BOX_FROM_DESKTOP =
  'desktop:rounded-lg desktop:border desktop:border-line desktop:bg-ground';

// A long table keeps its column names in view. The box that scrolls sideways also scrolls
// downwards (overflow-x alone makes it a scroll container), so a sticky header only sticks when
// that box has a height to scroll in: one screen, less the top bar and some page above it.
const STICKY_BOX = 'max-h-[calc(100dvh-8rem)] overflow-y-auto';
const STICKY_HEAD = '[&_th]:sticky [&_th]:top-0 [&_th]:z-10';

/**
 * `frame: 'desktop'` is for a table that is cards on the phone (`StackedTable` passes it).
 * `stickyHeader` is for a table longer than the screen.
 */
function Table({
  className,
  frame = false,
  stickyHeader = false,
  ...props
}: Omit<React.ComponentProps<'table'>, 'frame'> & {
  frame?: boolean | 'desktop';
  stickyHeader?: boolean;
}) {
  const fromDesktop = frame === 'desktop';
  return (
    <div
      data-slot="table-container"
      className={cn(
        'relative w-full overflow-x-auto',
        frame && (fromDesktop ? FRAME_BOX_FROM_DESKTOP : FRAME_BOX),
        stickyHeader && STICKY_BOX,
      )}
    >
      <table
        data-slot="table"
        className={cn(
          'w-full caption-bottom text-sm',
          frame && (fromDesktop ? FRAMED_FROM_DESKTOP : FRAMED),
          stickyHeader && STICKY_HEAD,
          className,
        )}
        {...props}
      />
    </div>
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<'thead'>) {
  return <thead data-slot="table-header" className={cn('[&_tr]:border-b', className)} {...props} />;
}

function TableBody({ className, ...props }: React.ComponentProps<'tbody'>) {
  return (
    <tbody
      data-slot="table-body"
      className={cn('[&_tr:last-child]:border-0', className)}
      {...props}
    />
  );
}

function TableRow({ className, ...props }: React.ComponentProps<'tr'>) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        'border-b transition-colors hover:bg-raised/50 has-aria-expanded:bg-raised/50 data-[state=selected]:bg-raised',
        className,
      )}
      {...props}
    />
  );
}

function TableHead({
  className,
  num = false,
  ...props
}: React.ComponentProps<'th'> & { num?: boolean }) {
  return (
    <th
      data-slot="table-head"
      data-numeric={num || undefined}
      className={cn(
        'h-10 px-2 text-left align-middle font-medium whitespace-nowrap text-ink data-numeric:text-right data-numeric:tabular-nums [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-0.5',
        className,
      )}
      {...props}
    />
  );
}

/** `num`: a number, right-aligned in the data font. Plain classes, so a caller's colour wins. */
function TableCell({
  className,
  num = false,
  ...props
}: React.ComponentProps<'td'> & { num?: boolean }) {
  return (
    <td
      data-slot="table-cell"
      data-numeric={num || undefined}
      className={cn(
        'p-2 align-middle whitespace-nowrap data-numeric:text-right data-numeric:tabular-nums [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-0.5',
        num && 'font-mono text-data text-ink',
        className,
      )}
      {...props}
    />
  );
}

export { Table, TableBody, TableCell, TableHead, TableHeader, TableRow };
