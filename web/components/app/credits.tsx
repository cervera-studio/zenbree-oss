import Link from 'next/link';
import { getFormatter, getTranslations } from 'next-intl/server';
import { Meter } from '@/components/ui/meter';
import type { getMe } from '@/lib/api/me';
import { cn } from '@/lib/utils';

type Me = ReturnType<typeof getMe>;

const CARD = 'bg-ground flex shrink-0 flex-col gap-2 rounded-lg border p-2.5';

async function remaining(me: Me) {
  const [res, format] = await Promise.all([me, getFormatter()]);
  return res.success ? format.number(res.data.creditsRemaining ?? 0, 'credits') : '—';
}

// The organization's credits (#306): every member sees them, only its admins top up.

/** The sidebar's credits box, the design's: the balance against what the plan grants each period
 *  (#434, `plan_credits` of `GET /v1/me`, full past it: a top-up can lift the balance over the
 *  plan), 20 ticks, and when the allowance resets. Without a plan, the balance alone. The date is
 *  the period's end, also for a plan cancelled at period end, so it never says the plan renews. */
export async function CreditsCard({ me, orgAdmin }: { me: Me; orgAdmin: boolean }) {
  const [res, t, tBilling, format] = await Promise.all([
    me,
    getTranslations('nav'),
    getTranslations('billing'),
    getFormatter(),
  ]);
  const data = res.success ? res.data : null;
  const left = Math.max(0, data?.creditsRemaining ?? 0);
  const plan = data?.planCredits || null;
  const resetAt = plan && data?.planCreditsResetAt ? new Date(data.planCreditsResetAt) : null;
  const planLeft = resetAt ? Math.max(0, data?.planCreditsLeft ?? 0) : null;
  return (
    <div className={CARD}>
      <div className="flex items-baseline">
        <span className="text-small text-soft flex-1">{t('credits')}</span>
        <span className="text-data tabular-nums">
          <b>{data ? format.number(left, 'credits') : '—'}</b>
          {plan && (
            <span className="text-muted">
              {' / '}
              {format.number(plan, 'credits')}
            </span>
          )}
        </span>
      </div>
      {plan && <Meter kind="ticks" value={left / plan} />}
      {plan && (
        <span className="sr-only">{t('balanceOfPlan', { left: data ? left : 0, plan })}</span>
      )}
      {resetAt && planLeft !== null && (
        <span className="text-small text-soft">
          {tBilling('balance.plan', {
            credits: planLeft,
            date: format.dateTime(resetAt, { day: 'numeric', month: 'short' }),
          })}
        </span>
      )}
      {orgAdmin && (
        <Link href="/billing" className="text-accent-ink text-small font-semibold hover:underline">
          {t('topUp')}
        </Link>
      )}
    </div>
  );
}

/** What the card's place holds while `GET /v1/me` streams in: the rows of an organization with a
 *  plan, empty, plus the top-up row for an admin, so the rows below stay put when it loads. The reset
 *  line is two lines: it wraps in the 232 px rail in both languages. */
export function CreditsCardFallback({ orgAdmin }: { orgAdmin: boolean }) {
  return (
    <div aria-hidden className={cn(CARD, 'animate-pulse')}>
      <div className="text-data">&nbsp;</div>
      <div className="h-1.5" />
      <div className="text-small">
        &nbsp;
        <br />
        &nbsp;
      </div>
      {orgAdmin && <div className="text-small">&nbsp;</div>}
    </div>
  );
}

/** The phone drawer's foot, after the company: «Ritmo Sales · 942 crediti». The separator comes
 *  with the count, so none shows while it streams in. */
export async function CreditsCount({ me }: { me: Me }) {
  const [t, res] = await Promise.all([getTranslations('nav'), me]);
  return (
    <span className="tabular-nums">
      {' · '}
      {res.success
        ? t('creditsCount', { count: res.data.creditsRemaining ?? 0 })
        : `— ${t('creditsUnit')}`}
    </span>
  );
}

/** The phone top bar's credits pill: a 44 px target around a 32 px pill. */
export async function CreditsPill({ me, orgAdmin }: { me: Me; orgAdmin: boolean }) {
  const [t, credits] = await Promise.all([getTranslations('nav'), remaining(me)]);
  const pill = (
    <span className="bg-side group-hover:bg-hair text-xs-plus flex h-8 items-center gap-1.5 rounded-full px-3 font-bold whitespace-nowrap tabular-nums">
      {credits}
      <span className="text-muted font-normal">{t('creditsUnit')}</span>
    </span>
  );
  return orgAdmin ? (
    <Link href="/billing" title={t('topUp')} className="group flex min-h-11 items-center">
      {pill}
    </Link>
  ) : (
    <span className="flex min-h-11 items-center">{pill}</span>
  );
}
