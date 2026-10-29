'use client';

import { Check, Pencil } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { usePhone } from '@/components/app/use-phone';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  CONFIRM_CREDITS,
  droppedColumns,
  droppedStages,
  MAX_PLAN_ROWS,
  planBudget,
  type TopUpPack,
} from '@/lib/chat/plan';
import type { AgentPlan } from '@/lib/chat/types';
import { cn } from '@/lib/utils';
import { PlanColumns } from './plan-columns';
import { PlanConfirm } from './plan-confirm';
import { PaymentBox, ShortfallBar, ShortfallFixes } from './plan-credits';
import { PlanStages } from './plan-stages';
import { RowsPicker } from './rows-picker';

/**
 * The plan a large run paused on (#44): columns with their required flags, the
 * paid stages, and a row count whose estimate updates before launch (#47).
 * Read-only once the user moved on (no `onApprove`).
 */
export function PlanCard({
  plan,
  balance,
  topUpPack = null,
  companyRows,
  onApprove,
  onEdit,
}: {
  plan: AgentPlan;
  /** Live credit balance, `null` until known. */
  balance: number | null;
  topUpPack?: TopUpPack | null;
  companyRows: number | null;
  onApprove?: (rows: number, credits: number, droppedStages: number[]) => void;
  onEdit?: () => void;
}) {
  const t = useTranslations('chat.plan');
  const rowsId = useId();
  const phone = usePhone();
  const [draft, setDraft] = useState(String(plan.approvedRows ?? plan.targetRows));
  const [dropped, setDropped] = useState<ReadonlySet<number>>(() => droppedStages(plan));
  const [confirming, setConfirming] = useState(false);
  const rows = Number(draft);
  const valid = Number.isInteger(rows) && rows >= 1 && rows <= MAX_PLAN_ROWS;
  const shownRows = valid ? rows : plan.targetRows;
  const answered = onApprove === undefined;
  const { ceiling, missing: gap, after } = planBudget(plan, shownRows, dropped, balance);
  const missing = answered ? 0 : gap;
  const blocked = missing > 0;
  const largeAsk = !answered && ceiling > CONFIRM_CREDITS;
  const hiddenColumns = droppedColumns(plan, dropped);
  const columns = plan.columns.filter((c) => !hiddenColumns.has(c.key));
  const droppedList = [...dropped].sort((a, b) => a - b);
  const approve = () => onApprove?.(rows, ceiling, droppedList);
  const toggleStage = (index: number) =>
    setDropped((prev) => {
      const next = new Set(prev);
      if (!next.delete(index)) next.add(index);
      return next;
    });

  const actions = onApprove && (
    <div className="flex gap-2 phone:sticky phone:bottom-0 phone:border-t phone:bg-ground phone:p-3">
      {onEdit && (
        <Button variant="outline" size="sm" className="phone:h-11" onClick={onEdit}>
          <Pencil aria-hidden />
          {t('edit')}
        </Button>
      )}
      <Button
        size="sm"
        className="phone:h-11 phone:flex-1"
        disabled={!valid || blocked}
        onClick={() => (ceiling > CONFIRM_CREDITS ? setConfirming(true) : approve())}
      >
        <Check aria-hidden />
        {t('approve')}
      </Button>
    </div>
  );

  return (
    <div className="flex flex-col gap-3.5">
      <section
        aria-label={t('title', { version: plan.version })}
        className="border-line bg-ground flex flex-col overflow-clip rounded-lg border"
      >
        <header className="border-line flex flex-col gap-2.5 border-b px-4 py-3">
          <div className="flex flex-wrap items-center gap-2.5">
            <h3 className="text-ui-strong">{t('title', { version: plan.version })}</h3>
            <Tag tone="neutral">{t(`mode.${plan.mode}`)}</Tag>
            <Tag tone="neutral">{t(`entity.${plan.entity}`)}</Tag>
            {plan.entity === 'person' && plan.mode === 'build' && companyRows ? (
              <span className="text-small text-muted desktop:ml-auto">
                {t('linkedList', { count: companyRows })}
              </span>
            ) : null}
          </div>
          <RowsPicker
            id={rowsId}
            draft={draft}
            rows={rows}
            valid={valid}
            disabled={!onApprove}
            companyRows={plan.entity === 'person' && plan.mode === 'build' ? companyRows : null}
            targetRows={plan.targetRows}
            onChange={setDraft}
          />
        </header>

        <PlanColumns columns={columns} />

        <PlanStages
          plan={plan}
          rows={shownRows}
          dropped={dropped}
          onToggle={onApprove ? toggleStage : undefined}
        />

        {!answered && balance !== null && missing === 0 && (
          <PaymentBox balance={balance} credits={ceiling} />
        )}

        <div
          className={cn(
            'flex flex-col gap-2.5 px-4 py-3',
            missing > 0 ? 'bg-warning-tint' : 'bg-raised',
          )}
        >
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="text-ui-strong tabular-nums" aria-live="polite">
                {t('estimate', { credits: ceiling })}
              </span>
              {missing > 0 ? (
                <span className="text-ui-strong text-warning tabular-nums">
                  {t('short.gap', { missing, balance: balance ?? 0 })}
                </span>
              ) : (
                <span className={cn('text-small', largeAsk ? 'text-warning' : 'text-muted')}>
                  {largeAsk ? t('noteLarge', { threshold: CONFIRM_CREDITS }) : t('noteSmall')}
                </span>
              )}
            </div>
            {!phone && actions}
          </div>
          {balance !== null && missing > 0 && <ShortfallBar credits={ceiling} balance={balance} />}
        </div>
        {phone && actions}

        <PlanConfirm
          open={confirming}
          onOpenChange={setConfirming}
          rows={shownRows}
          credits={ceiling}
          after={after}
          onConfirm={approve}
        />
      </section>

      {balance !== null && missing > 0 && (
        <ShortfallFixes
          plan={plan}
          rows={shownRows}
          valid={valid}
          dropped={dropped}
          balance={balance}
          topUpPack={topUpPack}
          onDrop={toggleStage}
          onRows={(n) => setDraft(String(n))}
        />
      )}
    </div>
  );
}
