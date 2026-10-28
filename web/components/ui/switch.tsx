'use client';

import { Switch as SwitchPrimitive } from 'radix-ui';
import type * as React from 'react';

import { cn } from '@/lib/utils';

// `warning`: a switch whose on state is a risk, such as a back-office vendor block.
const ON = {
  primary: 'data-[state=checked]:bg-accent',
  warning: 'data-[state=checked]:bg-warning',
};

/**
 * Switch — an immediate on/off, as opposed to a checkbox which is a value in a
 * form that gets submitted.
 *
 * The off track is `bg-line-strong`, not `bg-raised`: the thumb has to stay visible
 * against it in both themes, and `line-strong` is the token that already carries
 * "this is an interactive surface".
 */
function Switch({
  className,
  tone = 'primary',
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root> & { tone?: keyof typeof ON }) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'peer inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent shadow-xs outline-none transition-all',
        'focus-visible:border-accent focus-visible:ring-3 focus-visible:ring-accent/50',
        'disabled:cursor-not-allowed disabled:opacity-50',
        ON[tone],
        'data-[state=unchecked]:bg-line-strong',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className={cn(
          'pointer-events-none block size-4 rounded-full bg-ground ring-0 transition-transform',
          'data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0',
        )}
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
