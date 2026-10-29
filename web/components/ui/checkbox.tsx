'use client';

import { Check, Minus } from 'lucide-react';
import { Checkbox as CheckboxPrimitive } from 'radix-ui';
import type * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * The design checkbox: a 14 px box, filled with the accent when checked or mixed. The mixed
 * state draws a dash, so checked and mixed are tellable apart by shape and not by colour.
 * The design's border is 1.5 px and its radius 3 px, which have no step: 2 px and 4 px stand in.
 */
function Checkbox({ className, ...props }: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        'peer size-3.5 shrink-0 rounded-sm border-2 border-line-strong bg-ground outline-none transition-shadow',
        'focus-visible:border-accent focus-visible:ring-3 focus-visible:ring-accent/50',
        'disabled:cursor-not-allowed disabled:opacity-45',
        'data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=checked]:text-on-accent',
        'data-[state=indeterminate]:border-accent data-[state=indeterminate]:bg-accent data-[state=indeterminate]:text-on-accent',
        'aria-invalid:border-negative aria-invalid:ring-negative/20',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator
        data-slot="checkbox-indicator"
        className="grid place-items-center text-current transition-none"
      >
        {props.checked === 'indeterminate' ? (
          <Minus className="size-2.75" aria-hidden="true" />
        ) : (
          <Check className="size-2.75" aria-hidden="true" />
        )}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
