'use client';

import { X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { useState } from 'react';
import { BottomSheetContent } from '@/components/ui/bottom-sheet';
import { Button } from '@/components/ui/button';
import { Dialog, DialogTitle } from '@/components/ui/dialog';
import type { ListColumn, ListRow, RowRating } from '@/lib/lists/api';
import type { ColumnSpec } from '@/lib/lists/columns';
import { cn } from '@/lib/utils';
import { Cell } from './list-cells';
import { cardSummary } from './list-filter';
import { FitScore, RateToggles } from './row-cells';

/** The phone list (#171): one card per row; a tap opens every column in a bottom sheet. */
export function ListCards({
  specs,
  rows,
  onToggleExcluded,
  onRate,
  chatHref,
  columns,
}: {
  specs: ColumnSpec[];
  rows: ListRow[];
  onToggleExcluded: (row: ListRow) => void;
  onRate?: (row: ListRow, rating: RowRating) => void;
  /** The list's chat, where «Chiedi in chat» leads; null without one. */
  chatHref: string | null;
  /** The list's columns, for the required ones a row misses. */
  columns: ListColumn[];
}) {
  const t = useTranslations('lists');
  const [openId, setOpenId] = useState<string | null>(null);
  // Closing keeps the id, so the sheet still holds its row while it slides out.
  const [shown, setShown] = useState(false);
  // By id, so the sheet shows the row's latest state after a toggle or a rating.
  const open = rows.find((r) => r.id === openId);
  const show = (id: string) => {
    setOpenId(id);
    setShown(true);
  };

  return (
    <>
      <ul className="divide-y">
        {rows.map((row) => {
          const { title, subtitle } = cardSummary(row, specs);
          return (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => show(row.id)}
                title={!row.excluded && row.delivered === false ? t('incomplete') : undefined}
                className={cn(
                  'hover:bg-raised flex min-h-14 w-full items-center gap-3 px-4 py-2 text-left',
                  row.excluded ? 'opacity-40' : row.delivered === false && 'text-muted italic',
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{title}</span>
                  {subtitle && (
                    <span className="text-muted block truncate text-xs">{subtitle}</span>
                  )}
                </span>
                {row.excluded && <span className="text-muted text-xs">{t('excluded')}</span>}
                <FitScore score={row.relevance_score} />
              </button>
            </li>
          );
        })}
      </ul>
      <Dialog open={shown && open !== undefined} onOpenChange={setShown}>
        <BottomSheetContent
          aria-describedby={undefined}
          onDismiss={() => setShown(false)}
          className="max-h-[85dvh] gap-3 pb-[max(1rem,env(safe-area-inset-bottom))]"
        >
          {open && (
            <>
              <div className="flex items-center gap-2">
                <DialogTitle className="min-w-0 flex-1 truncate text-base">
                  {cardSummary(open, specs).title}
                </DialogTitle>
                <DialogPrimitive.Close asChild>
                  <Button
                    variant="ghost"
                    size="icon-md"
                    className="size-11"
                    aria-label={t('close')}
                  >
                    <X className="size-5" />
                  </Button>
                </DialogPrimitive.Close>
              </div>
              <dl className="grid gap-3 text-sm">
                {specs.map((spec) => (
                  <div key={spec.key} className="min-w-0">
                    <dt className="text-muted text-xs">{spec.label}</dt>
                    <dd className="break-words">
                      <Cell spec={spec} row={open} chatHref={chatHref} columns={columns} />
                    </dd>
                  </div>
                ))}
              </dl>
              <div className="flex items-center gap-2 border-t pt-2">
                <Button
                  variant="outline"
                  className="h-11 flex-1 font-semibold"
                  onClick={() => onToggleExcluded(open)}
                >
                  {open.excluded ? t('include') : t('exclude')}
                </Button>
                {onRate && (
                  <span className="[&_button]:size-11">
                    <RateToggles row={open} onRate={onRate} />
                  </span>
                )}
              </div>
            </>
          )}
        </BottomSheetContent>
      </Dialog>
    </>
  );
}
