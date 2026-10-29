'use client';

import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { ListDetail } from '@/lib/lists/api';
import { moreRowsCredits } from '@/lib/lists/view';

const BATCHES = [25, 50, 100] as const;

/** The chat with `message` waiting in its composer: the user reads it and sends it. */
export const draftHref = (chatHref: string, message: string) =>
  `${chatHref}&draft=${encodeURIComponent(message)}`;

/** «Altre righe»: how many, what they can cost, then the list's chat with the request drafted. */
export function MoreRowsDialog({
  list,
  chatHref,
  balance,
  open,
  onOpenChange,
}: {
  list: ListDetail;
  chatHref: string;
  /** The organization's credits when the page loaded; null when unknown. */
  balance: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations('lists');
  const tc = useTranslations('common');
  const format = useFormatter();
  const [count, setCount] = useState<number>(50);
  const credits = moreRowsCredits(list.prices, list.columns ?? [], count);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined} className="gap-4 sm:max-w-130">
        <DialogHeader>
          <DialogTitle className="text-lg font-bold">{t('moreRows')}</DialogTitle>
          <p className="text-muted text-sm leading-normal">{t('moreRowsBody')}</p>
        </DialogHeader>
        <div className="flex items-center gap-3">
          <span id="more-rows-count" className="text-xs-plus font-semibold">
            {t('moreRowsCount')}
          </span>
          <div
            role="radiogroup"
            aria-labelledby="more-rows-count"
            className="bg-raised inline-flex gap-0.5 rounded-md p-0.75"
          >
            {BATCHES.map((n) => (
              <label
                key={n}
                className="text-muted has-checked:bg-ground has-checked:text-ink has-focus-visible:ring-accent flex h-8 min-w-13 cursor-pointer items-center justify-center rounded-sm px-3 text-xs-plus font-semibold tabular-nums has-checked:shadow-xs has-focus-visible:ring-2"
              >
                <input
                  type="radio"
                  name="more-rows-count"
                  className="sr-only"
                  checked={n === count}
                  onChange={() => setCount(n)}
                />
                {format.number(n, 'credits')}
              </label>
            ))}
          </div>
        </div>
        <div className="bg-ground flex justify-between gap-3 rounded-lg border p-3 text-xs-plus tabular-nums">
          <span>
            {t.rich('moreRowsCost', { credits, b: (chunks) => <strong>{chunks}</strong> })}
          </span>
          {balance !== null && (
            <span className="text-muted">{t('moreRowsBalance', { balance })}</span>
          )}
        </div>
        {list.findable != null && (
          <p className="text-muted text-xs-plus">
            {t(list.entity === 'company' ? 'moreRowsFindableCompanies' : 'moreRowsFindable', {
              count: list.findable,
            })}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tc('cancel')}
          </Button>
          <Button asChild>
            <Link href={draftHref(chatHref, t('moreRowsDraft', { count, id: list.id }))}>
              {t('moreRowsCta', { count })}
            </Link>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
