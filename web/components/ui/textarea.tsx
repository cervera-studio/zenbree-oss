import type * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * Textarea. Shares `Input`'s chrome so a form mixing the two reads as one
 * control family — same border token, same focus ring, same `text-base
 * md:text-sm` (16px under `md` is what stops Safari zooming the page on focus).
 *
 * It used to carry the older ring — `ring-2` with a `ring-offset-2` halo, while
 * `Input` and `NativeSelect` had moved to the flat `ring-3 ring-accent/50`.
 * On a form with both, tabbing from an input to a textarea visibly changed the
 * shape of the focus indicator. That is the kind of difference nobody files a
 * bug for and everybody sees.
 */
function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        'flex min-h-20 w-full rounded-md border border-line-strong bg-ground px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted disabled:cursor-not-allowed disabled:opacity-50 md:text-sm',
        'focus-visible:border-accent focus-visible:ring-3 focus-visible:ring-accent/50',
        'aria-invalid:border-negative aria-invalid:ring-negative/20',
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
