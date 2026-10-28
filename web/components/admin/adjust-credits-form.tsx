'use client';

import { ArrowRight } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';
import { adjustCredits } from '@/app/admin/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { asResult } from '@/lib/admin/as-result';
import { useAction } from '@/lib/hooks/use-action';
import { cn } from '@/lib/utils';

const QUICK = [100, 500, 50, -100] as const;
const COURTESY = 50;

/**
 * The typed amount, or null unless it is a non-zero integer. `Number`, not
 * `parseInt`: the number input accepts "1e3", which `parseInt` reads as 1.
 */
export function parseAmount(amount: string): number | null {
  const n = Number(amount);
  return Number.isInteger(n) && n !== 0 ? n : null;
}

export function AdjustCreditsForm({
  target,
  balance,
}: {
  target: Parameters<typeof adjustCredits>[0];
  balance: number | null;
}) {
  const t = useTranslations('admin');
  const format = useFormatter();
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const { pending, error, run, reset } = useAction();

  const parsed = parseAmount(amount);
  const valid = parsed !== null;
  const n = parsed ?? 0;
  const after = balance === null || !valid ? null : balance + n;
  const belowZero = after !== null && after < 0;
  const credits = (v: number) => format.number(v, 'credits');
  const signed = (v: number) => `${v < 0 ? '−' : '+'}${credits(Math.abs(v))}`;

  const submitLabel = valid
    ? t(n > 0 ? 'userDetail.submitAdd' : 'userDetail.submitRemove', { n: Math.abs(n) })
    : t('userDetail.submit');

  return (
    <div className="grid items-start gap-6 desktop:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <form
        className="border-line flex flex-col gap-4 rounded-lg border p-5"
        onSubmit={(e) => {
          e.preventDefault();
          run(() => asResult(adjustCredits(target, n, reason.trim())), {
            successToast: t('common.saved'),
            onSuccess: () => {
              setAmount('');
              setReason('');
            },
          });
        }}
      >
        <div className="flex flex-col gap-1">
          <h2 className="text-ui-strong">{t('userDetail.adjustTitle')}</h2>
          <p className="text-muted text-ui">{t('userDetail.adjustIntro')}</p>
        </div>
        <fieldset aria-label={t('userDetail.shortcuts')} className="flex min-w-0 flex-wrap gap-2">
          {QUICK.map((q) => (
            <Button
              key={q}
              type="button"
              variant="outline"
              size="xs"
              className="rounded-full px-3"
              onClick={() => {
                setAmount(String(q));
                if (q === COURTESY && !reason) setReason(t('userDetail.courtesyReason'));
              }}
            >
              {q === COURTESY ? t('userDetail.quickCourtesy', { amount: signed(q) }) : signed(q)}
            </Button>
          ))}
        </fieldset>
        <div className="flex flex-col gap-2">
          <Label htmlFor="amount">
            {t('userDetail.amountLabel')}{' '}
            <span className="text-muted font-normal">{t('userDetail.amountHint')}</span>
          </Label>
          <Input
            id="amount"
            size="sm"
            type="number"
            step={1}
            required
            placeholder={t('userDetail.amountPlaceholder')}
            value={amount}
            aria-invalid={belowZero || undefined}
            aria-describedby={belowZero ? 'amount-error' : undefined}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="reason">{t('userDetail.reasonLabel')}</Label>
          <Input
            id="reason"
            size="sm"
            required
            maxLength={500}
            placeholder={t('userDetail.reasonPlaceholder')}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        {balance !== null && (
          <BalancePreview
            balance={balance}
            after={after}
            tone={belowZero ? 'text-negative' : valid && n > 0 && 'text-positive'}
          />
        )}
        {belowZero && (
          <p id="amount-error" role="alert" className="text-negative text-ui">
            {t('userDetail.belowZero', { max: balance ?? 0 })}
          </p>
        )}
        {error && <p className="text-negative text-ui">{error.message}</p>}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="sm" disabled={pending || !valid || belowZero}>
            {submitLabel}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setAmount('');
              setReason('');
              reset();
            }}
          >
            {t('common.cancel')}
          </Button>
        </div>
      </form>
      <aside className="text-muted text-ui flex flex-col gap-2">
        <h3 className="text-ui-strong text-ink">{t('userDetail.helpTitle')}</h3>
        <p>{t('userDetail.helpRefunds')}</p>
        <p>{t('userDetail.helpStripe')}</p>
      </aside>
    </div>
  );
}

function BalancePreview({
  balance,
  after,
  tone,
}: {
  balance: number;
  after: number | null;
  tone: string | false;
}) {
  const t = useTranslations('admin');
  const format = useFormatter();
  return (
    <div className="border-line bg-ground flex items-center gap-4 rounded-lg border p-3 font-mono tabular-nums">
      <div className="flex flex-col gap-0.5">
        <span className="text-muted text-label font-sans uppercase">
          {t('userDetail.balanceNow')}
        </span>
        <span className="font-semibold">{format.number(balance, 'credits')}</span>
      </div>
      <ArrowRight aria-hidden className="text-muted size-4" />
      <div className="flex flex-col gap-0.5">
        <span className="text-muted text-label font-sans uppercase">
          {t('userDetail.balanceAfterAdjust')}
        </span>
        <span className={cn('font-semibold', tone)}>
          {after === null ? '—' : format.number(after, 'credits')}
        </span>
      </div>
    </div>
  );
}
