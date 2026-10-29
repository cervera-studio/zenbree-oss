'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useErrorText } from '@/i18n/error-text';
import type { Estimate } from '@/lib/campaigns/snippet';

export function SnippetEstimate({ estimate, price }: { estimate: Estimate; price: number }) {
  const t = useTranslations('campaignBuilder.snippet');
  const errorText = useErrorText();
  if (estimate.status === 'idle') return null;
  if (estimate.status === 'loading') {
    return (
      <p role="status" className="text-muted text-small flex items-center gap-2">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        {t('estimating')}
      </p>
    );
  }
  if (estimate.status === 'failed') {
    return (
      <p role="alert" className="text-negative text-small">
        {errorText(estimate.error, t('estimateFailed'))}
      </p>
    );
  }
  if (estimate.researchRows === 0) {
    return <p className="text-muted text-small">{t('estimateNone')}</p>;
  }
  return (
    <div
      role="status"
      className="bg-ground flex flex-col gap-1 rounded-lg border p-3 text-xs-plus leading-normal"
    >
      <span className="tabular-nums">
        <span className="font-bold">{t('estimate', { credits: estimate.credits })}</span>
        <span className="text-muted">
          {' · '}
          {t('estimateDetail', { price, people: estimate.researchRows })}
        </span>
      </span>
      <span className="text-muted">{t('estimateNote')}</span>
    </div>
  );
}
