import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { lastDays, PRESETS, type Range } from '@/lib/reports/range';

/** The report's dates: a plain GET form, so the range lives in the URL and a reload keeps it. */
export async function ReportRange({ range, today }: { range: Range; today: string }) {
  const t = await getTranslations('reports.range');
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
      {/* A preset link navigates on the client and keeps this form mounted: the key remounts it, so
          the uncontrolled date fields show the new range instead of the old one. */}
      <form
        key={`${range.from}:${range.to}`}
        aria-label={t('label')}
        className="flex flex-wrap items-end gap-2"
      >
        <div className="flex flex-col gap-1">
          <Label htmlFor="report-from" className="text-small">
            {t('from')}
          </Label>
          <Input
            id="report-from"
            name="from"
            size="sm"
            className="w-35"
            type="date"
            defaultValue={range.from}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="report-to" className="text-small">
            {t('to')}
          </Label>
          <Input
            id="report-to"
            name="to"
            size="sm"
            className="w-35"
            type="date"
            defaultValue={range.to}
          />
        </div>
        <Button type="submit" variant="outline" size="sm" className="phone:h-11">
          {t('apply')}
        </Button>
      </form>
      <nav aria-label={t('presets')} className="flex flex-wrap gap-1">
        {PRESETS.map((days) => {
          const preset = lastDays(days, today);
          const active = preset.from === range.from && preset.to === range.to;
          return (
            <Link
              key={days}
              href={`/campaigns/report?${new URLSearchParams(preset)}`}
              aria-current={active ? 'page' : undefined}
              className={buttonVariants({
                variant: active ? 'soft' : 'ghost',
                size: 'sm',
                className: 'phone:h-11',
              })}
            >
              {t('last', { days })}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
