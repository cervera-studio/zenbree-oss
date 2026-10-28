/** Kinds from `api/db/schema_notifications.sql`. */
export type NotificationKind =
  | 'reply_urgent'
  | 'reply'
  | 'search_done'
  | 'low_credits'
  | 'payment_failed'
  | 'approval_needed'
  | 'search_failed'
  | 'agent_question'
  | 'linkedin_accepted'
  | 'bounces_high'
  | 'mailbox_error'
  | 'plan_renewed';

export interface AppNotification {
  id: string;
  kind: NotificationKind;
  href: string;
  data: {
    name?: string;
    // reply labels from `api/db/schema_inbound.sql`
    label?: 'interested' | 'meeting_request' | 'not_interested' | 'not_now' | 'other';
    channel?: string;
    unverified?: boolean;
    credits?: number;
    // a chat turn's kinds (#215): the chat's title, its lists' sizes, the per-row credits given back
    chat?: string;
    people?: number;
    companies?: number;
    refunded?: number;
    // search_failed: the stopped stage's progress
    done?: number;
    units?: number;
    // agent_question: what the agent is waiting for
    reason?: 'plan' | 'funnel' | null;
    // approval_needed: messages held and their campaign
    count?: number;
    campaign?: string;
    // low_credits, payment_failed (#323): which organization's wallet
    organization?: string;
    // bounces_high: people the campaign could not write to, out of those it tried
    invalid?: number;
    attempted?: number;
    // #747: hard bounces paused the campaign; `invalid` is its bounced emails, `attempted` its sent ones
    paused?: boolean;
    // mailbox_error: the account's label and its provider (google, microsoft, unipile)
    mailbox?: string;
    provider?: string;
    // plan_renewed: the plan, and last period's unused credits that expired
    plan?: string;
    expired?: number;
  };
  /** A reply's opening words, computed by the API from the message; never part of `data`. */
  quote?: string;
  read: boolean;
  createdAt: string;
  browser: boolean;
}

export type DayGroup = { day: 'today' | 'yesterday' | string; items: AppNotification[] };

function localDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Newest first, one group per local calendar day: 'today', 'yesterday', else `YYYY-MM-DD`. */
export function groupByDay(items: AppNotification[], now = new Date()): DayGroup[] {
  const today = localDay(now);
  const yesterday = localDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const groups: DayGroup[] = [];
  const sorted = [...items].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  for (const item of sorted) {
    const d = localDay(new Date(item.createdAt));
    const day = d === today ? 'today' : d === yesterday ? 'yesterday' : d;
    const last = groups.at(-1);
    if (last?.day === day) last.items.push(item);
    else groups.push({ day, items: [item] });
  }
  return groups;
}

// Any origin serves: only whether the href stays on it matters. Parsing it the way the browser will
// (tabs and newlines dropped, `\` read as `/`) catches what a pattern on the raw string misses.
const SAME_ORIGIN = 'https://app.zenbree.invalid';

/** Only in-app paths are followed: an item can never send the user off-site. */
export function safeHref(href: string) {
  try {
    const url = new URL(href, SAME_ORIGIN);
    // `/.//evil.test` normalises to the path `//evil.test`, which a redirect reads as another host.
    if (url.origin !== SAME_ORIGIN || url.pathname.startsWith('//')) return '/';
    return url.pathname + url.search + url.hash;
  } catch {
    return '/';
  }
}

type KindGroup = 'reply' | 'decide' | 'event';

const KIND_GROUP: Record<NotificationKind, KindGroup> = {
  reply: 'reply',
  reply_urgent: 'reply',
  approval_needed: 'decide',
  agent_question: 'decide',
  search_done: 'event',
  search_failed: 'event',
  low_credits: 'event',
  payment_failed: 'event',
  linkedin_accepted: 'reply',
  bounces_high: 'event',
  mailbox_error: 'event',
  plan_renewed: 'event',
};

export type Filter = 'all' | 'unread' | 'reply' | 'decide';

export const FILTERS: readonly Filter[] = ['all', 'unread', 'reply', 'decide'];

export function filterItems(items: AppNotification[], filter: Filter) {
  switch (filter) {
    case 'all':
      return items;
    case 'unread':
      return items.filter((n) => !n.read);
    default:
      return items.filter((n) => KIND_GROUP[n.kind] === filter);
  }
}

/** The chips' counts, all of unread items. `unread` is the server's total: it can exceed the loaded page. */
export function filterCounts(items: AppNotification[], unread: number): Record<Filter, number> {
  const fresh = items.filter((n) => !n.read);
  const inGroup = (g: KindGroup) => fresh.filter((n) => KIND_GROUP[n.kind] === g).length;
  return { all: 0, unread, reply: inGroup('reply'), decide: inGroup('decide') };
}

export type Action = {
  label: 'reply' | 'open' | 'accept' | 'review' | 'retry';
  href: string;
  primary: boolean;
};

// Behind a /switch link the fragment goes inside `next`: the redirect would drop one outside it.
const draft = (n: AppNotification) =>
  n.href.startsWith('/switch?') ? `${n.href}%23reply` : `${n.href}#reply`;

/** What an item offers besides its title link. Accepting a meeting opens the same draft as replying. */
export const ACTIONS: Record<NotificationKind, (n: AppNotification) => Action[]> = {
  reply: (n) => [
    { label: 'reply', href: draft(n), primary: true },
    { label: 'open', href: n.href, primary: false },
  ],
  reply_urgent: (n) =>
    n.data.label === 'meeting_request'
      ? [{ label: 'accept', href: draft(n), primary: true }]
      : [
          { label: 'reply', href: draft(n), primary: true },
          { label: 'open', href: n.href, primary: false },
        ],
  approval_needed: (n) => [{ label: 'review', href: n.href, primary: true }],
  search_failed: (n) => [{ label: 'retry', href: n.href, primary: false }],
  search_done: () => [],
  agent_question: () => [],
  low_credits: () => [],
  payment_failed: () => [],
  linkedin_accepted: () => [],
  bounces_high: () => [],
  mailbox_error: () => [],
  plan_renewed: () => [],
};

export type Channel = 'email' | 'linkedin' | 'meeting';

/** The avatar badge: a verified meeting request shows the calendar whatever the channel; an unverified one only its channel. */
export function channelOf(n: AppNotification): Channel | null {
  if (n.data.label === 'meeting_request' && !n.data.unverified) return 'meeting';
  return n.data.channel === 'email' || n.data.channel === 'linkedin' ? n.data.channel : null;
}
