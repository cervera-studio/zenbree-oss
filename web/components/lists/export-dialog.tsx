'use client';

import { Check } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import {
  type ExportFilters,
  type ExportFormat,
  type ExportRows,
  type List,
  type ListRow,
  listsApi,
} from '@/lib/lists/api';
import { toColumnSpecs } from '@/lib/lists/columns';
import { exportRowCounts } from '@/lib/lists/view';
import { cn } from '@/lib/utils';

const FORMATS = ['csv', 'xlsx', 'hubspot'] as const;
const LEGEND = 'text-small mb-2 flex w-full gap-2';
const OPTION =
  'has-checked:border-accent has-checked:bg-accent-tint border-line bg-ground has-focus-visible:ring-accent flex cursor-pointer rounded-lg border px-3 py-2.5 has-focus-visible:ring-2';

/**
 * Free download of the delivered rows; the API never includes excluded contacts. `rows` (the
 * list's loaded rows) lets the dialog show how many rows each choice holds.
 */
export function ExportDialog({
  list,
  rows: loaded,
  onClose,
}: {
  list: List;
  rows?: ListRow[];
  onClose: () => void;
}) {
  const t = useTranslations('lists');
  const tc = useTranslations('common');
  const specs = toColumnSpecs(list.columns ?? [], t);
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [rows, setRows] = useState<ExportRows>('all');
  const [filters, setFilters] = useState<ExportFilters>({
    verified_only: false,
    hide_off_target: false,
  });
  const [columns, setColumns] = useState(() => new Set(specs.map((s) => s.key)));
  const [state, setState] = useState<'idle' | 'busy' | 'failed'>('idle');
  // Once a file was saved the dialog stays on «File pronto», also while it downloads again.
  const [ready, setReady] = useState(false);
  const rowChoices: ExportRows[] =
    list.entity === 'person' ? ['all', 'high_fit', 'with_email'] : ['all', 'high_fit'];
  const counts = loaded && exportRowCounts(loaded, filters);
  const count = counts?.[rows];

  const toggle = (key: string, on: boolean) =>
    setColumns((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });

  const keys = specs.map((s) => s.key).filter((k) => columns.has(k));
  const download = async () => {
    setState('busy');
    const ok = await listsApi.download(list.id, list.name, {
      format,
      rows,
      columns: keys,
      ...filters,
    });
    setState(ok ? 'idle' : 'failed');
    if (ok) setReady(true);
  };

  const failedAlert = state === 'failed' && (
    <div role="alert" className="bg-negative-tint text-ui flex gap-2.5 rounded-lg p-3.5">
      <span aria-hidden="true" className="bg-negative mt-1.75 size-2 shrink-0 rounded-full" />
      <span>
        <strong className="text-negative">{t('exportErrorTitle')}</strong> {t('exportErrorBody')}
      </span>
    </div>
  );

  const cta =
    columns.size === 0
      ? t('exportNoColumns')
      : state === 'failed'
        ? t('retry')
        : count === undefined
          ? t('exportDownload')
          : t('exportCta', { count });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        className="max-h-11/12 gap-0 overflow-y-auto rounded-xl p-0 sm:max-w-140"
      >
        <DialogHeader className="border-hair gap-0.5 border-b px-4 py-3.5 pr-14">
          <DialogTitle className="text-feature">{t('exportTitle')}</DialogTitle>
          <p className="text-muted text-small">
            {t('exportSubtitle', {
              name: list.name,
              count: counts?.all ?? list.delivered_count,
            })}
          </p>
        </DialogHeader>
        {ready ? (
          <div className="flex flex-col gap-4 p-4">
            <div role="status" className="flex items-start gap-3">
              <span className="bg-positive-tint text-positive flex size-control-md shrink-0 items-center justify-center rounded-full">
                <Check className="size-4.5" aria-hidden="true" />
              </span>
              <div className="flex flex-col gap-1.5">
                <strong className="text-feature">{t('exportReady')}</strong>
                <span className="text-data font-mono break-all">{`${list.name}.${format}`}</span>
                <span className="text-muted text-small">
                  {count === undefined
                    ? t('exportReadyColumns', { columns: keys.length })
                    : t('exportReadyMeta', { rows: count, columns: keys.length })}
                </span>
              </div>
            </div>
            {failedAlert}
          </div>
        ) : (
          <div className="flex flex-col gap-3.5 px-4 pt-4">
            <fieldset>
              <legend className={LEGEND}>{t('exportFormat')}</legend>
              <div className="grid grid-cols-3 gap-2 phone:grid-cols-1">
                {FORMATS.map((f) => (
                  <label
                    key={f}
                    className={cn(
                      OPTION,
                      'flex-col gap-0.5 has-disabled:cursor-not-allowed has-disabled:opacity-55',
                    )}
                  >
                    <input
                      type="radio"
                      name="export-format"
                      className="sr-only"
                      disabled={f === 'hubspot'}
                      checked={format === f}
                      onChange={() => f !== 'hubspot' && setFormat(f)}
                    />
                    <span className="text-ui-strong flex items-center gap-1.5">
                      {t(`exportFormats.${f}`)}
                      {f === 'hubspot' && <Tag tone="neutral">{t('exportSoon')}</Tag>}
                    </span>
                    <span className="text-muted text-small">{t(`exportFormatHint.${f}`)}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className={LEGEND}>{t('exportRows')}</legend>
              {rowChoices.map((r) => (
                <label key={r} className={cn(OPTION, 'text-ui mb-1.5 items-center gap-2.5')}>
                  <input
                    type="radio"
                    name="export-rows"
                    className="accent-accent size-4"
                    checked={rows === r}
                    onChange={() => setRows(r)}
                  />
                  <span className="grow">{t(`exportRowChoices.${r}`)}</span>
                  {counts && (
                    <span className="text-data text-ink font-mono tabular-nums">{counts[r]}</span>
                  )}
                </label>
              ))}
              {(['verified_only', 'hide_off_target'] as const)
                .filter((f) => f !== 'verified_only' || list.entity === 'person')
                .map((f) => (
                  <Label
                    key={f}
                    className="text-ui mt-1 min-h-8 cursor-pointer gap-2.5 font-normal"
                  >
                    <Checkbox
                      checked={filters[f]}
                      onCheckedChange={(on) =>
                        setFilters((prev) => ({ ...prev, [f]: on === true }))
                      }
                    />
                    {t(`exportFilters.${f}`)}
                  </Label>
                ))}
            </fieldset>
            <fieldset>
              <legend className={LEGEND}>
                {t('exportColumns')}
                <span className="text-muted">
                  {t('exportColumnsCount', { selected: columns.size, total: specs.length })}
                </span>
              </legend>
              <div className="flex max-h-56 flex-wrap gap-1.5 overflow-auto">
                {specs.map((s) => (
                  <Label
                    key={s.key}
                    className="text-ui border-line bg-ground has-data-[state=checked]:border-accent-tint has-data-[state=checked]:bg-accent-tint has-data-[state=checked]:text-accent-ink h-control-sm cursor-pointer gap-1.5 rounded-md border px-2 font-normal"
                  >
                    <Checkbox
                      checked={columns.has(s.key)}
                      onCheckedChange={(on) => toggle(s.key, on === true)}
                    />
                    {s.label}
                    {s.render === 'ai' && <Tag tone="accent">{t('exportAi')}</Tag>}
                  </Label>
                ))}
              </div>
            </fieldset>
            <p className="text-muted text-small">{t('exportNote')}</p>
            {failedAlert}
          </div>
        )}
        <DialogFooter className="border-hair bg-raised mt-4 items-center rounded-b-xl border-t px-4 py-3">
          <Button variant="outline" onClick={onClose}>
            {ready ? t('close') : tc('cancel')}
          </Button>
          <Button
            onClick={download}
            disabled={state === 'busy' || columns.size === 0 || count === 0}
          >
            {ready && state !== 'failed' ? t('exportAgain') : cta}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
