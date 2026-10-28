import {
  Activity,
  ArrowLeft,
  Building2,
  ChartLine,
  type LucideIcon,
  ScrollText,
  SlidersHorizontal,
  Users,
} from 'lucide-react';

/** Which number sits beside an entry: the user total, the jobs that failed or wait on input in the
 *  last day, or the kill switches that are on. */
export type NavCount = 'users' | 'jobsAttention' | 'switchesOn';
export type NavCounts = Record<NavCount, number>;

export type AdminNavEntry = {
  href: string;
  /** Key under `admin.nav` in messages/admin.{en,it}.json. */
  labelKey: 'users' | 'organizations' | 'costs' | 'jobs' | 'settings';
  icon: LucideIcon;
  count?: NavCount;
  /** Other path prefixes that keep the entry lit: a user's detail and chats hang off Utenti. */
  also?: readonly string[];
};

// ponytail: the design also lists a «Chat» entry. There is no chat list page to lead to, so it is
// left out until one exists.
export const ADMIN_NAV: readonly AdminNavEntry[] = [
  {
    href: '/admin',
    labelKey: 'users',
    icon: Users,
    count: 'users',
    also: ['/admin/users', '/admin/chat-sessions'],
  },
  { href: '/admin/organizations', labelKey: 'organizations', icon: Building2 },
  { href: '/admin/costs', labelKey: 'costs', icon: ChartLine },
  { href: '/admin/jobs', labelKey: 'jobs', icon: ScrollText, count: 'jobsAttention' },
  { href: '/admin/settings', labelKey: 'settings', icon: SlidersHorizontal, count: 'switchesOn' },
];

export type AdminToolEntry = {
  labelKey: 'flower' | 'backToApp';
  icon: LucideIcon;
  href: string;
  external?: true;
};

/** The «Strumenti» group. Flower has no address in every deployment, and then no entry. */
export const adminTools = (flower?: string): AdminToolEntry[] => [
  ...(flower
    ? [{ labelKey: 'flower', icon: Activity, href: flower, external: true } as const]
    : []),
  { labelKey: 'backToApp', icon: ArrowLeft, href: '/' },
];

const within = (pathname: string, href: string) =>
  pathname === href || pathname.startsWith(`${href}/`);

/** Whether `pathname` is on this entry. Utenti is the root, so it matches exactly or via `also`. */
export function isCurrent(entry: AdminNavEntry, pathname: string) {
  if (entry.href === '/admin')
    return pathname === entry.href || (entry.also ?? []).some((p) => within(pathname, p));
  return within(pathname, entry.href);
}
