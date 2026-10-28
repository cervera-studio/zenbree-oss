import Link from 'next/link';
import { getFormatter, getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import { ImpersonateButton, UserActions } from '@/components/admin/user-actions';
import { BanPill, RolePill } from '@/components/admin/user-badges';
import { Avatar } from '@/components/ui/avatar';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { getUsage } from '@/lib/admin/api';
import { formatDay } from '@/lib/admin/format';

type UsageResult = Awaited<ReturnType<typeof getUsage>>;

type HeaderUser = {
  id: string;
  email: string;
  name?: string | null;
  role?: string | null;
  banned?: boolean | null;
  banReason?: string | null;
  createdAt: Date | string;
};

export async function UserHeader({
  user,
  usage,
  adjustHref,
}: {
  user: HeaderUser;
  usage: UsageResult;
  adjustHref: string;
}) {
  const [t, locale, timeZone, format] = [
    await getTranslations('admin.userDetail'),
    await getLocale(),
    await getTimeZone(),
    await getFormatter(),
  ];
  return (
    <header className="flex flex-wrap items-center gap-4">
      <Avatar user={user} size="lg" />
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1.5">
        <h1 className="text-title truncate">{user.name || user.email}</h1>
        <div className="text-ui text-muted flex flex-wrap items-center gap-x-2 gap-y-1">
          <span>
            {[
              user.email,
              usage.success && usage.data.company,
              t('since', { date: formatDay(user.createdAt, { locale, timeZone }) }),
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
          <RolePill role={user.role} />
          <BanPill banned={user.banned} reason={user.banReason} />
          <Plan usage={usage} />
        </div>
        <Organizations usage={usage} />
      </div>
      {usage.success && (
        <p className="flex flex-col items-end gap-0.5">
          <span className="text-label text-muted uppercase">{t('creditBalance')}</span>
          <span className="text-metric font-mono tabular-nums">
            {usage.data.balance === null ? '—' : format.number(usage.data.balance, 'credits')}
          </span>
        </p>
      )}
      <div className="flex items-center gap-2">
        <ImpersonateButton userId={user.id} size="sm" />
        <Button asChild size="sm">
          <Link href={adjustHref}>{t('adjust')}</Link>
        </Button>
        <UserActions
          look="header"
          user={{ id: user.id, email: user.email, role: user.role, banned: user.banned }}
          jobsHref={`/admin/jobs?user=${user.id}`}
        />
      </div>
    </header>
  );
}

/** Their own organization's live plan and when it renews or ends (#352). */
async function Plan({ usage }: { usage: UsageResult }) {
  const [t, locale, timeZone] = [
    await getTranslations('admin.userDetail'),
    await getLocale(),
    await getTimeZone(),
  ];
  if (!usage.success) return null;
  const sub = usage.data.subscription;
  if (!sub) return <Tag tone="neutral">{t('noPlan')}</Tag>;
  const end = sub.cancelAt ?? sub.currentPeriodEnd;
  const plan = sub.planId ?? '?';
  return (
    <Tag tone="accent">
      {[
        plan.charAt(0).toUpperCase() + plan.slice(1),
        end &&
          t(sub.cancelAt ? 'planEnds' : 'planRenews', {
            date: formatDay(end, { locale, timeZone }),
          }),
      ]
        .filter(Boolean)
        .join(' · ')}
    </Tag>
  );
}

/** The organizations the user belongs to (#327), their own first. */
async function Organizations({ usage }: { usage: UsageResult }) {
  const t = await getTranslations('admin');
  const orgs = usage.success ? usage.data.organizations : [];
  if (orgs.length === 0) return null;
  return (
    <p className="text-muted text-ui flex flex-wrap gap-x-2 gap-y-1">
      <span>{t('userDetail.organizations')}</span>
      {orgs.map((o) => (
        <Link
          key={o.id}
          href={`/admin/organizations/${o.id}`}
          className="text-accent-ink text-ui-strong hover:underline"
        >
          {o.name} ({t(`orgDetail.roles.${o.role === 'admin' ? 'admin' : 'member'}`)})
        </Link>
      ))}
    </p>
  );
}
