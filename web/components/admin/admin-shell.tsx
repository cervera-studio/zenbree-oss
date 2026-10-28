import { Shield } from 'lucide-react';
import { headers } from 'next/headers';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import type { ReactNode } from 'react';
import { rowFocus } from '@/components/app/nav';
import { Logo } from '@/components/logo';
import { Avatar } from '@/components/ui/avatar';
import { getBadges } from '@/lib/admin/api';
import { auth } from '@/lib/auth';
import { logger } from '@/lib/logger';
import { cn } from '@/lib/utils';
import { AdminNav, type AdminNavCounts } from './admin-nav';

async function userTotal() {
  try {
    return (await auth.api.listUsers({ headers: await headers(), query: { limit: 1 } })).total;
  } catch (error) {
    // Fail-open: the row shows no count, and the log says why.
    logger.error('admin.users.total.failed', { error });
    return undefined;
  }
}

/** The back office frame: a rail with the sections and who is signed in, and beside it the
 *  production band over the page; each page brings its own topbar (`AdminPage`). */
export async function AdminShell({
  viewer,
  children,
}: {
  viewer: { name: string; email: string };
  children: ReactNode;
}) {
  const [t, badges, users] = await Promise.all([
    getTranslations('admin'),
    getBadges(),
    userTotal(),
  ]);
  const counts: AdminNavCounts = {
    ...(users !== undefined && { users }),
    ...(badges.success && {
      jobsAttention: badges.data.jobsAttention,
      switchesOn: badges.data.switchesOn,
    }),
  };

  return (
    <div className="flex h-dvh overflow-hidden phone:h-auto phone:flex-col phone:overflow-visible">
      <nav
        aria-label={t('shell.label')}
        className="bg-side flex w-sidebar shrink-0 flex-col gap-1 border-r px-2.5 py-3 phone:w-full phone:border-r-0 phone:border-b"
      >
        <div className="flex h-9 items-center gap-2 px-1.5">
          <Link href="/admin" className={cn('flex items-center gap-2 rounded-md', rowFocus)}>
            <Logo markOnly />
            <span className="text-ui-strong">{t('shell.label')}</span>
          </Link>
          <span className="bg-warning-tint text-warning text-data-sm ml-auto inline-flex h-4.5 items-center rounded-sm px-1.25 font-mono">
            {t('shell.tag')}
          </span>
        </div>
        <AdminNav counts={counts} flower={process.env.NEXT_PUBLIC_FLOWER_URL} />
        <div className="flex-1 phone:hidden" />
        <div className="flex items-center gap-2 px-1.5 pt-2.5 phone:hidden">
          <Avatar user={{ ...viewer, role: 'admin' }} size="md" />
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="text-ui-strong truncate">{viewer.name}</span>
            <span className="text-small text-muted truncate">
              {t('shell.role', { email: viewer.email })}
            </span>
          </span>
        </div>
      </nav>
      {/* On a phone the column dissolves, so the band lands at the top, over the stacked rail. */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col phone:contents">
        {/* Always on screen: there is no staging back office, every switch here is live. */}
        <p
          role="note"
          className="bg-warning-tint text-warning text-small flex h-7.5 shrink-0 items-center gap-2 px-5 phone:order-first"
        >
          <Shield className="size-3.5 shrink-0" aria-hidden />
          {t('shell.production')}
        </p>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain phone:overflow-visible">
          {children}
        </div>
      </div>
    </div>
  );
}
