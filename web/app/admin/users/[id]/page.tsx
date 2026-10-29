import { headers } from 'next/headers';
import { notFound } from 'next/navigation';
import { getFormatter, getTranslations } from 'next-intl/server';
import { AdjustCreditsForm } from '@/components/admin/adjust-credits-form';
import { AdminPage } from '@/components/admin/admin-page';
import { ApiError } from '@/components/admin/api-error';
import { LedgerTable } from '@/components/admin/ledger';
import { type TabLink, TabLinks } from '@/components/ui/tab-links';
import { getUsage } from '@/lib/admin/api';
import { getChatSessions } from '@/lib/admin/chats';
import { auth } from '@/lib/auth';
import { ChatsTab } from './chats-tab';
import { UsageTab } from './usage-tab';
import { UserHeader } from './user-header';

const TABS = [
  { key: 'usage', labelKey: 'userDetail.usage' },
  { key: 'ledger', labelKey: 'userDetail.ledger' },
  { key: 'adjust', labelKey: 'userDetail.adjust' },
  { key: 'chats', labelKey: 'userDetail.chats' },
] as const;
type Tab = (typeof TABS)[number]['key'];

export default async function UserDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const active: Tab = TABS.find(({ key }) => key === sp.tab)?.key ?? 'usage';
  const user = await auth.api
    .getUser({ headers: await headers(), query: { id } })
    .catch(() => null);
  if (!user) notFound();

  const [t, format, usage, sessions] = await Promise.all([
    getTranslations('admin'),
    getFormatter(),
    getUsage(id),
    getChatSessions(id),
  ]);
  const href = (key: Tab) =>
    key === 'usage' ? `/admin/users/${id}` : `/admin/users/${id}?tab=${key}`;
  // The ledger tab lists the latest 100 and the sessions tab one page of 200: the count is the
  // real total, or «200+» when there are more sessions than the page.
  const counts: Partial<Record<Tab, string>> = {
    ...(usage.success && { ledger: format.number(usage.data.ledgerTotal, 'credits') }),
    ...(sessions.success && {
      chats: `${sessions.data.items.length}${sessions.data.nextOffset === null ? '' : '+'}`,
    }),
  };
  const tabs: TabLink[] = TABS.map(({ key, labelKey }) => ({
    key,
    label: t(labelKey),
    href: href(key),
    count: counts[key],
  }));

  return (
    <AdminPage crumbs={[{ label: t('users'), href: '/admin' }, { label: user.email }]}>
      <UserHeader user={user} usage={usage} adjustHref={href('adjust')} />
      <TabLinks tabs={tabs} active={active} label={t('userDetail.sections')} />

      {active === 'adjust' ? (
        <AdjustCreditsForm
          target={{ user: id }}
          balance={usage.success ? usage.data.balance : null}
        />
      ) : active === 'chats' ? (
        <ChatsTab sessions={sessions} />
      ) : !usage.success ? (
        <ApiError error={usage.error} />
      ) : active === 'ledger' ? (
        <LedgerTable ledger={usage.data.ledger} />
      ) : (
        <UsageTab usage={usage.data} />
      )}
    </AdminPage>
  );
}
