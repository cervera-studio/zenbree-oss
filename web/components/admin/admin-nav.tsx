'use client';

import { ArrowUpRight } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { countPill, countText, groupLabel, railIcon, railRow } from '@/components/app/side-rail';
import { cn } from '@/lib/utils';
import {
  ADMIN_NAV,
  type AdminNavEntry,
  adminTools,
  isCurrent,
  type NavCount,
  type NavCounts,
} from './nav-entries';

// Fail-open: with no counters (API down) the rows show none, the pages say so themselves.
export type AdminNavCounts = Partial<NavCounts>;

const COUNT_TONE: Record<NavCount, string | null> = {
  users: null,
  jobsAttention: 'bg-negative-tint text-negative',
  switchesOn: 'bg-warning-tint text-warning',
};

function EntryCount({ kind, n }: { kind: NavCount; n: number }) {
  const t = useTranslations('admin.nav');
  const text = useFormatter().number(n, 'credits');
  const tone = COUNT_TONE[kind];
  // Users is a plain total; the other two call for attention, so they get a pill and a spoken label.
  if (!tone) return <span className={countText}>{text}</span>;
  return (
    <>
      <span aria-hidden className={cn(countPill, tone)}>
        {text}
      </span>
      <span className="sr-only">
        {t(kind === 'jobsAttention' ? 'jobsBadge' : 'settingsBadge', { n })}
      </span>
    </>
  );
}

function NavRow({ entry, n }: { entry: AdminNavEntry; n: number | undefined }) {
  const t = useTranslations('admin.nav');
  const pathname = usePathname();
  const active = isCurrent(entry, pathname);
  const Icon = entry.icon;
  return (
    <Link href={entry.href} aria-current={active ? 'page' : undefined} className={railRow(active)}>
      <Icon className={railIcon(active)} aria-hidden />
      <span className="flex-1">{t(entry.labelKey)}</span>
      {entry.count && n ? <EntryCount kind={entry.count} n={n} /> : null}
    </Link>
  );
}

/** The sections, then the «Strumenti» group; Flower only where it has an address. */
export function AdminNav({ counts, flower }: { counts: AdminNavCounts; flower?: string }) {
  const t = useTranslations('admin');
  return (
    <>
      <div className="flex flex-col gap-1 phone:flex-row phone:flex-wrap">
        {ADMIN_NAV.map((entry) => (
          <NavRow key={entry.href} entry={entry} n={entry.count && counts[entry.count]} />
        ))}
      </div>
      <section
        aria-labelledby="admin-tools"
        className="flex flex-col gap-1 phone:flex-row phone:flex-wrap"
      >
        <h2 id="admin-tools" className={cn(groupLabel, 'phone:hidden')}>
          {t('shell.tools')}
        </h2>
        {adminTools(flower).map(({ labelKey, icon: Icon, href, external }) => {
          const content = (
            <>
              <Icon className={railIcon(false)} aria-hidden />
              <span className="flex-1">{t(`nav.${labelKey}`)}</span>
              {external && <ArrowUpRight className="text-muted size-3.5" aria-hidden />}
            </>
          );
          return external ? (
            <a
              key={labelKey}
              href={href}
              target="_blank"
              rel="noreferrer"
              className={railRow(false)}
            >
              {content}
            </a>
          ) : (
            <Link key={labelKey} href={href} className={railRow(false)}>
              {content}
            </Link>
          );
        })}
      </section>
    </>
  );
}
