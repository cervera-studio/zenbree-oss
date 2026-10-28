import { getFormatter, getLocale, getTranslations } from 'next-intl/server';
import { AdminPage, sectionCrumbs } from '@/components/admin/admin-page';
import { ApiError } from '@/components/admin/api-error';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { KpiStrip } from '@/components/ui/kpi';
import { Label } from '@/components/ui/label';
import { type Costs, getCosts, getSettings } from '@/lib/admin/api';
import { formatMarginPct, moneyFormatters } from '@/lib/admin/format';
import { DailyPanel } from './daily-panel';
import { ProviderTable } from './provider-table';
import { UserTable } from './user-table';
import { costsHref, parseView, type View } from './view';

export default async function CostsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const view = parseView(await searchParams);
  const [t, res, settings] = await Promise.all([
    getTranslations('admin'),
    getCosts(view.from, view.to),
    getSettings(),
  ]);
  // A provider whose kill switch is on gets a «disattivato» tag; no settings, no tags.
  const disabled = new Set(
    settings.success
      ? settings.data.settings
          .filter((s) => s.key.startsWith('vendor_disabled:') && s.value === true)
          .map((s) => s.key.slice('vendor_disabled:'.length))
      : [],
  );
  const query = new URLSearchParams({
    ...(view.from && { from: view.from }),
    ...(view.to && { to: view.to }),
  }).toString();

  return (
    <AdminPage
      crumbs={sectionCrumbs(t('shell.label'), t('costs.title'))}
      title={t('costs.title')}
      meta={t('costs.subtitle')}
      actions={
        <form className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="from" className="text-small">
              {t('costs.from')}
            </Label>
            <Input
              id="from"
              name="from"
              size="sm"
              className="w-35"
              type="date"
              defaultValue={res.success ? res.data.from : view.from}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="to" className="text-small">
              {t('costs.to')}
            </Label>
            <Input
              id="to"
              name="to"
              size="sm"
              className="w-35"
              type="date"
              defaultValue={res.success ? res.data.to : view.to}
            />
          </div>
          {view.table && <input type="hidden" name="view" value="table" />}
          {view.sort === 'margin' && <input type="hidden" name="sort" value="margin" />}
          <Button type="submit" variant="outline" size="sm">
            {t('common.apply')}
          </Button>
        </form>
      }
    >
      {!res.success ? (
        <ApiError
          error={res.error}
          request={`GET /v1/admin/costs${query && `?${query}`}`}
          retryHref={costsHref(view, {})}
        />
      ) : (
        <CostsView data={res.data} view={view} disabled={disabled} />
      )}
    </AdminPage>
  );
}

async function CostsView({
  data,
  view,
  disabled,
}: {
  data: Costs;
  view: View;
  disabled: Set<string>;
}) {
  const [t, locale, format] = await Promise.all([
    getTranslations('admin'),
    getLocale(),
    getFormatter(),
  ]);
  const { eur, usd } = moneyFormatters(locale);
  const tot = data.totals;

  return (
    <>
      <KpiStrip
        stats={[
          { label: t('costs.llm'), value: usd(tot.llmCostUsd) },
          { label: t('costs.vendor'), value: usd(tot.vendorCostUsd) },
          { label: t('costs.revenue'), value: eur(tot.revenueEur) },
          { label: t('costs.margin'), value: eur(tot.marginEur) },
          {
            label: t('costs.marginPct'),
            value: formatMarginPct(tot.marginEur, tot.revenueEur, locale),
            tone: tot.marginEur < 0 ? 'negative' : 'positive',
          },
          { label: t('costs.creditsSpent'), value: format.number(tot.creditsSpent, 'credits') },
        ]}
      />
      <DailyPanel data={data} view={view} />
      <div className="flex flex-col gap-6 desktop:flex-row desktop:items-start">
        <ProviderTable providers={data.byProvider} disabled={disabled} />
        <UserTable users={data.users} view={view} />
      </div>
    </>
  );
}
