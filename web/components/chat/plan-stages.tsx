'use client';

import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { canDrop, stageCost, stageUnits } from '@/lib/chat/plan';
import type { AgentPlan } from '@/lib/chat/types';
import { cn } from '@/lib/utils';
import { PlanSection } from './plan-section';

export function PlanStages({
  plan,
  rows,
  dropped,
  onToggle,
}: {
  plan: AgentPlan;
  rows: number;
  dropped: ReadonlySet<number>;
  onToggle?: (index: number) => void;
}) {
  const t = useTranslations('chat.plan');
  const researchColumns = plan.columns.filter((c) => c.research).length;
  const researchCredits = Math.ceil((plan.creditsResearchPerRow ?? 0) * rows - 1e-9);
  const count = plan.stages.length + (researchColumns > 0 ? 1 : 0);
  if (count === 0) return null;

  return (
    <PlanSection title={t('stages')} summary={t('stagesSummary', { count })}>
      <ol className="flex flex-col">
        {plan.stages.map((s, i) => {
          const off = dropped.has(i);
          const cost = stageCost(plan, s, rows);
          return (
            <li
              key={`${i}-${s.tool}`}
              className="text-ui flex min-h-6.5 flex-wrap items-center gap-x-2 gap-y-1"
            >
              <span className="bg-hair text-soft text-data-sm flex size-4.5 shrink-0 items-center justify-center rounded-full font-mono">
                {i + 1}
              </span>
              <span className={cn('text-ui-strong', off && 'text-muted line-through')}>
                {s.label}
              </span>
              {!off && !!s.unitCost && (
                <span className="text-small text-muted">
                  {t('stageHint', { credits: s.unitCost })}
                </span>
              )}
              {canDrop(s) && (
                <span className="text-small text-muted">{off ? t('dropped') : t('optional')}</span>
              )}
              <span className="flex-1" />
              {!off && cost !== null && (
                <span className="text-data-sm text-muted font-mono tabular-nums">
                  {t('stageCost', { credits: cost })}
                </span>
              )}
              {!off && (
                <span className="text-data-sm text-muted font-mono tabular-nums">
                  {t('units', { units: stageUnits(plan, s, rows) })}
                </span>
              )}
              {onToggle && canDrop(s) && off && (
                <Button
                  size="xs"
                  variant="outline"
                  className="phone:h-11"
                  onClick={() => onToggle(i)}
                >
                  {t('restore', { label: s.label })}
                </Button>
              )}
            </li>
          );
        })}
        {researchColumns > 0 && (
          <li className="text-ui flex min-h-6.5 flex-wrap items-center gap-x-2">
            <span className="bg-hair text-soft text-data-sm flex size-4.5 shrink-0 items-center justify-center rounded-full font-mono">
              {plan.stages.length + 1}
            </span>
            <span className="text-ui-strong">{t('researchStage')}</span>
            <span className="text-small text-muted">
              {t('researchNote', { count: researchColumns })}
            </span>
            <span className="flex-1" />
            <span className="text-data-sm text-muted font-mono tabular-nums">
              {t('stageCost', { credits: researchCredits })}
            </span>
            <span className="text-data-sm text-muted font-mono tabular-nums">
              {t('cells', { units: rows * researchColumns })}
            </span>
          </li>
        )}
      </ol>
    </PlanSection>
  );
}
