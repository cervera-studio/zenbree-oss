'use client';

import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { AppPage } from '@/components/app/page';
import { cn } from '@/lib/utils';
import { SettingsMenu, SettingsTabs } from './settings-tabs';

// Desktop: the tabs over the page, /settings being Profile. Phone: /settings is the menu alone, and
// every page opens full screen under a back link to it. CSS swaps the two, so nothing flashes.
// ponytail: /settings still renders Profile (hidden) on a phone; split the route if its fetch hurts.
export function SettingsShell({
  orgAdmin,
  meta,
  children,
}: {
  orgAdmin: boolean;
  /** The line under the title: the client's company and the user's email. */
  meta?: string;
  children: ReactNode;
}) {
  const t = useTranslations('settings.tabs');
  const atMenu = usePathname() === '/settings';
  return (
    <div className="flex min-h-full flex-col">
      <AppPage
        crumbs={[{ label: t('label') }]}
        crumbsLabel={t('breadcrumb')}
        title={t('label')}
        titleSrOnlyOnPhone={!atMenu}
        meta={meta}
        className="flex-1 gap-5"
      >
        {!atMenu && (
          <Link
            href="/settings"
            className="text-accent-ink text-ui-strong inline-flex min-h-11 w-fit items-center gap-1.5 desktop:hidden"
          >
            <ArrowLeft aria-hidden className="size-4" /> {t('back')}
          </Link>
        )}
        <SettingsTabs orgAdmin={orgAdmin} />
        {atMenu && <SettingsMenu orgAdmin={orgAdmin} />}
        <div className={cn('contents', atMenu && 'phone:hidden')}>{children}</div>
      </AppPage>
    </div>
  );
}
