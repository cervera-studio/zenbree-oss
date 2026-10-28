'use client';

import { Check, Search } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { chipClass } from '@/components/ui/filter-chip';
import { Input } from '@/components/ui/input';
import { tabClass } from '@/components/ui/tab-links';
import { FIT_MIN, type RowTab, shownTabs } from '@/lib/lists/view';
import { cn } from '@/lib/utils';

/** Tutte / Consegnate / Da completare, then Senza contatto and Escluse while they hold a row. */
export function RowTabs({
  counts,
  value,
  onChange,
  pill = false,
}: {
  counts: Record<RowTab, number>;
  value: RowTab;
  onChange: (tab: RowTab) => void;
  /** Phone: separate outlined pills instead of one segmented bar. */
  pill?: boolean;
}) {
  const t = useTranslations('lists');
  const format = useFormatter();
  return (
    <fieldset
      aria-label={t('tabsLabel')}
      className={
        pill
          ? 'm-0 flex min-w-0 flex-wrap gap-1.5 border-0 p-0'
          : 'border-hair m-0 flex w-full min-w-0 gap-4.5 border-0 border-b p-0'
      }
    >
      {shownTabs(counts).map((tab) => {
        const on = tab === value;
        return (
          <button
            key={tab}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(tab)}
            className={
              pill
                ? cn(
                    'focus-visible:outline-accent-ink text-ui-strong h-9 shrink-0 rounded-full border px-3.5 whitespace-nowrap focus-visible:outline-2',
                    on && 'border-accent-ink bg-accent-tint text-accent-ink',
                  )
                : tabClass(on)
            }
          >
            {t(`tab.${tab}`)}{' '}
            <span className={cn(!pill && 'text-data-sm text-muted font-normal')}>
              {format.number(counts[tab], 'credits')}
            </span>
          </button>
        );
      })}
    </fieldset>
  );
}

export function RowSearch({
  value,
  onChange,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  const t = useTranslations('lists');
  return (
    <div className={cn('relative', className)}>
      <Search
        className="text-muted pointer-events-none absolute top-1/2 left-3 size-3.75 -translate-y-1/2"
        aria-hidden="true"
      />
      <Input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== 'Escape') return;
          // Escape belongs to the search while it has focus: clear it, then leave it.
          e.preventDefault();
          if (value) onChange('');
          else e.currentTarget.blur();
        }}
        placeholder={t('searchPlaceholder')}
        aria-label={t('filterSearch')}
        className="pl-8.5 phone:h-11"
        size="sm"
      />
    </div>
  );
}

export function FitChip({
  minFit,
  onChange,
}: {
  minFit: number | null;
  onChange: (minFit: number | null) => void;
}) {
  const t = useTranslations('lists.bulk');
  const on = minFit !== null;
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={() => onChange(on ? null : FIT_MIN)}
      className={cn(chipClass(on), 'phone:h-9 phone:justify-center')}
    >
      {on && <Check className="size-3" aria-hidden="true" />}
      {t('fitChip', { min: minFit ?? FIT_MIN })}
    </button>
  );
}
