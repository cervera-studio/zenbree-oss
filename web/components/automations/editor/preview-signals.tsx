'use client';

import { useFormatter, useTranslations } from 'next-intl';
import type { Preview } from '@/lib/automations/editor-api';

/** The companies whose recent signals reach the minimum: what the trigger would have started, never what it will. */
export function PreviewSignals({ signals }: { signals: NonNullable<Preview['signals']> }) {
  const t = useTranslations('automationEditor.preview.signals');
  const format = useFormatter();
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-ui-strong">{t('title')}</h3>
      <p className="text-ui">{t('accounts', { n: signals.accounts })}</p>
      {signals.recent.length > 0 && (
        <ul className="text-ui flex flex-col gap-2">
          {signals.recent.map((account) => (
            <li key={account.accountId} className="flex flex-col">
              <span className="flex items-baseline justify-between gap-3">
                <span className="text-ink min-w-0 break-words">{account.name ?? t('noName')}</span>
                <span className="text-soft shrink-0 font-mono tabular-nums">
                  {t('score', { n: Math.round(account.score) })}
                </span>
              </span>
              <span className="text-small text-muted break-words">
                {t('seen', {
                  title: account.title ?? t('noTitle'),
                  when: format.dateTime(new Date(account.seenAt), { dateStyle: 'medium' }),
                })}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-small text-muted">{t('fromNow')}</p>
    </section>
  );
}
