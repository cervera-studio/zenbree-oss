import { cva, type VariantProps } from 'class-variance-authority';
import { Slot } from 'radix-ui';
import type * as React from 'react';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-transparent text-ui-strong whitespace-nowrap transition-all duration-out ease-exit outline-none hover:duration-in hover:ease-snap active:scale-97 active:duration-press active:ease-snap focus-visible:ring-3 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:opacity-45 aria-invalid:border-negative aria-invalid:ring-negative/20 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        primary: 'bg-accent text-on-accent hover:bg-accent/90',
        negative:
          'bg-negative text-on-negative hover:bg-negative/90 focus-visible:ring-negative/20',
        outline: 'border-line bg-ground text-ink hover:bg-hair',
        ghost: 'text-soft hover:bg-hair hover:text-ink',
        soft: 'bg-accent-tint text-accent-ink hover:bg-accent-tint/70',
        link: 'text-accent-ink underline-offset-4 hover:underline',
      },
      size: {
        xs: 'h-control-sm px-2',
        sm: 'h-control px-2.5',
        md: "h-control-md px-3.5 [&_svg:not([class*='size-'])]:size-4",
        lg: "h-control-lg px-4.5 text-sm-plus font-bold [&_svg:not([class*='size-'])]:size-4",
        'icon-xs': 'size-control-sm',
        'icon-sm': 'size-control',
        'icon-md': "size-control-md [&_svg:not([class*='size-'])]:size-4",
        'icon-lg': "size-control-lg [&_svg:not([class*='size-'])]:size-4",
      },
    },
    compoundVariants: [{ variant: 'link', class: 'px-0' }],
    defaultVariants: {
      variant: 'primary',
      size: 'md',
    },
  },
);

function Button({
  className,
  variant = 'primary',
  size = 'md',
  asChild = false,
  ...props
}: React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot.Root : 'button';

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
