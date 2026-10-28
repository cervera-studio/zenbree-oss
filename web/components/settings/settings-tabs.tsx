'use client';

import { ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useEffect, useRef } from 'react';
import { TabLinks } from '@/components/ui/tab-links';

type SettingsTab = { readonly href: string; readonly key: string; readonly orgAdminOnly?: true };

const ALL_TABS = [
  { href: '/settings', key: 'profile' },
  { href: '/settings/notifications', key: 'notifications' },
  { href: '/settings/senders', key: 'senders' },
  { href: '/settings/integrations', key: 'integrations' },
  { href: '/settings/memory', key: 'memory' },
  { href: '/settings/exclusions', key: 'exclusions' },
  { href: '/settings/client', key: 'client' },
  { href: '/settings/team', key: 'team' },
  { href: '/settings/api-keys', key: 'apiKeys', orgAdminOnly: true },
  { href: '/settings/webhooks', key: 'webhooks', orgAdminOnly: true },
  { href: '/billing', key: 'billing', orgAdminOnly: true },
] as const satisfies readonly SettingsTab[];

// A member of the organization does not see what only its admins can use (#308); the API refuses it anyway.
export const tabsFor = (orgAdmin: boolean) =>
  ALL_TABS.filter((tab) => orgAdmin || !('orgAdminOnly' in tab));

const isWithin = (pathname: string, href: string) =>
  pathname === href || pathname.startsWith(`${href}/`);

function activeTabHref(pathname: string) {
  return ALL_TABS.filter(({ href }) => isWithin(pathname, href)).sort(
    (a, b) => b.href.length - a.href.length,
  )[0]?.href;
}

export function SettingsTabs({ orgAdmin }: { orgAdmin: boolean }) {
  const t = useTranslations('settings.tabs');
  const pathname = usePathname();
  const nav = useRef<HTMLDivElement>(null);
  const activeHref = activeTabHref(pathname);
  useEffect(() => {
    nav.current
      ?.querySelector(`[href="${activeHref}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [activeHref]);
  const tabs = tabsFor(orgAdmin).map(({ href, key }) => ({ key, href, label: t(key) }));
  return (
    <div ref={nav} className="phone:hidden">
      <TabLinks
        label={t('label')}
        tabs={tabs}
        active={tabs.find(({ href }) => href === activeHref)?.key ?? ''}
      />
    </div>
  );
}

/** The phone's settings index: one full-width row per page, in the tabs' order. */
export function SettingsMenu({ orgAdmin }: { orgAdmin: boolean }) {
  const t = useTranslations('settings.tabs');
  return (
    <nav aria-label={t('label')} className="desktop:hidden">
      <ul className="border-line bg-ground flex flex-col rounded-lg border">
        {tabsFor(orgAdmin).map(({ href, key }) => (
          <li key={href} className="border-hair border-b last:border-0">
            {/* /settings is this menu on a phone, so Profile has a page of its own. */}
            <Link
              href={href === '/settings' ? '/settings/profile' : href}
              className="text-body flex min-h-14 items-center gap-3 px-4 font-medium"
            >
              <span className="grow">{t(key)}</span>
              <ChevronRight aria-hidden className="size-4 text-muted" />
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
