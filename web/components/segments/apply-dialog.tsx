'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useEffect, useId, useState } from 'react';
import { previewAction } from '@/app/(app)/segments/actions';
import { Checkbox } from '@/components/ui/checkbox';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Label } from '@/components/ui/label';
import { useErrorText } from '@/i18n/error-text';
import { unreachable } from '@/lib/api/error';
import type { SegmentPreview } from '@/lib/segments/api';
import type { Entity, RuleGroup } from '@/lib/segments/rule';
import { type AppErrorJSON, fail } from '@/lib/types';
import { PAUSED, PausedNotice } from './preview-panel';

type Answer = { preview: SegmentPreview } | { error: AppErrorJSON } | null;

export function ApplyDialog({
  kind,
  onClose,
  segmentId,
  entity,
  rule,
  onConfirm,
}: {
  kind: 'publish' | 'edit';
  onClose: () => void;
  segmentId: string;
  entity: Entity;
  rule: RuleGroup;
  onConfirm: (include: boolean) => Promise<void>;
}) {
  const t = useTranslations('segments.apply');
  const tc = useTranslations('segments.cost');
  const errorText = useErrorText();
  const id = useId();
  const [answer, setAnswer] = useState<Answer>(null);
  const [include, setInclude] = useState(false);
  const request = JSON.stringify({ entity, rule, segment_id: segmentId });

  useEffect(() => {
    let live = true;
    previewAction(JSON.parse(request))
      .catch(() => fail(unreachable()))
      .then((result) => {
        if (live) setAnswer(result.success ? { preview: result.data } : { error: result.error });
      });
    return () => {
      live = false;
    };
  }, [request]);

  const preview = answer && 'preview' in answer ? answer.preview : null;
  const joining = kind === 'publish' ? (preview?.count ?? 0) : (preview?.joining ?? 0);
  const credits = preview?.max_credits ?? 0;
  return (
    <ConfirmDialog
      open
      onOpenChange={(next) => !next && onClose()}
      destructive={false}
      title={t(`${kind}.title`)}
      description={t(`${kind}.description`)}
      confirmLabel={t(`${kind}.confirm`)}
      confirmDisabled={!preview}
      onConfirm={() => onConfirm(include && joining > 0)}
    >
      {!answer && (
        <p role="status" className="text-ink text-ui flex items-center gap-2">
          <Loader2 aria-hidden className="size-4 animate-spin" />
          {t('counting')}
        </p>
      )}
      {answer &&
        'error' in answer &&
        (answer.error.code === PAUSED ? (
          <PausedNotice />
        ) : (
          <p role="alert" className="text-negative text-ui">
            {errorText(answer.error)}
          </p>
        ))}
      {preview && (
        <div className="flex flex-col gap-3">
          <dl className="text-ui flex flex-col gap-1">
            {kind === 'publish' ? (
              <Row label={t('matching')} value={t('people', { n: preview.count, entity })} />
            ) : (
              <>
                <Row
                  label={t('joining')}
                  value={t('people', { n: preview.joining ?? 0, entity })}
                />
                <Row
                  label={t('leaving')}
                  value={t('people', { n: preview.leaving ?? 0, entity })}
                />
              </>
            )}
            <Row label={tc('max')} value={tc('credits', { n: credits })} />
          </dl>
          {credits === 0 && <p className="text-ink text-small">{tc('noAutomation')}</p>}
          {joining > 0 && (
            <div className="flex items-start gap-2.5">
              <Checkbox
                id={id}
                className="mt-1"
                checked={include}
                onCheckedChange={(v) => setInclude(v === true)}
              />
              <div className="flex flex-col gap-0.5">
                <Label htmlFor={id} className="text-ink text-ui">
                  {t(`${kind}.include`, { n: joining, entity })}
                </Label>
                <p className="text-ink text-small">{t('silent', { n: joining, entity })}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </ConfirmDialog>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-soft">{label}</dt>
      <dd className="text-ink font-mono tabular-nums">{value}</dd>
    </div>
  );
}
