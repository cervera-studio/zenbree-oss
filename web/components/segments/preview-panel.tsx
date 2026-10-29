'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Panel } from '@/components/ui/panel';
import { useErrorText } from '@/i18n/error-text';
import type { SegmentPreview } from '@/lib/segments/api';
import type { FieldCatalog } from '@/lib/segments/fields';
import type { Entity } from '@/lib/segments/rule';
import type { AppErrorJSON } from '@/lib/types';
import { cn } from '@/lib/utils';
import { RowName } from './row-name';
import { WhyButton } from './why-drawer';

export const PAUSED = 'SEGMENT.EVALUATION_PAUSED';

export function PausedNotice() {
  const t = useTranslations('errors.SEGMENT');
  return (
    <p role="status" className="bg-warning-tint text-ink text-ui rounded-md p-3">
      {t('EVALUATION_PAUSED')}
    </p>
  );
}

export function PreviewPanel({
  preview,
  fresh,
  checking,
  failed,
  sendable,
  entity,
  why,
}: {
  entity: Entity;
  preview: SegmentPreview | null;
  fresh: boolean;
  checking: boolean;
  failed: AppErrorJSON | null;
  sendable: boolean;
  why: { segmentId: string; catalog: FieldCatalog } | null;
}) {
  const t = useTranslations('segments.preview');
  const errorText = useErrorText();
  return (
    <Panel
      title={t('title')}
      action={
        checking && <Loader2 aria-label={t('checking')} className="text-soft size-4 animate-spin" />
      }
    >
      <div className="flex flex-col gap-4">
        {!sendable && <p className="text-ink text-ui">{t('incomplete')}</p>}
        {failed?.code === PAUSED ? (
          <PausedNotice />
        ) : failed?.code === 'SEGMENT.INVALID_RULE' ? (
          <p className="text-ink text-ui">{t('invalid')}</p>
        ) : (
          failed && (
            <p role="alert" className="text-negative text-ui">
              {errorText(failed, t('failed'))}
            </p>
          )
        )}
        {preview && (
          <div
            aria-busy={!fresh || undefined}
            className={cn('flex flex-col gap-4', !fresh && 'opacity-60')}
          >
            <p role="status" className="text-ink">
              <span className="text-title font-mono tabular-nums break-words">
                {t('count', { n: preview.count, entity })}
              </span>
            </p>
            <dl className="text-ui flex flex-col gap-1">
              <Fact label={t('pending')} n={preview.pending} />
              <Fact label={t('unidentified')} n={preview.unidentified_rows} />
              <Fact label={t('linking')} n={preview.linking_rows} />
            </dl>
            {preview.sample.length > 0 && (
              <section className="flex flex-col gap-1.5">
                <h3 className="text-ui-strong">{t('sample')}</h3>
                <ul className="flex flex-col">
                  {preview.sample.map((s) => (
                    <li
                      key={s.identity_id}
                      className="border-line flex min-h-11 items-center justify-between gap-2 border-b last:border-b-0"
                    >
                      <RowName row={s.row} />
                      {why && <WhyButton {...why} identityId={s.identity_id} />}
                    </li>
                  ))}
                </ul>
                {!why && <p className="text-ink text-small">{t('whyAfterSave')}</p>}
              </section>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}

function Fact({ label, n }: { label: string; n: number }) {
  const t = useTranslations('segments.preview');
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-soft">{label}</dt>
      <dd className="text-ink font-mono tabular-nums">{t('number', { n })}</dd>
    </div>
  );
}
