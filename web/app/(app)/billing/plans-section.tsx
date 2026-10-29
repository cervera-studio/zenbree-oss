import { getFormatter, getLocale, getTranslations } from 'next-intl/server';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { getPlans, getSubscription } from '@/lib/billing/api';
import { formatEurCents } from '@/lib/billing/schemas';
import { cn } from '@/lib/utils';
import { portalAction } from './actions';
import { BillingBlock } from './billing-block';
import { type CurrentPlan, PlanCardAction } from './plan-card-action';

function planName(id: string): string {
  const name = id.replaceAll('_', ' ');
  return `${name.charAt(0).toUpperCase()}${name.slice(1)}`;
}

type PlansT = Awaited<ReturnType<typeof getTranslations<'billing'>>>;

/** The current plan's next date: when it ends (#538: `cancelAt`), or what the renewal brings. */
function periodLine(
  t: PlansT,
  ends: string | undefined | null,
  renewal: string | null,
  scheduled: string | null,
) {
  if (ends) return t('plans.endsOn', { date: ends });
  if (!renewal) return null;
  return scheduled
    ? t('plans.scheduled', { plan: planName(scheduled), date: renewal })
    : t('plans.renewsOn', { date: renewal });
}

type PlanCard = { id: string; credits?: number; priceEurCents?: number };

export async function PlansSection({ profileReady }: { profileReady: boolean }) {
  const [t, format, locale, plans, sub] = await Promise.all([
    getTranslations('billing'),
    getFormatter(),
    getLocale(),
    getPlans(),
    getSubscription(),
  ]);
  const current = sub.success ? sub.data.subscription : null;
  const periodEnd = current?.currentPeriodEnd ? new Date(current.currentPeriodEnd) : null;
  const cards: PlanCard[] = plans.success ? plans.data.plans : [];
  const currentPlanId = current?.planId ?? null;
  if (currentPlanId && !cards.some((p) => p.id === currentPlanId)) {
    cards.unshift({ id: currentPlanId });
  }
  const pastDue = current?.status === 'past_due' || current?.status === 'unpaid';
  const renewal = periodEnd && format.dateTime(periodEnd, { dateStyle: 'medium' });
  const ends =
    current?.cancelAt && format.dateTime(new Date(current.cancelAt), { dateStyle: 'medium' });
  const scheduledPlanId = current?.scheduledPlanId ?? null;
  const currentOffer = cards.find((p) => p.id === currentPlanId);
  const currentPlan: CurrentPlan | null = current
    ? {
        plan:
          currentOffer?.credits != null && currentOffer.priceEurCents != null
            ? {
                id: currentOffer.id,
                name: planName(currentOffer.id),
                credits: currentOffer.credits,
                priceEurCents: currentOffer.priceEurCents,
              }
            : null,
        scheduledPlanId,
        renewal,
        changeable: current.status === 'active' && !ends,
      }
    : null;
  const periodNote = current && periodLine(t, ends, renewal, scheduledPlanId);

  return (
    <BillingBlock
      title={t('plans.title')}
      action={
        current && (
          <form action={portalAction}>
            <Button type="submit" variant="link" className="h-auto p-0">
              {t('plans.manage')}
            </Button>
          </form>
        )
      }
    >
      {!plans.success && !current ? (
        <p className="text-muted text-ui">{t('plans.error')}</p>
      ) : cards.length === 0 ? (
        <p className="text-muted text-ui">{t('plans.empty')}</p>
      ) : (
        <div className="grid gap-3 desktop:grid-cols-3">
          {cards.map((p) => {
            const name = planName(p.id);
            const isCurrent = current != null && p.id === currentPlanId;
            return (
              <div
                key={p.id}
                className={cn(
                  'bg-ground flex flex-col gap-1.5 rounded-lg border p-3.5',
                  isCurrent ? 'border-accent' : 'border-line',
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-ui-strong grow">{name}</span>
                  {isCurrent && <Tag tone="accent">{t('plans.currentBadge')}</Tag>}
                  {p.id === scheduledPlanId && renewal && !ends && (
                    <Tag tone="accent">{t('plans.scheduledBadge', { date: renewal })}</Tag>
                  )}
                </div>
                {p.priceEurCents != null && (
                  <span className="flex items-baseline gap-1">
                    <span className="text-kpi font-mono tabular-nums">
                      {formatEurCents(p.priceEurCents, locale)}
                    </span>
                    <span className="text-muted text-small">{t('plans.cadence')}</span>
                  </span>
                )}
                <span className="text-soft text-small">
                  {p.credits != null && t('plans.credits', { credits: p.credits })}
                  {isCurrent && pastDue && ` · ${t('plans.pastDue')}`}
                  {isCurrent && !pastDue && periodNote && ` · ${periodNote}`}
                </span>
                {!isCurrent && p.credits != null && p.priceEurCents != null && (
                  <PlanCardAction
                    offer={{ id: p.id, name, credits: p.credits, priceEurCents: p.priceEurCents }}
                    current={currentPlan}
                    profileReady={profileReady}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </BillingBlock>
  );
}
