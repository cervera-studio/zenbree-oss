'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { type ReactNode, Suspense, useId } from 'react';
import { type Nav, useNav } from '@/lib/home/nav';
import { AccountMenu, type Viewer } from './account-menu';
import { CommandSearch } from './command-search';
import { currentNavId, navEntries } from './nav';
import { NotificationBell } from './notification-bell';
import { countHot, countText, groupLabel, railIcon, railRow } from './side-rail';
import { NewChatLink, RecentChats, useOpenSessionId, WorkspaceSwitcher } from './sidebar-chats';

/** The count beside an entry (#342), or null for none: Liste, Viste salvate and Campagne always show theirs,
 *  Risposte and Da approvare only when something waits. */
function entryCount(id: string, nav: Nav | null): number | null {
  if (!nav) return null;
  if (id === 'lists') return nav.lists;
  if (id === 'views') return nav.views;
  if (id === 'campaigns') return nav.campaigns;
  if (id === 'inbox') return nav.replies || null;
  if (id === 'approvals') return nav.approvals || null;
  return null;
}

/** Out of the link's name, so the entry keeps its label as its name: the link points at it with
 *  `aria-describedby`. A plain number, in the accent for unread replies and messages to approve. */
function EntryCount({ id, count, countId }: { id: string; count: number; countId: string }) {
  const t = useTranslations('nav');
  const n = useFormatter().number(count, 'credits');
  if (id === 'inbox')
    return (
      <>
        <span aria-hidden className={countHot}>
          {n}
        </span>
        <span id={countId} aria-hidden className="sr-only">
          {t('unread', { count })}
        </span>
      </>
    );
  return (
    <span id={countId} aria-hidden className={id === 'approvals' ? countHot : countText}>
      {n}
    </span>
  );
}

type RowsProps = { place: 'main' | 'workspace'; viewer: Viewer; phone: boolean; groupId: string };

function NavRows({ place, viewer, phone, groupId, chatOpen }: RowsProps & { chatOpen: boolean }) {
  const t = useTranslations('nav');
  const current = currentNavId(usePathname(), chatOpen);
  const nav = useNav();
  return navEntries(place, viewer.isAdmin, viewer.isOrgAdmin, phone).map(
    ({ id, href, icon: Icon, label }) => {
      const count = entryCount(id, nav);
      const countId = `${groupId}-${id}`;
      return (
        <Link
          key={id}
          href={href}
          aria-current={id === current ? 'page' : undefined}
          aria-describedby={count === null ? undefined : countId}
          className={railRow(id === current, phone)}
        >
          <Icon className={railIcon(id === current)} aria-hidden />
          <span className="flex-1">{t(label)}</span>
          {count !== null && <EntryCount id={id} count={count} countId={countId} />}
        </Link>
      );
    },
  );
}

/** Reading `?s=` suspends, so the rows fall back to the pathname alone while it loads. */
function OpenAwareRows(props: RowsProps) {
  return <NavRows {...props} chatOpen={useOpenSessionId().sid !== null} />;
}

/**
 * Everything under the brand row, shared by the desktop rail and the phone drawer. `credits`
 * streams in from the server: the rail's card, or on the phone the balance in the account line
 * (the drawer's foot in the design); `phone` gives every row a 48 px target.
 */
export function SidebarNav({
  viewer,
  credits,
  phone = false,
}: {
  viewer: Viewer;
  credits: ReactNode;
  phone?: boolean;
}) {
  const t = useTranslations('nav');
  const groupId = useId();

  const rows = (place: 'main' | 'workspace') => (
    <Suspense fallback={<NavRows {...{ place, viewer, phone, groupId }} chatOpen={false} />}>
      <OpenAwareRows {...{ place, viewer, phone, groupId }} />
    </Suspense>
  );

  return (
    <>
      <div className="flex shrink-0 items-center gap-1">
        <WorkspaceSwitcher userId={viewer.id} phone={phone} />
        {!phone && <NotificationBell />}
      </div>
      <CommandSearch orgAdmin={viewer.isOrgAdmin} phone={phone} />
      <Suspense fallback={<div className={phone ? 'h-12' : 'h-control'} />}>
        <NewChatLink phone={phone} />
      </Suspense>
      <div className="flex shrink-0 flex-col gap-1">{rows('main')}</div>
      <Suspense fallback={<div className="flex-1" />}>
        <RecentChats phone={phone} />
      </Suspense>
      <section aria-labelledby={groupId} className="flex shrink-0 flex-col gap-1">
        <h2 id={groupId} className={groupLabel}>
          {t('workspace')}
        </h2>
        {rows('workspace')}
      </section>
      {!phone && credits}
      <AccountMenu viewer={viewer} credits={phone ? credits : null} />
    </>
  );
}
