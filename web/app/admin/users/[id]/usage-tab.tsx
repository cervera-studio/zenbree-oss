import { getFormatter, getLocale, getTranslations } from 'next-intl/server';
import { Empty } from '@/components/admin/api-error';
import { PurchasesTable } from '@/components/admin/ledger';
import { KpiStrip, type Stat } from '@/components/ui/kpi';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { getUsage } from '@/lib/admin/api';
import { formatMarginPct, formatMoney } from '@/lib/admin/format';

export type Usage = Extract<Awaited<ReturnType<typeof getUsage>>, { success: true }>['data'];

export async function UsageTab({ usage }: { usage: Usage }) {
  const [t, locale, format] = [
    await getTranslations('admin'),
    await getLocale(),
    await getFormatter(),
  ];
  const { totals, byVendor } = usage.costs;
  const stats: Stat[] = [
    { label: t('costs.llm'), value: formatMoney(totals.llmCostUsd, 'USD', locale) },
    { label: t('costs.vendor'), value: formatMoney(totals.vendorCostUsd, 'USD', locale) },
    { label: t('costs.revenue'), value: formatMoney(totals.revenueEur, 'EUR', locale) },
    {
      label: t('costs.margin'),
      value: formatMoney(totals.marginEur, 'EUR', locale),
      note: formatMarginPct(totals.marginEur, totals.revenueEur, locale),
      tone: totals.marginEur < 0 ? 'negative' : 'positive',
    },
    { label: t('costs.creditsSpent'), value: format.number(totals.creditsSpent, 'credits') },
  ];

  return (
    <div className="flex flex-col gap-4">
      <p className="text-small text-muted">
        {t('userDetail.last30Days', { rate: usage.eurPerUsd })}
      </p>
      <KpiStrip stats={stats} />

      <div className="grid grid-cols-1 items-start gap-6 desktop:grid-cols-2">
        <section className="flex flex-col gap-2">
          <h2 className="text-ui-strong">{t('userDetail.byVendor')}</h2>
          {byVendor.length === 0 ? (
            <Empty />
          ) : (
            <Table frame>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('userDetail.vendor')}</TableHead>
                  <TableHead num>{t('userDetail.calls')}</TableHead>
                  <TableHead num>{t('userDetail.cost')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {byVendor.map((v) => (
                  <TableRow key={v.vendor}>
                    <TableCell className="font-mono">{v.vendor}</TableCell>
                    <TableCell num>{format.number(v.calls, 'credits')}</TableCell>
                    <TableCell num>{formatMoney(v.costUsd, 'USD', locale)}</TableCell>
                  </TableRow>
                ))}
                <TableRow className="font-bold">
                  <TableCell>{t('userDetail.total')}</TableCell>
                  <TableCell num>
                    {format.number(
                      byVendor.reduce((n, v) => n + v.calls, 0),
                      'credits',
                    )}
                  </TableCell>
                  <TableCell num>{formatMoney(totals.vendorCostUsd, 'USD', locale)}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          )}
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-ui-strong">{t('userDetail.purchases')}</h2>
          <PurchasesTable purchases={usage.purchases} />
        </section>
      </div>
    </div>
  );
}
