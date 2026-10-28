import Link from 'next/link';
import { getFormatter, getLocale, getTranslations } from 'next-intl/server';
import { TEXT } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { MultiLineChart } from '@/components/ui/charts';
import { Panel } from '@/components/ui/panel';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { Costs } from '@/lib/admin/api';
import { moneyFormatters, moneyOptions } from '@/lib/admin/format';
import { cn } from '@/lib/utils';
import { costsHref, type View } from './view';

const r2 = (n: number) => Math.round(n * 100) / 100;

export async function DailyPanel({ data, view }: { data: Costs; view: View }) {
  const [t, locale, format] = await Promise.all([
    getTranslations('admin'),
    getLocale(),
    getFormatter(),
  ]);
  const { eur } = moneyFormatters(locale);
  const fx = data.eurPerUsd;
  const days = data.daily.map((d) => ({
    day: d.day,
    // The API buckets days in UTC (`api/app/admin/costs.py`), so a day key is a UTC calendar day.
    label: format.dateTime(new Date(`${d.day}T00:00:00Z`), {
      day: 'numeric',
      month: 'short',
      timeZone: 'UTC',
    }),
    revenue: r2(d.revenueEur),
    vendor: r2(d.vendorCostUsd * fx),
    llm: r2(d.llmCostUsd * fx),
    margin: r2(d.revenueEur - (d.vendorCostUsd + d.llmCostUsd) * fx),
  }));
  // The design's three lines: the accent, the ink, and a dashed muted one.
  const series = [
    {
      dataKey: 'revenue',
      label: t('costs.revenue'),
      legend: t('costs.revenue'),
      color: 'var(--chart-1)',
    },
    {
      dataKey: 'vendor',
      label: t('costs.vendorShort'),
      legend: t('costs.vendor'),
      color: 'var(--color-ink)',
    },
    {
      dataKey: 'llm',
      label: t('costs.llmShort'),
      legend: t('costs.llm'),
      color: 'var(--chart-5)',
      dashed: true,
    },
  ] as const;
  const tot = data.totals;

  return (
    <Panel
      title={t('costs.daily')}
      action={
        <>
          <ul className="text-small text-soft mr-1.5 flex flex-wrap gap-3.5 phone:order-last phone:basis-full">
            {series.map((s) => (
              <li key={s.dataKey} className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className={cn(
                    'h-0.5 w-4 rounded-full',
                    'dashed' in s && 'h-0 rounded-none border-t-2 border-dashed',
                  )}
                  style={{ ['dashed' in s ? 'borderColor' : 'backgroundColor']: s.color }}
                />
                {s.legend}
              </li>
            ))}
          </ul>
          <Button asChild variant="outline" size="sm">
            <Link href={costsHref(view, { table: !view.table })}>
              {view.table ? t('costs.showChart') : t('costs.showTable')}
            </Link>
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-1.5">
        {view.table ? (
          <Table frame>
            <TableHeader>
              <TableRow>
                <TableHead>{t('costs.day')}</TableHead>
                {series.map((s) => (
                  <TableHead key={s.dataKey} num>
                    {s.legend}
                  </TableHead>
                ))}
                <TableHead num>{t('costs.margin')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {days.map((d) => (
                <TableRow key={d.day}>
                  <TableCell>{d.label}</TableCell>
                  {series.map((s) => (
                    <TableCell key={s.dataKey} num>
                      {eur(d[s.dataKey])}
                    </TableCell>
                  ))}
                  <TableCell num className={cn(d.margin < 0 && TEXT.negative)}>
                    {eur(d.margin)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <MultiLineChart
            data={days}
            series={series}
            tooltipRow={{ dataKey: 'margin', label: t('costs.margin') }}
            valueFormat={moneyOptions('EUR')}
            className="h-44"
            ariaLabel={t('costs.chartSummary', {
              from: days[0]?.label ?? data.from,
              to: days.at(-1)?.label ?? data.to,
              revenue: eur(tot.revenueEur),
              vendor: eur(tot.vendorCostUsd * fx),
              llm: eur(tot.llmCostUsd * fx),
              margin: eur(tot.marginEur),
            })}
          />
        )}
        <p className="text-small text-muted">{t('costs.fxNote', { rate: fx })}</p>
      </div>
    </Panel>
  );
}
