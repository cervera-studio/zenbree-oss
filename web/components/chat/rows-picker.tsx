'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { usePhone } from '@/components/app/use-phone';
import { Input } from '@/components/ui/input';
import { MAX_PLAN_ROWS, rowPresets } from '@/lib/chat/plan';
import { cn } from '@/lib/utils';

/** The plan's row count: three presets as a segmented control, and a field for any other number. */
export function RowsPicker({
  id,
  draft,
  rows,
  valid,
  disabled,
  companyRows,
  targetRows,
  onChange,
}: {
  id: string;
  draft: string;
  rows: number;
  valid: boolean;
  disabled: boolean;
  companyRows: number | null;
  targetRows: number;
  onChange: (draft: string) => void;
}) {
  const t = useTranslations('chat.plan');
  const format = useFormatter();
  const phone = usePhone();
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <span aria-hidden className="text-ui-strong phone:sr-only">
        {t('rows')}
      </span>
      <fieldset className="border-line bg-raised inline-flex gap-0.5 rounded-md border p-0.5 phone:grid phone:flex-1 phone:grid-flow-col phone:auto-cols-fr">
        <legend className="sr-only">{t('rows')}</legend>
        {rowPresets(targetRows).map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={valid && rows === n}
            disabled={disabled}
            onClick={() => onChange(String(n))}
            className={cn(
              'text-data-sm text-muted aria-pressed:border-line aria-pressed:bg-ground aria-pressed:text-ink h-5.5 min-w-10.5 rounded-sm border border-transparent px-2 font-mono whitespace-nowrap tabular-nums phone:h-10',
              'focus-visible:ring-accent/50 outline-none focus-visible:ring-3',
            )}
          >
            {phone ? t('rowsPreset', { count: n }) : format.number(n, 'credits')}
          </button>
        ))}
      </fieldset>
      <Input
        id={id}
        type="number"
        inputMode="numeric"
        min={1}
        max={MAX_PLAN_ROWS}
        value={draft}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        aria-invalid={!valid}
        aria-label={t('rowsOther')}
        size="sm"
        className="w-24 tabular-nums phone:h-11 phone:w-full"
      />
      {companyRows ? (
        <span className="text-small text-muted">{t('rowsFound', { count: companyRows })}</span>
      ) : null}
    </div>
  );
}
