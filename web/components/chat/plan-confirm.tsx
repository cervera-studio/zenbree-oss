'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { CONFIRM_CREDITS } from '@/lib/chat/plan';

export function PlanConfirm({
  open,
  onOpenChange,
  rows,
  credits,
  after,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rows: number;
  credits: number;
  after: number | null;
  onConfirm: () => void;
}) {
  const t = useTranslations('chat.plan.confirm');
  const format = useFormatter();
  const figures = [
    [t('rows'), format.number(rows, 'credits')],
    [t('max'), format.number(credits, 'credits')],
    [t('after'), after === null ? '—' : format.number(after, 'credits')],
  ] as const;
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('title', { credits })}
      description={t('body', { credits, threshold: CONFIRM_CREDITS })}
      confirmLabel={t('confirm')}
      destructive={false}
      onConfirm={onConfirm}
    >
      <dl className="bg-ground grid grid-cols-3 gap-2.5 rounded-xl border p-3 text-xs-plus">
        {figures.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted">{label}</dt>
            <dd className="mt-0.5 font-bold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </ConfirmDialog>
  );
}
