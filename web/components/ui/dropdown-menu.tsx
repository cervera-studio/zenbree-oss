'use client';

import { DropdownMenu as DropdownMenuPrimitive } from 'radix-ui';
import type * as React from 'react';

import { cn } from '@/lib/utils';

function DropdownMenu({ ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Root>) {
  return <DropdownMenuPrimitive.Root data-slot="dropdown-menu" {...props} />;
}

function DropdownMenuTrigger({
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Trigger>) {
  return <DropdownMenuPrimitive.Trigger data-slot="dropdown-menu-trigger" {...props} />;
}

function DropdownMenuContent({
  className,
  sideOffset = 4,
  container,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Content> & {
  /** Portal target. Defaults to `document.body` — override it wherever the
   * trigger sits on a surface whose CSS custom properties (theme colors)
   * must not be skipped, since a portal to `body` renders outside any
   * ancestor that scopes them. */
  container?: React.ComponentProps<typeof DropdownMenuPrimitive.Portal>['container'];
}) {
  return (
    <DropdownMenuPrimitive.Portal container={container}>
      <DropdownMenuPrimitive.Content
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        // `duration-*` alone leaves `transition-property` at its initial `all`, so the origin Radix
        // sets after positioning would glide from the centre: the keyframes are the only motion here.
        className={cn(
          'z-50 max-h-dropdown-available min-w-32 origin-dropdown overflow-x-hidden overflow-y-auto rounded-md border bg-ground p-1 text-ink shadow-md transition-none duration-in ease-snap data-[state=closed]:duration-press data-[state=closed]:ease-exit data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95',
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

function DropdownMenuItem({
  className,
  inset,
  variant = 'default',
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Item> & {
  inset?: boolean;
  variant?: 'default' | 'destructive';
}) {
  return (
    <DropdownMenuPrimitive.Item
      data-slot="dropdown-menu-item"
      data-inset={inset}
      data-variant={variant}
      className={cn(
        "relative flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none focus:bg-raised focus:text-ink data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[inset]:pl-8 data-[variant=destructive]:text-negative data-[variant=destructive]:focus:bg-negative/10 data-[variant=destructive]:focus:text-negative [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted data-[variant=destructive]:*:[svg]:text-negative!",
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuSeparator({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn('-mx-1 my-1 h-px bg-line', className)}
      {...props}
    />
  );
}

export {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
};
