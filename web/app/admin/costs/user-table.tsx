import Link from 'next/link';
import { getFormatter, getLocale, getTranslations } from 'next-intl/server';
import { Empty } from '@/components/admin/api-error';
import { TEXT } from '@/components/ui/badge';
import { FilterChip } from '@/components/ui/filter-chip';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { Costs } from '@/lib/admin/api';
import { formatMarginPct, moneyFormatters } from '@/lib/admin/format';
import { cn } from '@/lib/utils';
import { costsHref, type View } from './view';

export async function UserTable({ users, view }: { users: Costs['users']; view: View }) {
  const [t, locale, format] = await Promise.all([
    getTranslations('admin'),
    getLocale(),
    getFormatter(),
  ]);
  const { eur, usd } = moneyFormatters(locale);
  const rows = [...users].sort((a, b) =>
    view.sort === 'margin' ? a.marginEur - b.marginEur : b.costUsd - a.costUsd,
  );
  return (
    <section className="flex min-w-0 grow flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-ui-strong grow">{t('costs.leaderboard')}</h2>
        <nav aria-label={t('costs.sort')} className="flex gap-2">
          {(['cost', 'margin'] as const).map((key) => (
            <FilterChip key={key} href={costsHref(view, { sort: key })} on={view.sort === key}>
              {t(key === 'cost' ? 'costs.sortCost' : 'costs.sortMargin')}
            </FilterChip>
          ))}
        </nav>
      </div>
      {rows.length === 0 ? (
        <Empty />
      ) : (
        <Table frame>
          <TableHeader>
            <TableRow>
              <TableHead>{t('email')}</TableHead>
              <TableHead num>{t('costs.llmShort')}</TableHead>
              <TableHead num>{t('costs.vendorShort')}</TableHead>
              <TableHead num>{t('costs.credits')}</TableHead>
              <TableHead num>{t('costs.revenue')}</TableHead>
              <TableHead num>{t('costs.margin')}</TableHead>
              <TableHead num>%</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((u) => (
              <TableRow key={u.userId ?? 'none'}>
                <TableCell className="text-ui-strong max-w-0 min-w-32 truncate text-ink">
                  {u.userId ? (
                    <Link
                      href={`/admin/users/${u.userId}`}
                      title={u.email ?? u.userId}
                      className="hover:underline"
                    >
                      {u.email ?? u.userId}
                    </Link>
                  ) : (
                    <span className="text-muted font-normal">{t('costs.anonymous')}</span>
                  )}
                </TableCell>
                <TableCell num>{usd(u.llmCostUsd)}</TableCell>
                <TableCell num>{usd(u.vendorCostUsd)}</TableCell>
                <TableCell num>{format.number(u.creditsSpent, 'credits')}</TableCell>
                <TableCell num>{eur(u.revenueEur)}</TableCell>
                <TableCell num className={cn(u.marginEur < 0 && TEXT.negative)}>
                  {eur(u.marginEur)}
                </TableCell>
                <TableCell num>{formatMarginPct(u.marginEur, u.revenueEur, locale)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <p className="text-small text-muted">{t('costs.negativeNote')}</p>
    </section>
  );
}
