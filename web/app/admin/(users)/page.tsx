import { Search } from 'lucide-react';
import { headers } from 'next/headers';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { AdminPage, sectionCrumbs } from '@/components/admin/admin-page';
import { Pager, parseOffset } from '@/components/admin/pager';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { FilterChip } from '@/components/ui/filter-chip';
import { Input } from '@/components/ui/input';
import { getUsersOverview } from '@/lib/admin/api';
import { auth } from '@/lib/auth';
import { UsersTable } from './users-table';

const LIMIT = 50;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

// Better Auth's listUsers takes one filter next to the email search.
const FILTERS = {
  admin: { filterField: 'role', filterOperator: 'contains', filterValue: 'admin' },
  banned: { filterField: 'banned', filterOperator: 'eq', filterValue: true },
} as const;
type Filter = keyof typeof FILTERS;
type ListQuery = NonNullable<Parameters<typeof auth.api.listUsers>[0]>['query'];

const usersHref = (q: string, filter?: Filter) => {
  const s = new URLSearchParams({ ...(q && { q }), ...(filter && { filter }) }).toString();
  return s ? `/admin?${s}` : '/admin';
};

export default async function AdminUsers({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const q = typeof sp.q === 'string' ? sp.q.trim() : '';
  const filter = sp.filter === 'admin' || sp.filter === 'banned' ? sp.filter : undefined;
  const offset = parseOffset(sp.offset);
  const [t, h] = [await getTranslations('admin'), await headers()];

  const list = (query: ListQuery) => auth.api.listUsers({ headers: h, query });
  // The header counts ignore the search: they describe the whole user base.
  const count = async (query: ListQuery) => (await list({ ...query, limit: 1 })).total;
  const weekAgo = new Date(Date.now() - WEEK_MS).toISOString();
  const [{ users, total }, all, admins, banned, week] = await Promise.all([
    list({
      limit: LIMIT,
      offset,
      sortBy: 'createdAt',
      sortDirection: 'desc',
      ...(q && { searchField: 'email', searchOperator: 'contains', searchValue: q }),
      ...(filter && FILTERS[filter]),
    }),
    count({}),
    count(FILTERS.admin),
    count(FILTERS.banned),
    count({ filterField: 'createdAt', filterOperator: 'gte', filterValue: weekAgo }),
  ]);
  // Balance, plan, company and last activity live in the API's tables, not Better Auth's.
  // Fail-open: an API error leaves those cells empty, the list still works.
  const overview = users.length ? await getUsersOverview(users.map((u) => u.id)) : null;
  const extra = new Map(overview?.success ? overview.data.users.map((o) => [o.userId, o]) : []);

  const chips = [
    [undefined, t('userList.all'), null],
    ['admin', t('userList.admins'), admins],
    ['banned', t('userList.bannedFilter'), banned],
  ] as const;

  return (
    <AdminPage
      crumbs={sectionCrumbs(t('shell.label'), t('users'))}
      title={t('users')}
      meta={t('userList.summary', { total: all, week, banned })}
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <form className="relative w-full max-w-sm desktop:w-85">
          <Search
            className="text-muted pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            name="q"
            type="search"
            defaultValue={q}
            placeholder={t('userList.searchPlaceholder')}
            aria-label={t('userList.searchPlaceholder')}
            className="pl-9"
          />
          {filter && <input type="hidden" name="filter" value={filter} />}
        </form>
        <nav aria-label={t('userList.filters')} className="flex gap-2">
          {chips.map(([key, label, n]) => (
            <FilterChip
              key={label}
              href={usersHref(q, key)}
              on={filter === key}
              count={n ?? undefined}
            >
              {label}
            </FilterChip>
          ))}
        </nav>
      </div>

      {users.length === 0 ? (
        <EmptyState
          icon={Search}
          title={t('userList.noResults')}
          text={
            q
              ? t('userList.noResultsQuery', { q })
              : filter
                ? t('userList.noResultsFilter')
                : t('userList.noResultsPage')
          }
          action={
            <Button asChild variant="outline" size="sm">
              <Link href="/admin">{t('userList.reset')}</Link>
            </Button>
          }
        />
      ) : (
        <UsersTable users={users} extra={extra} />
      )}

      {users.length > 0 && (
        <Pager
          path="/admin"
          params={{ q, filter }}
          offset={offset}
          limit={LIMIT}
          hasNext={offset + users.length < total}
          shown={users.length}
          total={total}
        />
      )}
    </AdminPage>
  );
}
