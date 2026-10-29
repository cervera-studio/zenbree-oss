import { getLocale, getTranslations } from 'next-intl/server';
import { Button } from '@/components/ui/button';
import { formatEurCents } from '@/lib/billing/schemas';
import { changePlanAction, subscribeAction } from './actions';
import { PlanChangeDialog } from './plan-change-dialog';

type Offer = { id: string; name: string; credits: number; priceEurCents: number };

export type CurrentPlan = {
  plan: Offer | null; // null: a price not in BILLING_PLANS
  scheduledPlanId: string | null;
  renewal: string | null; // the formatted current_period_end
  changeable: boolean; // active and not cancelled at period end, as POST /v1/billing/plan requires
};

/** What a plan card other than the current one offers: subscribe, switch, or cancel a scheduled switch. */
export async function PlanCardAction({
  offer,
  current,
  profileReady,
}: {
  offer: Offer;
  current: CurrentPlan | null;
  profileReady: boolean;
}) {
  const [t, locale] = await Promise.all([getTranslations('billing.plans'), getLocale()]);
  if (!current) {
    return (
      <form action={subscribeAction} className="self-start">
        <input type="hidden" name="planId" value={offer.id} />
        <Button type="submit" disabled={!profileReady}>
          {t('subscribe')}
        </Button>
      </form>
    );
  }
  const { plan, renewal } = current;
  if (!plan || !renewal || !current.changeable) return null;
  if (offer.id === current.scheduledPlanId) {
    return (
      <form action={changePlanAction.bind(null, plan.id)} className="self-start">
        <Button type="submit" variant="outline">
          {t('cancelChange')}
        </Button>
      </form>
    );
  }
  const label = t('switchTo', { plan: offer.name });
  if (offer.priceEurCents > plan.priceEurCents) {
    const price = formatEurCents(offer.priceEurCents - plan.priceEurCents, locale);
    return (
      <PlanChangeDialog
        planId={offer.id}
        label={label}
        title={t('upgradeTitle', { plan: offer.name })}
        description={t('upgradeBody', {
          plan: offer.name,
          price,
          credits: offer.credits,
          date: renewal,
          renewal: formatEurCents(offer.priceEurCents, locale),
        })}
        confirmLabel={t('upgradeConfirm', { price })}
      />
    );
  }
  return (
    <PlanChangeDialog
      planId={offer.id}
      label={label}
      title={t('downgradeTitle', { plan: offer.name })}
      description={t('downgradeBody', {
        plan: offer.name,
        current: plan.name,
        price: formatEurCents(offer.priceEurCents, locale),
        credits: offer.credits,
        date: renewal,
      })}
      confirmLabel={t('downgradeConfirm', { plan: offer.name, date: renewal })}
    />
  );
}
