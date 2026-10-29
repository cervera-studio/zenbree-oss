import {
  Ban,
  Bookmark,
  CircleCheck,
  CircleHelp,
  Coins,
  Inbox,
  LayoutGrid,
  ListFilter,
  type LucideIcon,
  MessageSquare,
  Plug,
  Send,
  Settings,
  Shield,
  Table,
  UserRound,
  Users,
  Workflow,
} from 'lucide-react';
import type { Locale } from '@/i18n/config';
import { landingUrl } from '@/lib/landing';

/** Inset, so the scrolling chat list and the drawer don't clip it. */
export const rowFocus =
  'outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-inset';

/** Where an entry renders: the sidebar's main group, its «Workspace» group, or the account menu. */
type NavPlace = 'main' | 'workspace' | 'account';

type NavEntry = {
  id: string;
  href: string;
  icon: LucideIcon;
  /** Key under `nav` in messages/{en,it}.json. */
  label:
    | 'overview'
    | 'searches'
    | 'lists'
    | 'views'
    | 'campaigns'
    | 'automations'
    | 'segments'
    | 'approvals'
    | 'inbox'
    | 'exclusions'
    | 'team'
    | 'integrations'
    | 'settings'
    | 'profile'
    | 'billing'
    | 'help'
    | 'backoffice';
  place: NavPlace;
  /** Path prefixes that make the entry the current page; account entries are never current. */
  match: readonly string[];
  adminOnly?: true;
  /** Only for an admin of the active organization (#308). */
  orgAdminOnly?: true;
  /** Only in the phone drawer (#342). */
  phoneOnly?: true;
  /** `href` is an anchor on the marketing site, resolved per locale; no site set hides the entry. */
  onLanding?: true;
};

// The new-chat button and the recent chats are the home entries: they live in sidebar-chats.tsx
// because their current state needs the `?s=` search param.
const NAV: readonly NavEntry[] = [
  { id: 'home', href: '/', icon: LayoutGrid, label: 'overview', place: 'main', match: ['/'] },
  // A chat brief is a search in this product: the page lists them all.
  {
    id: 'searches',
    href: '/chats',
    icon: MessageSquare,
    label: 'searches',
    place: 'main',
    match: ['/chats'],
  },
  { id: 'lists', href: '/lists', icon: Table, label: 'lists', place: 'main', match: ['/lists'] },
  { id: 'views', href: '/views', icon: Bookmark, label: 'views', place: 'main', match: ['/views'] },
  {
    id: 'campaigns',
    href: '/campaigns',
    icon: Send,
    label: 'campaigns',
    place: 'main',
    match: ['/campaigns'],
  },
  {
    id: 'automations',
    href: '/automations',
    icon: Workflow,
    label: 'automations',
    place: 'main',
    match: ['/automations'],
  },
  {
    id: 'segments',
    href: '/segments',
    icon: ListFilter,
    label: 'segments',
    place: 'main',
    match: ['/segments'],
  },
  // The campaigns page shows each campaign's messages waiting for approval; never current, Campagne is.
  {
    id: 'approvals',
    href: '/campaigns',
    icon: CircleCheck,
    label: 'approvals',
    place: 'main',
    match: [],
    phoneOnly: true,
  },
  { id: 'inbox', href: '/inbox', icon: Inbox, label: 'inbox', place: 'main', match: ['/inbox'] },
  // The specific settings pages come before Impostazioni, which owns the rest of /settings.
  {
    id: 'exclusions',
    href: '/settings/exclusions',
    icon: Ban,
    label: 'exclusions',
    place: 'workspace',
    match: ['/settings/exclusions'],
  },
  {
    id: 'team',
    href: '/settings/team',
    icon: Users,
    label: 'team',
    place: 'workspace',
    match: ['/settings/team'],
  },
  {
    id: 'integrations',
    href: '/settings/integrations',
    icon: Plug,
    label: 'integrations',
    place: 'workspace',
    match: ['/settings/integrations'],
  },
  // Settings and its tabs (billing is one of them) sit behind a single entry (#171).
  {
    id: 'settings',
    href: '/settings',
    icon: Settings,
    label: 'settings',
    place: 'workspace',
    match: ['/settings', '/billing'],
  },
  {
    id: 'profile',
    // Not /settings: on a phone that is the settings menu, not the profile.
    href: '/settings/profile',
    icon: UserRound,
    label: 'profile',
    place: 'account',
    match: [],
  },
  {
    id: 'billing',
    href: '/billing',
    icon: Coins,
    label: 'billing',
    place: 'account',
    match: [],
    orgAdminOnly: true,
  },
  // The landing's FAQ, which ends on the support address (#434); no in-app help page exists yet.
  {
    id: 'help',
    href: '#faq',
    onLanding: true,
    icon: CircleHelp,
    label: 'help',
    place: 'account',
    match: [],
  },
  {
    id: 'admin',
    href: '/admin',
    icon: Shield,
    label: 'backoffice',
    place: 'account',
    match: [],
    adminOnly: true,
  },
];

export function navEntries(
  place: NavPlace,
  isAdmin: boolean,
  isOrgAdmin: boolean,
  phone = false,
  locale: Locale = 'en',
): NavEntry[] {
  return NAV.filter(
    (e) =>
      e.place === place &&
      (isAdmin || !e.adminOnly) &&
      (isOrgAdmin || !e.orgAdminOnly) &&
      (phone || !e.phoneOnly),
  ).flatMap((e) => {
    if (!e.onLanding) return [e];
    const url = landingUrl(locale, e.href);
    return url ? [{ ...e, href: url.href }] : [];
  });
}

const startsSegment = (pathname: string, prefix: string) =>
  pathname === prefix || pathname.startsWith(`${prefix}/`);

/** The one entry the pathname belongs to, or null (pages outside the nav). A chat open on home
 *  (`/?s=`) is the current entry itself, in the recent chats, so home is not. */
export function currentNavId(pathname: string, chatOpen = false): string | null {
  if (chatOpen && pathname === '/') return null;
  return NAV.find((e) => e.match.some((p) => startsSegment(pathname, p)))?.id ?? null;
}
