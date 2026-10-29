import { cn } from '@/lib/utils';
import { rowFocus } from './nav';

/** The title over a group of rail rows, the design's side title: in the app rail and the back
 *  office rail. */
export const groupLabel = 'text-label text-muted px-2 pt-3 pb-1 uppercase';

/** The back office rail's count beside a row that calls for attention: a pill, with a tint pair
 *  from the caller. */
export const countPill =
  'text-2xs inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 font-bold tabular-nums';

/** The count beside a row that calls for attention: the accent, semibold (the design's `hot`). */
export const countHot = 'text-data-sm text-accent-ink font-mono font-semibold';

/** The count beside any other row: plain mono text. */
export const countText = 'text-data-sm text-muted font-mono';

/** One rail row, the design's NavItem; `phone` gives it a 48 px target (the drawer). */
export function railRow(active: boolean, phone = false) {
  return cn(
    'text-ui flex shrink-0 items-center gap-2 rounded-md border px-2 whitespace-nowrap transition-colors',
    rowFocus,
    phone ? 'min-h-12' : 'h-control',
    active ? 'bg-ground text-ui-strong border-line' : 'text-ink hover:bg-hair border-transparent',
  );
}

/** The icon of a rail row: the brand colour on the current one, muted on the rest. */
export const railIcon = (active: boolean) =>
  cn('size-4 shrink-0', active ? 'text-accent' : 'text-muted');
