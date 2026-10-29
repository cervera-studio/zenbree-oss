import Link from 'next/link';
import type { useFormatter, useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { SkeletonBar } from '@/components/admin/skeleton-bar';
import { UserActions } from '@/components/admin/user-actions';
import { BanPill, RolePill } from '@/components/admin/user-badges';
import { Avatar } from '@/components/ui/avatar';
import type { UserOverview } from '@/lib/admin/api';
import { formatDay, formatLastSeen } from '@/lib/admin/format';

export type ListUser = {
  id: string;
  email: string;
  name?: string | null;
  role?: string | null;
  banned?: boolean | null;
  banReason?: string | null;
  createdAt: Date;
};

type CellContext = {
  user: ListUser;
  extra: UserOverview | undefined;
  format: ReturnType<typeof useFormatter>;
  reader: { locale: string; timeZone: string };
  now: Date;
  topUpsOnly: string;
};

type Translate = ReturnType<typeof useTranslations<'admin'>>;

type Column = {
  key: string;
  /** The header text, from the `admin` messages. */
  header: (t: Translate) => string;
  num?: boolean;
  /** Tailwind width of the column; the user column takes the rest. */
  width?: string;
  /** The header text stays for screen readers only (a column of icon buttons). */
  srOnly?: boolean;
  cell: (c: CellContext) => ReactNode;
  /** The placeholder the loading page shows in this column. */
  skeleton: ReactNode;
};

/** One table: the page renders `cell`, the loading page renders `skeleton`, so they never drift. */
export const USER_COLUMNS: readonly Column[] = [
  {
    key: 'user',
    header: (t) => t('userList.user'),
    cell: ({ user, extra }) => {
      const sub = [user.name, extra?.company].filter(Boolean).join(' · ');
      return (
        <div className="flex items-center gap-2.5">
          <Avatar user={user} />
          <div className="flex min-w-0 items-baseline gap-2">
            <Link
              href={`/admin/users/${user.id}`}
              className="text-ui-strong text-ink shrink-0 hover:underline"
            >
              {user.email}
            </Link>
            {sub && <span className="text-muted text-small truncate">{sub}</span>}
          </div>
        </div>
      );
    },
    skeleton: (
      <div className="flex items-center gap-2.5">
        <SkeletonBar className="size-5.5 shrink-0 rounded-full" />
        <div className="flex flex-col gap-1.25">
          <SkeletonBar className="w-32" />
          <SkeletonBar className="h-2 w-24" />
        </div>
      </div>
    ),
  },
  {
    key: 'role',
    header: (t) => t('role'),
    width: 'w-22',
    cell: ({ user }) => <RolePill role={user.role} />,
    skeleton: <SkeletonBar className="h-4.5 w-14 rounded-full" />,
  },
  {
    key: 'status',
    header: (t) => t('userList.status'),
    width: 'w-25',
    cell: ({ user }) => <BanPill banned={user.banned} reason={user.banReason} />,
    skeleton: <SkeletonBar className="h-4.5 w-14 rounded-full" />,
  },
  {
    key: 'plan',
    header: (t) => t('userList.plan'),
    width: 'w-30',
    cell: ({ extra, topUpsOnly }) =>
      extra?.planId ? (
        <span className="capitalize">{extra.planId}</span>
      ) : extra?.hasPurchases ? (
        topUpsOnly
      ) : (
        '—'
      ),
    skeleton: <SkeletonBar className="w-16" />,
  },
  {
    key: 'balance',
    header: (t) => t('userDetail.balance'),
    num: true,
    width: 'w-22',
    cell: ({ extra, format }) =>
      extra?.balance == null ? '—' : format.number(extra.balance, 'credits'),
    skeleton: (
      <div className="flex justify-end">
        <SkeletonBar className="w-10" />
      </div>
    ),
  },
  {
    key: 'lastActive',
    header: (t) => t('userList.lastActive'),
    width: 'w-32',
    cell: ({ extra, reader, now }) =>
      extra?.lastActiveAt ? formatLastSeen(extra.lastActiveAt, reader, now) : '—',
    skeleton: <SkeletonBar className="w-20" />,
  },
  {
    key: 'createdAt',
    header: (t) => t('createdAt'),
    width: 'w-30',
    cell: ({ user, reader }) => formatDay(user.createdAt, reader),
    skeleton: <SkeletonBar className="w-20" />,
  },
  {
    key: 'actions',
    header: (t) => t('userList.actions'),
    width: 'w-11',
    srOnly: true,
    cell: ({ user }) => (
      <UserActions
        user={{ id: user.id, email: user.email, role: user.role, banned: user.banned }}
      />
    ),
    skeleton: <SkeletonBar className="w-3.5" />,
  },
];
