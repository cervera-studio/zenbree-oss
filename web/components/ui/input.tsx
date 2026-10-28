import { cva, type VariantProps } from 'class-variance-authority';
import type * as React from 'react';

import { cn } from '@/lib/utils';

const inputVariants = cva(
  'w-full min-w-0 rounded-md border border-line-strong bg-ground px-2.5 py-1 text-body text-ink transition-[color,box-shadow] outline-none selection:bg-accent selection:text-on-accent file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-small file:font-bold file:text-ink placeholder:text-muted focus-visible:border-accent focus-visible:ring-3 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-45 aria-invalid:border-negative aria-invalid:ring-negative/20 desktop:text-ui',
  {
    variants: {
      size: {
        sm: 'h-control',
        md: 'h-control-md',
      },
    },
    defaultVariants: { size: 'md' },
  },
);

/** The 16 px text under the `desktop:` breakpoint keeps iOS from zooming a focused field. */
function Input({
  className,
  type,
  size,
  ...props
}: Omit<React.ComponentProps<'input'>, 'size'> & VariantProps<typeof inputVariants>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(inputVariants({ size }), className)}
      {...props}
    />
  );
}

export { Input };
