'use client';

import Link from 'next/link';
import { useLocale, useTranslations } from 'next-intl';
import { formatEurCents } from '@/lib/billing/schemas';
import { canDrop, planCredits, rowsWithin, type TopUpPack } from '@/lib/chat/plan';
import type { AgentPlan } from '@/lib/chat/types';
import { PLAN_EYEBROW } from './plan-section';

const FIX =
  'border-line bg-ground hover:bg-raised focus-visible:ring-accent/50 flex flex-col gap-1.5 rounded-lg border p-3.5 text-left transition-colors outline-none focus-visible:ring-3 phone:min-h-11';

export function PaymentBox({ balance, credits }: { balance: number; credits: number }) {
  const t = useTranslations('chat.plan.pay');
  return (
    <div className="border-line flex flex-col gap-2 border-b px-4 py-3">
      <h4 className={PLAN_EYEBROW}>{t('title')}</h4>
      <p className="text-small text-soft">{t('body')}</p>
      <dl className="text-small flex flex-col gap-1 tabular-nums">
        <div className="flex justify-between">
          <dt className="text-muted">{t('balance')}</dt>
          <dd className="text-data font-mono">{t('credits', { credits: balance })}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted">{t('after')}</dt>
          <dd className="text-data font-mono">
            {t('credits', { credits: Math.max(0, balance - credits) })}
          </dd>
        </div>
      </dl>
    </div>
  );
}

export function ShortfallBar({ credits, balance }: { credits: number; balance: number }) {
  const t = useTranslations('chat.plan.short');
  const covered = Math.min(100, Math.round((balance / credits) * 100));
  return (
    <div className="flex flex-col gap-2">
      <div aria-hidden className="bg-hair flex h-2 overflow-hidden rounded-full">
        {/* Width from data: a style, not a class, since it is any of 101 values. */}
        <span className="bg-accent h-full" style={{ width: `${covered}%` }} />
        <span className="bg-warning h-full flex-1" />
      </div>
      <div className="text-data-sm text-muted flex justify-between font-mono tabular-nums">
        <span>{t('balance', { balance })}</span>
        <span>{t('need', { credits })}</span>
      </div>
    </div>
  );
}

export function ShortfallFixes({
  plan,
  rows,
  valid,
  dropped,
  balance,
  topUpPack,
  onDrop,
  onRows,
}: {
  plan: AgentPlan;
  rows: number;
  valid: boolean;
  dropped: ReadonlySet<number>;
  balance: number;
  topUpPack: TopUpPack | null;
  onDrop: (index: number) => void;
  onRows: (rows: number) => void;
}) {
  const t = useTranslations('chat.plan.fix');
  const locale = useLocale();
  const fit = rowsWithin(plan, dropped, balance);
  const droppable = plan.stages.flatMap((s, i) =>
    canDrop(s) && !dropped.has(i) ? [{ s, i }] : [],
  );
  const fitsFewer = fit >= 1 && (!valid || fit < rows);
  const count = 1 + droppable.length + (fitsFewer ? 1 : 0);

  return (
    <section aria-labelledby="plan-fix" className="flex flex-col gap-2.5">
      <h4 id="plan-fix" className="text-ui-strong">
        {t('title', { count })}
      </h4>
      <div className="grid gap-2.5 desktop:grid-cols-3">
        <Link href="/billing" className={FIX}>
          <span className="text-ui-strong">{t('topUp.title')}</span>
          <span className="text-small text-muted">
            {topUpPack
              ? t('topUp.bodyPack', {
                  credits: topUpPack.credits,
                  price: formatEurCents(topUpPack.priceEurCents, locale),
                })
              : t('topUp.body')}
          </span>
        </Link>
        {droppable.map(({ s, i }) => (
          <button key={i} type="button" className={FIX} onClick={() => onDrop(i)}>
            <span className="text-ui-strong">{t('dropStage.title', { label: s.label })}</span>
            <span className="text-small text-muted">
              {t('dropStage.body', { credits: planCredits(plan, rows, new Set([...dropped, i])) })}
            </span>
          </button>
        ))}
        {fitsFewer && (
          <button type="button" className={FIX} onClick={() => onRows(fit)}>
            <span className="text-ui-strong">{t('fitRows.title', { rows: fit })}</span>
            <span className="text-small text-muted">{t('fitRows.body')}</span>
          </button>
        )}
      </div>
    </section>
  );
}
