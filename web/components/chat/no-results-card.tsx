'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { useId } from 'react';
import type { AgentQuestion, Funnel } from '@/lib/chat/types';
import { cn } from '@/lib/utils';
import { OptionCards } from './option-cards';

const MIN_BAR_PERCENT = 2;

function FunnelSteps({ funnel }: { funnel: Funnel }) {
  const t = useTranslations('chat.noResults');
  const format = useFormatter();
  const widest = Math.max(1, ...funnel.steps.map((s) => s.count));
  return (
    <ol aria-label={t('funnelLabel', { entity: funnel.entity })} className="flex flex-col gap-1">
      {funnel.steps.map((step, i) => {
        const excluded = i === funnel.excludedIndex;
        const values = format.list(step.values, { type: 'disjunction' });
        const percent = Math.max(MIN_BAR_PERCENT, Math.round((step.count / widest) * 100));
        return (
          <li
            key={step.group}
            aria-current={excluded ? 'step' : undefined}
            className="text-ui flex min-h-5.5 items-center gap-3 phone:flex-wrap phone:gap-x-3 phone:gap-y-1"
          >
            <span
              className={cn('w-60 shrink-0 phone:w-full', excluded ? 'text-warning' : 'text-ink')}
            >
              {t(`step.${step.group}`, { entity: funnel.entity, values })}
            </span>
            <span className="bg-hair h-1.5 min-w-0 flex-1 overflow-hidden rounded-full">
              <span
                className={cn('block h-1.5 rounded-full', excluded ? 'bg-warning' : 'bg-accent')}
                style={{ width: `${percent}%` }}
              />
            </span>
            <span
              className={cn(
                'text-data w-14 shrink-0 text-right font-mono tabular-nums',
                excluded ? 'text-warning' : 'text-ink',
              )}
            >
              {format.number(step.count, 'credits')}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function NoResultsCard({
  question,
  funnel,
  creditsUsed,
  onAnswer,
}: {
  question: AgentQuestion;
  funnel: Funnel;
  creditsUsed: number;
  onAnswer?: (text: string, pick: number) => void;
}) {
  const t = useTranslations('chat.noResults');
  const titleId = useId();
  const options = question.options.map((o) =>
    o.drop
      ? { ...o, label: t(`drop.${o.drop}.label`), description: t(`drop.${o.drop}.description`) }
      : o,
  );
  return (
    <section
      aria-labelledby={titleId}
      className="border-line bg-raised flex flex-col gap-2.5 rounded-lg border p-4"
    >
      <h2 id={titleId} className="text-ui-strong">
        {t('title', { entity: funnel.entity })}
      </h2>
      <p className="text-ui text-soft">
        {t(creditsUsed === 0 ? 'leadFree' : 'lead', { entity: funnel.entity })}
      </p>
      <FunnelSteps funnel={funnel} />
      {onAnswer && (
        <div className="border-hair flex flex-col gap-2.5 border-t pt-2.5">
          {options.length > 0 && (
            <>
              <span className="text-label text-muted uppercase">{t('proceed')}</span>
              <OptionCards options={options} onPick={(o, i) => onAnswer(o.label, i)} />
            </>
          )}
          <span className="text-small text-muted">{t('hint')}</span>
        </div>
      )}
    </section>
  );
}
