'use client';

import { Check } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { Schema } from '@/lib/api/schema';
import { cn } from '@/lib/utils';

export function ReviewStep({
  entity,
  preview,
  name,
  onName,
  findEmails,
  onFindEmails,
}: {
  entity: 'company' | 'person';
  preview: Schema<'CsvPreview'>;
  name: string;
  onName: (name: string) => void;
  findEmails: boolean;
  onFindEmails: (on: boolean) => void;
}) {
  const t = useTranslations('lists');
  const format = useFormatter();
  const counts = [
    ['toImport', preview.to_import],
    ['inLists', preview.in_lists],
    ['duplicates', preview.duplicates],
    ['excluded', preview.excluded],
    ['skipped', preview.skipped],
  ] as const;
  return (
    <div className="flex flex-col gap-3.5 px-4 pt-4">
      <div className="flex flex-col gap-2">
        <dl className="border-line bg-hair grid grid-cols-5 gap-px overflow-hidden rounded-lg border phone:grid-cols-2">
          {counts.map(([key, n], i) => (
            <div
              key={key}
              className="bg-ground flex flex-col-reverse justify-between gap-0.5 px-3 py-2.5"
            >
              <dt className="text-label text-muted uppercase">{t(`importCounts.${key}`)}</dt>
              <dd className={cn('text-kpi font-mono tabular-nums', i === 0 && 'text-positive')}>
                {format.number(n, 'credits')}
              </dd>
            </div>
          ))}
        </dl>
        <p className="text-muted text-small">{t('importDropped')}</p>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="csv-list-name" className="text-small font-normal">
          {t('importName')}
        </Label>
        <Input
          id="csv-list-name"
          value={name}
          maxLength={120}
          onChange={(e) => onName(e.target.value)}
        />
      </div>
      {entity === 'person' && preview.incomplete > 0 && (
        <Label
          className={cn(
            'border-line bg-ground flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 font-normal',
            findEmails && 'border-accent bg-accent-tint',
          )}
        >
          <Checkbox
            className="mt-0.5"
            checked={findEmails}
            onCheckedChange={(on) => onFindEmails(on === true)}
          />
          <span className="grid gap-1">
            <span className="text-ui-strong">{t('findEmails')}</span>
            <span className="text-muted text-small">
              {t('findEmailsHint', {
                missing: preview.incomplete,
                total: preview.to_import,
                credits: preview.incomplete * preview.prices.email,
                per: preview.prices.email,
              })}
            </span>
          </span>
        </Label>
      )}
      <p className="text-positive text-ui-strong flex items-center gap-2">
        <Check aria-hidden="true" className="size-4" />
        {t('importFree')}
      </p>
    </div>
  );
}
