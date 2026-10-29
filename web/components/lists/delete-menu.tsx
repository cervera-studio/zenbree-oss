'use client';

import { Ellipsis, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

/**
 * Delete one step away, behind a «…» menu: a bin always in reach next to a name is one stray click
 * from losing a list or a column. The toast's «Annulla» still undoes it.
 */
export function DeleteMenu({
  label,
  item,
  onDelete,
  className,
}: {
  /** The trigger's accessible name: what the menu acts on. */
  label: string;
  item: string;
  onDelete: () => void;
  className?: string;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={label}
          title={label}
          className={cn('text-muted hover:text-ink', className)}
        >
          <Ellipsis />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem variant="destructive" onSelect={onDelete}>
          <Trash2 /> {item}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
