import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getFormatter, getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import { AdjustCreditsForm } from '@/components/admin/adjust-credits-form';
import { AdminPage } from '@/components/admin/admin-page';
import { ApiError, Empty } from '@/components/admin/api-error';
import { LedgerTable, PurchasesTable } from '@/components/admin/ledger';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { getOrganization, type Organization } from '@/lib/admin/api';
import { formatDay } from '@/lib/admin/format';
import { cn } from '@/lib/utils';

const TABS = ['members', 'clients', 'ledger', 'billing', 'adjust'] as const;
type Tab = (typeof TABS)[number];

export default async function OrganizationDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const tab: Tab = TABS.find((x) => x === sp.tab) ?? 'members';
  const [t, locale, timeZone, format, res] = await Promise.all([
    getTranslations('admin'),
    getLocale(),
    getTimeZone(),
    getFormatter(),
    getOrganization(id),
  ]);
  if (!res.success && [404, 422].includes(res.error.status)) notFound();
  const tabHref = (key: Tab) =>
    key === 'members' ? `/admin/organizations/${id}` : `/admin/organizations/${id}?tab=${key}`;

  return (
    <AdminPage
      crumbs={[
        { label: t('organizations.title'), href: '/admin/organizations' },
        { label: res.success ? res.data.name : id },
      ]}
    >
      {!res.success ? (
        <ApiError error={res.error} request={`GET /v1/admin/organizations/${id}`} />
      ) : (
        <>
          <header className="flex flex-wrap items-center gap-5">
            <div className="flex min-w-0 grow flex-col gap-1.5">
              <h1 className="truncate text-2xl font-bold">{res.data.name}</h1>
              <p className="text-muted text-sm">
                {t('orgDetail.summary', {
                  members: res.data.members.length,
                  clients: res.data.projects.length,
                })}{' '}
                ·{' '}
                {t('userDetail.since', {
                  date: formatDay(res.data.createdAt, { locale, timeZone }),
                })}
              </p>
            </div>
            <p className="flex flex-col items-end gap-0.5">
              <span className="text-label text-muted uppercase">{t('userDetail.balance')}</span>
              <span className="text-metric font-mono tabular-nums">
                {res.data.balance == null ? '—' : format.number(res.data.balance, 'credits')}
              </span>
              <span className="text-muted text-xs">{t('userDetail.creditsUnit')}</span>
            </p>
            <Button asChild>
              <Link href={tabHref('adjust')}>{t('userDetail.adjust')}</Link>
            </Button>
          </header>

          <nav
            aria-label={t('userDetail.sections')}
            className="flex gap-1 overflow-x-auto overflow-y-hidden scrollbar-none shadow-tab-rule"
          >
            {TABS.map((key) => (
              <Link
                key={key}
                href={tabHref(key)}
                aria-current={key === tab ? 'page' : undefined}
                className={cn(
                  'inline-flex h-11 shrink-0 items-center gap-1.5 border-b-2 px-3 text-sm whitespace-nowrap',
                  '-outline-offset-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent',
                  key === tab
                    ? 'border-accent-ink font-semibold text-ink'
                    : 'border-transparent font-medium text-muted',
                )}
              >
                {t(`orgDetail.tabs.${key}`)}
                {key === 'ledger' && (
                  <span className="text-muted font-normal tabular-nums">
                    {format.number(res.data.ledgerTotal, 'credits')}
                  </span>
                )}
              </Link>
            ))}
          </nav>

          {tab === 'adjust' ? (
            <AdjustCreditsForm target={{ organization: id }} balance={res.data.balance} />
          ) : tab === 'ledger' ? (
            <LedgerTable ledger={res.data.ledger} who />
          ) : tab === 'billing' ? (
            <Billing org={res.data} />
          ) : tab === 'clients' ? (
            <Clients org={res.data} />
          ) : (
            <Members org={res.data} />
          )}
        </>
      )}
    </AdminPage>
  );
}

async function Members({ org }: { org: Organization }) {
  const [t, locale, timeZone] = [
    await getTranslations('admin'),
    await getLocale(),
    await getTimeZone(),
  ];
  return (
    <Table frame>
      <TableHeader>
        <TableRow>
          <TableHead>{t('userList.user')}</TableHead>
          <TableHead>{t('role')}</TableHead>
          <TableHead>{t('orgDetail.joined')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {org.members.map((m) => (
          <TableRow key={m.userId}>
            <TableCell>
              <div className="flex min-w-0 flex-col">
                <Link
                  href={`/admin/users/${m.userId}`}
                  className="text-accent-ink truncate font-semibold hover:underline"
                >
                  {m.email}
                </Link>
                {m.name && <span className="text-muted truncate text-xs">{m.name}</span>}
              </div>
            </TableCell>
            <TableCell>
              <Tag tone={m.role === 'admin' ? 'positive' : 'neutral'}>
                {t(`orgDetail.roles.${m.role === 'admin' ? 'admin' : 'member'}`)}
              </Tag>
            </TableCell>
            <TableCell>{formatDay(m.createdAt, { locale, timeZone })}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

async function Clients({ org }: { org: Organization }) {
  const [t, format] = [await getTranslations('admin'), await getFormatter()];
  if (org.projects.length === 0) return <Empty />;
  return (
    <Table frame>
      <TableHeader>
        <TableRow>
          <TableHead>{t('orgDetail.client')}</TableHead>
          <TableHead>{t('orgDetail.cap')}</TableHead>
          <TableHead num>{t('orgDetail.monthSpend')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {org.projects.map((p) => (
          <TableRow key={p.id}>
            <TableCell className="font-semibold">{p.name}</TableCell>
            <TableCell>
              {p.creditCap && p.creditCapMode
                ? t('orgDetail.capValue', { cap: p.creditCap, mode: p.creditCapMode })
                : t('orgDetail.noCap')}
            </TableCell>
            <TableCell
              num
              className={cn(p.creditCap && p.creditsThisMonth >= p.creditCap && 'text-negative')}
            >
              {format.number(p.creditsThisMonth, 'credits')}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

async function Billing({ org }: { org: Organization }) {
  const [t, locale, timeZone] = [
    await getTranslations('admin'),
    await getLocale(),
    await getTimeZone(),
  ];
  const sub = org.subscription;
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h2 className="font-bold">{t('orgDetail.subscription')}</h2>
        {sub ? (
          <dl className="bg-ground grid max-w-md grid-cols-2 gap-x-4 gap-y-2 rounded-xl border p-4 text-sm">
            <dt className="text-muted">{t('userList.plan')}</dt>
            <dd className="capitalize">{sub.planId ?? '—'}</dd>
            <dt className="text-muted">{t('userList.status')}</dt>
            <dd>{sub.status}</dd>
            <dt className="text-muted">{t('orgDetail.periodEnd')}</dt>
            <dd>
              {sub.currentPeriodEnd ? formatDay(sub.currentPeriodEnd, { locale, timeZone }) : '—'}
              {sub.cancelAt &&
                ` · ${t('orgDetail.cancels', { date: formatDay(sub.cancelAt, { locale, timeZone }) })}`}
            </dd>
          </dl>
        ) : (
          <p className="text-muted text-sm">{t('orgDetail.noSubscription')}</p>
        )}
      </section>
      <section className="flex flex-col gap-3">
        <h2 className="font-bold">{t('userDetail.purchases')}</h2>
        <PurchasesTable purchases={org.purchases} />
      </section>
    </div>
  );
}
