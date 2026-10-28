import { Building2, Search } from 'lucide-react';
import Link from 'next/link';
import { getFormatter, getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import { AdminPage, sectionCrumbs } from '@/components/admin/admin-page';
import { ApiError } from '@/components/admin/api-error';
import { Pager, parseOffset } from '@/components/admin/pager';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { getOrganizations } from '@/lib/admin/api';
import { formatDay } from '@/lib/admin/format';

const LIMIT = 50;

export default async function AdminOrganizations({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const q = typeof sp.q === 'string' ? sp.q.trim() : '';
  const offset = parseOffset(sp.offset);
  const [t, locale, timeZone, format, res] = await Promise.all([
    getTranslations('admin'),
    getLocale(),
    getTimeZone(),
    getFormatter(),
    getOrganizations(q, offset, LIMIT),
  ]);

  return (
    <AdminPage
      crumbs={sectionCrumbs(t('shell.label'), t('organizations.title'))}
      title={t('organizations.title')}
      meta={res.success && t('organizations.summary', { total: res.data.total })}
    >
      <form className="relative w-full max-w-sm">
        <Search
          className="text-muted pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
          aria-hidden
        />
        <Input
          name="q"
          type="search"
          defaultValue={q}
          placeholder={t('organizations.searchPlaceholder')}
          aria-label={t('organizations.searchPlaceholder')}
          className="bg-ground h-10 pl-9"
        />
      </form>

      {!res.success ? (
        <ApiError error={res.error} request="GET /v1/admin/organizations" />
      ) : res.data.items.length === 0 ? (
        <section className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-12 text-center">
          <Building2 className="text-muted mb-1 size-6" aria-hidden />
          <h2 className="font-bold">{t('organizations.noResults')}</h2>
          {q && (
            <p className="text-muted max-w-md text-sm leading-normal">
              {t('organizations.noResultsQuery', { q })}
            </p>
          )}
          <Button asChild variant="outline" size="sm" className="mt-2">
            <Link href="/admin/organizations">{t('userList.reset')}</Link>
          </Button>
        </section>
      ) : (
        <>
          <Table frame>
            <TableHeader>
              <TableRow>
                <TableHead>{t('organizations.name')}</TableHead>
                <TableHead num>{t('organizations.members')}</TableHead>
                <TableHead>{t('userList.plan')}</TableHead>
                <TableHead num>{t('userDetail.balance')}</TableHead>
                <TableHead>{t('createdAt')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {res.data.items.map((o) => (
                <TableRow key={o.id}>
                  <TableCell>
                    <Link
                      href={`/admin/organizations/${o.id}`}
                      className="text-accent-ink font-semibold hover:underline"
                    >
                      {o.name}
                    </Link>
                  </TableCell>
                  <TableCell num>{format.number(o.members, 'credits')}</TableCell>
                  <TableCell>
                    {o.planId ? (
                      <span className="capitalize">{o.planId}</span>
                    ) : o.hasPurchases ? (
                      t('userList.topUpsOnly')
                    ) : (
                      '—'
                    )}
                  </TableCell>
                  <TableCell num className="font-semibold">
                    {o.balance == null ? '—' : format.number(o.balance, 'credits')}
                  </TableCell>
                  <TableCell>{formatDay(o.createdAt, { locale, timeZone })}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pager
            path="/admin/organizations"
            params={{ q }}
            offset={offset}
            limit={LIMIT}
            hasNext={offset + res.data.items.length < res.data.total}
            shown={res.data.items.length}
            total={res.data.total}
          />
        </>
      )}
    </AdminPage>
  );
}
