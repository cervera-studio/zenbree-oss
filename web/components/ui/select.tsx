import { cva, type VariantProps } from 'class-variance-authority';
import { ChevronsUpDown } from 'lucide-react';
import type * as React from 'react';

import { cn } from '@/lib/utils';

const selectVariants = cva(
  'w-full min-w-0 appearance-none rounded-md border border-line-strong bg-ground pr-8 pl-2.5 text-body text-ink transition-[color,box-shadow] outline-none focus-visible:border-accent focus-visible:ring-3 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-45 aria-invalid:border-negative aria-invalid:ring-negative/20 desktop:text-ui',
  {
    variants: {
      size: {
        sm: 'h-control',
        md: 'h-control-md',
      },
      touch: {
        true: 'phone:h-11',
        false: '',
      },
    },
    defaultVariants: { size: 'md', touch: false },
  },
);

/**
 * A native `<select>` in the design's input look, with the up-down chevrons on the right. It stays
 * native for forms, server actions and the phone pickers. `className` sizes the wrapper (a `grow`),
 * and the select fills it. `touch` gives the select a 44 px tap height on the phone. The 16 px text
 * under `desktop:` keeps iOS from zooming a focused field.
 */
function Select({
  className,
  size,
  touch,
  children,
  ...props
}: Omit<React.ComponentProps<'select'>, 'size'> & VariantProps<typeof selectVariants>) {
  return (
    <span className={cn('relative block', className)}>
      <select data-slot="select" className={selectVariants({ size, touch })} {...props}>
        {children}
      </select>
      <ChevronsUpDown
        aria-hidden
        className="text-muted pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2"
      />
    </span>
  );
}

export { Select };
