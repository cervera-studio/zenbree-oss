import Link from 'next/link';
import { getFormatter, getTranslations } from 'next-intl/server';
import { Empty } from '@/components/admin/api-error';
import { Tag } from '@/components/ui/badge';
import { Meter } from '@/components/ui/meter';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { Costs } from '@/lib/admin/api';

export async function ProviderTable({
  providers,
  disabled,
}: {
  providers: Costs['byProvider'];
  disabled: ReadonlySet<string>;
}) {
  const [t, format] = await Promise.all([getTranslations('admin'), getFormatter()]);
  const rows = [...providers].sort((a, b) => b.hits / (b.lookups || 1) - a.hits / (a.lookups || 1));
  return (
    <section className="flex flex-col gap-2 min-w-0 desktop:w-132.5">
      <h2 className="text-ui-strong flex h-control items-center">{t('costs.providers')}</h2>
      {rows.length === 0 ? (
        <Empty />
      ) : (
        <Table frame>
          <TableHeader>
            <TableRow>
              <TableHead>{t('costs.provider')}</TableHead>
              <TableHead className="w-20">{t('costs.channel')}</TableHead>
              <TableHead num className="w-22">
                {t('costs.lookups')}
              </TableHead>
              <TableHead num className="w-20">
                {t('costs.hits')}
              </TableHead>
              <TableHead className="w-36">{t('costs.hitRate')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((p) => {
              const rate = p.lookups ? p.hits / p.lookups : 0;
              return (
                <TableRow key={`${p.provider}:${p.channel}`}>
                  <TableCell>
                    <span className="text-data font-mono">{p.provider}</span>
                    {disabled.has(p.provider) && (
                      <Link
                        href={`/admin/settings#vendor_disabled:${p.provider}`}
                        aria-label={t('costs.disabledLink', { provider: p.provider })}
                        className="ml-1.5 rounded-sm outline-none hover:opacity-80 focus-visible:ring-2 focus-visible:ring-accent"
                      >
                        <Tag tone="warning">{t('costs.disabled')}</Tag>
                      </Link>
                    )}
                  </TableCell>
                  <TableCell>
                    {t(`costs.channels.${p.channel === 'phone' ? 'phone' : 'email'}`)}
                  </TableCell>
                  <TableCell num>{format.number(p.lookups, 'credits')}</TableCell>
                  <TableCell num>{format.number(p.hits, 'credits')}</TableCell>
                  <TableCell>
                    <span className="flex items-center gap-2">
                      <Meter value={rate} />
                      <span className="text-data font-mono tabular-nums">
                        {format.number(rate, { style: 'percent' })}
                      </span>
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
