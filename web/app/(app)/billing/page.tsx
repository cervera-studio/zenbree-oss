import { getFormatter, getTranslations } from 'next-intl/server';
import { SettingsCard } from '@/components/settings/settings-card';
import { StatusNotice } from '@/components/settings/status-notice';
import { Meter } from '@/components/ui/meter';
import { getMe } from '@/lib/api/me';
import { getBillingProfile } from '@/lib/billing/api';
import { isOrgAdmin } from '@/lib/org';
import { AutoTopupSection } from './auto-topup-section';
import { InvoicesSection } from './invoices-section';
import { PacksSection } from './packs-section';
import { PlansSection } from './plans-section';
import { BillingProfileForm } from './profile-form';
import { TransactionsSection } from './transactions-section';

const NOTICES = ['success', 'cancel', 'error'] as const;
const AUTO_TOPUP_NOTICES = ['saved', 'cancel', 'error'] as const;
const PLAN_NOTICES = ['upgraded', 'downgrade_scheduled', 'downgrade_canceled', 'error'] as const;

// Mirrors the API: ToolDef `cost` in api/app/agent/tools/handlers/{search_companies,search_people,
// enrich_email,enrich_phone}.py and `CELL_COST` in api/app/lists/ai_columns.py.
const PRICES = [
  { key: 'agent', credits: 0 },
  { key: 'row', credits: 0 },
  { key: 'email', credits: 1 },
  { key: 'phone', credits: 8 },
  { key: 'aiCell', credits: 1 },
] as const satisfies readonly { key: string; credits: number }[];

export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<{
    checkout?: string;
    profile?: string;
    autotopup?: string;
    plan?: string;
    offset?: string;
    invoiceCursor?: string;
  }>;
}) {
  const sp = await searchParams;
  const offset = Math.max(0, Number(sp.offset) || 0);
  const [t, format, me, orgAdmin] = await Promise.all([
    getTranslations('billing'),
    getFormatter(),
    getMe(),
    isOrgAdmin(),
  ]);
  const notice = NOTICES.find((n) => n === sp.checkout);
  const autoTopupNotice = AUTO_TOPUP_NOTICES.find((n) => n === sp.autotopup);
  const planNotice = PLAN_NOTICES.find((n) => n === sp.plan);
  const profile = orgAdmin ? await getBillingProfile() : null;
  const profileReady = Boolean(
    profile?.success && profile.data.profile && profile.data.syncStatus !== 'pending',
  );
  const profileNotice = ['required', 'pending'].find((n) => n === sp.profile);

  const planLeft = me.success ? me.data.planCreditsLeft : null;
  const planResetAt = me.success ? me.data.planCreditsResetAt : null;
  const purchased = me.success ? (me.data.purchasedCredits ?? 0) : 0;
  const planSplit =
    planLeft != null && planResetAt ? { left: planLeft, resetAt: planResetAt } : null;

  return (
    <div className="flex flex-col gap-5">
      <h2 className="sr-only">{t('title')}</h2>
      {notice && (
        <StatusNotice tone={notice === 'success' ? 'positive' : 'negative'}>
          {t(`checkout.${notice}`)}
        </StatusNotice>
      )}
      {planNotice && (
        <StatusNotice tone={planNotice === 'error' ? 'negative' : 'positive'}>
          {t(`plans.notice.${planNotice}`)}
        </StatusNotice>
      )}
      {profileNotice && (
        <StatusNotice tone="negative">
          {t(profileNotice === 'pending' ? 'profile.pending' : 'profile.required')}
        </StatusNotice>
      )}
      {autoTopupNotice && (
        <StatusNotice tone={autoTopupNotice === 'saved' ? 'positive' : 'negative'}>
          {t(`autoTopup.notice.${autoTopupNotice}`)}
        </StatusNotice>
      )}

      <section aria-labelledby="balance" className="grid gap-4 desktop:grid-cols-2">
        <SettingsCard className="gap-2 p-4">
          <h3 id="balance" className="text-label text-muted uppercase">
            {t('balance.title')}
          </h3>
          {me.success ? (
            <>
              <div className="flex items-baseline gap-2">
                <span className="text-metric font-mono tabular-nums">
                  {format.number(me.data.creditsRemaining ?? 0, 'credits')}
                </span>
                <span className="text-muted text-ui">{t('balance.unit')}</span>
              </div>
              {planSplit && (
                <>
                  {planSplit.left + purchased > 0 && (
                    <Meter kind="stacked" value={planSplit.left / (planSplit.left + purchased)} />
                  )}
                  <ul className="text-soft text-small flex flex-wrap gap-x-6 gap-y-1">
                    <li className="flex items-center gap-1.5">
                      <span aria-hidden className="bg-accent size-2 shrink-0 rounded-xs" />
                      <span>
                        {t.rich('balance.fromPlan', {
                          credits: planSplit.left,
                          date: format.dateTime(new Date(planSplit.resetAt), {
                            dateStyle: 'medium',
                          }),
                          b: (chunks) => <b className="text-data text-ink font-mono">{chunks}</b>,
                        })}
                      </span>
                    </li>
                    <li className="flex items-center gap-1.5">
                      <span aria-hidden className="bg-lime size-2 shrink-0 rounded-xs" />
                      <span>
                        {t.rich('balance.fromPurchased', {
                          credits: purchased,
                          b: (chunks) => <b className="text-data text-ink font-mono">{chunks}</b>,
                        })}
                      </span>
                    </li>
                  </ul>
                </>
              )}
            </>
          ) : (
            <p className="text-muted text-ui">{t('balanceUnavailable')}</p>
          )}
        </SettingsCard>
        <SettingsCard className="gap-2 p-4">
          <h3 id="prices" className="text-label text-muted uppercase">
            {t('prices.title')}
          </h3>
          <ul aria-labelledby="prices" className="flex flex-col gap-2">
            {PRICES.map(({ key, credits }) => (
              <li key={key} className="flex items-center justify-between gap-3">
                <span className="text-ui">{t(`prices.items.${key}`)}</span>
                <span className="text-data text-ink font-mono tabular-nums">
                  {t('prices.value', { credits })}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-muted text-small">{t('prices.note')}</p>
        </SettingsCard>
      </section>

      {/* Credits belong to the organization: members see its balance, only admins buy and manage (#306). */}
      {orgAdmin ? (
        <>
          {!profileReady && (
            <p className="text-small">
              <a href="#billing-profile" className="text-accent-ink text-ui-strong">
                {t('needsProfile')}
              </a>
            </p>
          )}
          <PlansSection profileReady={profileReady} />
          <PacksSection profileReady={profileReady} />
          <TransactionsSection offset={offset} invoiceCursor={sp.invoiceCursor} />
          {profile?.success ? (
            <BillingProfileForm data={profile.data} />
          ) : (
            <StatusNotice tone="negative">{t('profile.loadError')}</StatusNotice>
          )}
          <AutoTopupSection profileReady={profileReady} />
          <InvoicesSection cursor={sp.invoiceCursor} offset={offset} />
        </>
      ) : (
        <p className="text-muted text-ui">{t('adminOnly')}</p>
      )}
    </div>
  );
}
