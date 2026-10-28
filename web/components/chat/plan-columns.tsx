'use client';

import { useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import type { AgentPlan } from '@/lib/chat/types';
import { PlanSection } from './plan-section';

export function PlanColumns({ columns }: { columns: AgentPlan['columns'] }) {
  const t = useTranslations('chat.plan');
  return (
    <PlanSection
      title={t('columns')}
      summary={t('columnsSummary', {
        columns: columns.length,
        required: columns.filter((c) => c.required).length,
        ai: columns.filter((c) => c.research).length,
      })}
    >
      <ul className="grid gap-x-8 desktop:grid-cols-2">
        {columns.map((c) => (
          <li
            key={c.key}
            className="border-hair flex h-7.5 items-center gap-2 overflow-hidden border-b whitespace-nowrap"
          >
            <span className="text-ui-strong">
              {c.label || c.key}
              {c.required && <span className="sr-only"> ({t('required')})</span>}
            </span>
            {c.required && (
              <span aria-hidden className="text-ui text-accent-ink">
                *
              </span>
            )}
            <span className="text-small text-muted min-w-0 flex-1 truncate">
              {c.research ?? c.description}
            </span>
            {c.research && <Tag tone="accent">{t('research')}</Tag>}
          </li>
        ))}
      </ul>
      {columns.some((c) => c.required) && (
        <p className="text-small text-muted mt-2">{t('requiredHint')}</p>
      )}
      {columns.some((c) => c.research) && (
        <p className="text-small text-muted mt-1">{t('researchHint')}</p>
      )}
    </PlanSection>
  );
}
