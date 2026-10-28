'use client';

import { Check } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import type { Schema } from '@/lib/api/schema';
import { csvImportApi } from '@/lib/lists/csv-import';
import { cn } from '@/lib/utils';

/** The worker is importing: the dialog can close, the import goes on (#344). */
export function ProgressStep({ imp }: { imp: Schema<'CsvImportState'> }) {
  const t = useTranslations('lists');
  const pct = imp.total ? Math.min(100, Math.round((imp.processed / imp.total) * 100)) : 0;
  return (
    <div className="flex flex-col gap-3 px-4 pt-5 pb-1">
      <p className="text-ui-strong">{imp.name}</p>
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="bg-hair h-1 overflow-hidden rounded-xs"
      >
        <div
          className={cn('bg-accent h-full transition-all', !imp.total && 'w-1/3 animate-pulse')}
          style={imp.total ? { width: `${pct}%` } : undefined}
        />
      </div>
      <p className="text-muted text-ui">
        {imp.total
          ? t('importProgress', { done: imp.processed, total: imp.total })
          : t('importQueued')}
      </p>
    </div>
  );
}

/** How the import ended: the counts, the discarded rows to download, and the way into the chat. */
export function DoneStep({ imp }: { imp: Schema<'CsvImportState'> }) {
  const t = useTranslations('lists');
  const errorText = useErrorText();
  const failed = imp.status === 'failed';

  const reasons = [
    ['duplicates', imp.duplicates],
    ['inLists', imp.in_lists],
    ['excluded', imp.excluded],
    ['skipped', imp.skipped],
  ] as const;

  async function download() {
    if (!(await csvImportApi.downloadDiscarded(imp.id, imp.name)))
      toast.error(t('importDiscardedError'));
  }

  return (
    <div className="flex flex-col gap-3.5 px-4 pt-5 pb-1">
      {failed ? (
        <p role="alert" className="text-negative text-ui-strong">
          {errorText({ code: imp.error }, t('importFailed'))}
        </p>
      ) : (
        <p className="text-positive text-ui-strong flex items-center gap-2">
          <Check aria-hidden="true" className="size-4" />
          {t('importFinished', { added: imp.rows_added })}
        </p>
      )}
      {imp.discarded > 0 && (
        <div className="bg-raised border-line text-ui flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2.5">
          <span className="grow">
            {t('importDiscardedSummary')}{' '}
            {reasons
              .filter(([, n]) => n > 0)
              .map(([reason, count]) => t(`importDiscardedReason.${reason}`, { count }))
              .join(', ')}
          </span>
          <Button variant="outline" size="sm" onClick={() => void download()}>
            {t('importDiscardedDownload', { count: imp.discarded })}
          </Button>
        </div>
      )}
    </div>
  );
}
