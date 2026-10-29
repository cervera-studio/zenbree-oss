'use client';

import {
  Bell,
  CalendarDays,
  Check,
  CircleAlert,
  ClipboardCheck,
  Coins,
  CreditCard,
  Mail,
  MailWarning,
  Send,
  SlidersHorizontal,
  Sparkles,
  Unplug,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { Popover } from 'radix-ui';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api/browser';
import { initials } from '@/lib/initials';
import {
  applyEvent,
  type Feed,
  followFeed,
  type NotificationEvent,
} from '@/lib/notifications/feed';
import {
  ACTIONS,
  type AppNotification,
  type Channel,
  channelOf,
  FILTERS,
  type Filter,
  filterCounts,
  filterItems,
  groupByDay,
  type NotificationKind,
  safeHref,
} from '@/lib/notifications/group';
import { syncPush } from '@/lib/notifications/push';
import { cn } from '@/lib/utils';
import { showBrowser } from './notification-browser';
import { NotificationLink } from './notification-link';
import { useText } from './notification-text';
import { usePhone } from './use-phone';

/** Bell panel (#173). Rendered in the sidebar's brand row and in the phone top bar; only the
 *  visible one runs, so a tab holds one stream (#242), which is also how the API knows Zenbree is open. */
export function NotificationBell({ phone = false }: { phone?: boolean }) {
  return usePhone() === phone ? <BellPanel phone={phone} /> : null;
}

const TILE: Partial<Record<NotificationKind, { tone: string; icon: typeof Bell }>> = {
  search_done: { tone: 'bg-positive/15 text-positive', icon: ClipboardCheck },
  approval_needed: { tone: 'bg-warning/15 text-warning', icon: Send },
  search_failed: { tone: 'bg-negative/15 text-negative', icon: CircleAlert },
  agent_question: { tone: 'bg-accent/15 text-accent-ink', icon: Sparkles },
  low_credits: { tone: 'bg-warning/15 text-warning', icon: Coins },
  payment_failed: { tone: 'bg-negative/15 text-negative', icon: CreditCard },
  bounces_high: { tone: 'bg-warning/15 text-warning', icon: MailWarning },
  mailbox_error: { tone: 'bg-negative/15 text-negative', icon: Unplug },
  plan_renewed: { tone: 'bg-warning/15 text-warning', icon: Coins },
};

const BADGE: Record<Channel, string> = {
  email: 'bg-accent/15 text-accent-ink',
  linkedin: 'bg-accent/15 text-accent-ink',
  meeting: 'bg-positive/15 text-positive',
};

function ChannelGlyph({ channel }: { channel: Channel }) {
  if (channel === 'email') return <Mail className="size-2.5" />;
  if (channel === 'meeting') return <CalendarDays className="size-2.5" />;
  return <span className="text-3xs font-bold">in</span>;
}

function KindIcon({ n }: { n: AppNotification }) {
  const t = useTranslations('notifications.channel');
  const tile = TILE[n.kind];
  if (tile) {
    return (
      <span
        aria-hidden
        className={cn('flex size-9 shrink-0 items-center justify-center rounded-md', tile.tone)}
      >
        <tile.icon className="size-4.5" />
      </span>
    );
  }
  const channel = channelOf(n);
  return (
    <span className="relative flex size-9 shrink-0">
      <span
        aria-hidden
        className="bg-accent-tint text-accent-ink flex size-9 items-center justify-center rounded-full text-sm font-bold"
      >
        {initials(n.data.name ?? '')}
      </span>
      {channel && (
        <span
          role="img"
          aria-label={t(channel)}
          title={t(channel)}
          className={cn(
            'ring-ground absolute -right-1 -bottom-1 flex size-4.5 items-center justify-center rounded-full ring-2',
            BADGE[channel],
          )}
        >
          <ChannelGlyph channel={channel} />
        </span>
      )}
    </span>
  );
}

function BellPanel({ phone }: { phone: boolean }) {
  const t = useTranslations('notifications');
  const format = useFormatter();
  const router = useRouter();
  const text = useText();
  const [feed, setFeed] = useState<Feed>({ items: [], unread: 0 });
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<Filter>('all');
  const seen = useRef(new Set<string>());
  const textRef = useRef(text);
  textRef.current = text;

  // The bell runs on every signed-in page, so this is where a granted browser's push subscription is
  // kept current (a rotated key, a permission granted before push existed).
  useEffect(() => {
    void syncPush();
  }, []);

  // Load, then only the stream: new items, counts and reads from other tabs. A removal loads again.
  useEffect(() => {
    const ctrl = new AbortController();
    const onFeed = (loaded: Feed) => {
      seen.current = new Set(loaded.items.map((n) => n.id));
      setFeed(loaded);
    };
    const onEvent = (ev: NotificationEvent) => {
      if (ev.type === 'notification' && !seen.current.has(ev.item.id)) {
        seen.current.add(ev.item.id);
        void showBrowser([ev.item], textRef.current, router.push);
      }
      setFeed((f) => applyEvent(f, ev));
    };
    void followFeed(onFeed, onEvent, ctrl.signal, router);
    return () => ctrl.abort();
  }, [router]);

  const markRead = async (ids?: string[]) => {
    setFeed((f) => {
      const items = f.items.map((n) => (!ids || ids.includes(n.id) ? { ...n, read: true } : n));
      return { items, unread: ids ? Math.max(0, f.unread - ids.length) : 0 };
    });
    await api('/v1/notifications/read', { method: 'POST', body: ids ? { ids } : {} });
  };

  const shown = filterItems(feed.items, filter);
  const counts = filterCounts(feed.items, feed.unread);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button
          variant="ghost"
          size="icon-md"
          className={cn(
            'hover:bg-hair data-[state=open]:bg-hair relative',
            phone ? 'size-11' : 'size-8 rounded-sm',
          )}
          aria-label={feed.unread ? t('openUnread', { count: feed.unread }) : t('open')}
        >
          <Bell className={phone ? 'size-5' : 'size-4.5'} />
          {feed.unread > 0 && (
            <span
              aria-hidden
              className={cn(
                'bg-accent text-on-accent text-3xs absolute flex h-4 min-w-4 items-center justify-center rounded-full border-2 px-1 font-bold tabular-nums',
                phone ? 'border-ground top-1.5 right-1' : 'border-side top-0.5 right-px',
              )}
            >
              {feed.unread > 9 ? '9+' : feed.unread}
            </span>
          )}
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side={phone ? 'bottom' : 'right'}
          align={phone ? 'end' : 'start'}
          sideOffset={6}
          collisionPadding={8}
          aria-label={t('title')}
          className="bg-ground text-ink z-50 flex max-h-popover-available w-100 max-w-[calc(100vw-16px)] flex-col rounded-xl border shadow-lg"
        >
          <header className="flex flex-col gap-3 border-b px-4 pt-3.5 pb-3">
            <div className="flex items-start gap-1">
              <div className="mr-auto flex flex-col gap-0.5 pt-1">
                <h2 className="text-base font-bold">{t('title')}</h2>
                <p className="text-muted text-xs" aria-live="polite">
                  {feed.unread > 0
                    ? t('summary.unread', { count: feed.unread })
                    : t('summary.allRead')}
                </p>
              </div>
              <Button variant="ghost" size="icon-sm" asChild>
                <Link
                  href="/settings/notifications"
                  aria-label={t('settingsLabel')}
                  onClick={() => setOpen(false)}
                >
                  <SlidersHorizontal className="size-4" />
                </Link>
              </Button>
              <Popover.Close asChild>
                <Button variant="ghost" size="icon-sm" aria-label={t('close')}>
                  <X className="size-4" />
                </Button>
              </Popover.Close>
            </div>
            <fieldset className="flex min-w-0 gap-1.5 overflow-x-auto">
              <legend className="sr-only">{t('filters.label')}</legend>
              {FILTERS.map((f) => (
                <Button
                  key={f}
                  variant="outline"
                  size="sm"
                  className="aria-pressed:border-transparent aria-pressed:bg-accent-tint aria-pressed:text-accent-ink h-7 shrink-0 rounded-full px-2.5 text-xs font-semibold"
                  aria-pressed={filter === f}
                  onClick={() => setFilter(f)}
                >
                  {t(`filters.${f}`)}
                  {counts[f] > 0 && (
                    <span className={cn('font-medium tabular-nums', filter !== f && 'text-muted')}>
                      {format.number(counts[f], 'credits')}
                    </span>
                  )}
                </Button>
              ))}
            </fieldset>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
            {shown.length === 0 ? (
              <div className="flex flex-col items-center px-4 py-10 text-center">
                <span
                  aria-hidden
                  className="bg-positive/15 text-positive mb-2 flex size-10 items-center justify-center rounded-full"
                >
                  <Check className="size-4.5" />
                </span>
                <p className="text-sm font-semibold">{t('empty.title')}</p>
                <p className="text-muted mt-1 text-xs">{t('empty.body')}</p>
              </div>
            ) : (
              <NotificationList
                items={shown}
                onPick={(n) => {
                  setOpen(false);
                  return n.read ? undefined : markRead([n.id]);
                }}
              />
            )}
          </div>
          <div className="flex min-h-11 items-center border-t px-3">
            <Button
              variant="ghost"
              size="sm"
              disabled={feed.unread === 0}
              onClick={() => markRead()}
            >
              {t('markAllRead')}
            </Button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function NotificationList({
  items,
  onPick,
}: {
  items: AppNotification[];
  onPick: (n: AppNotification) => Promise<unknown> | undefined;
}) {
  const t = useTranslations('notifications');
  const text = useText();
  const format = useFormatter();
  const dayLabel = (day: string) =>
    day === 'today' || day === 'yesterday'
      ? t(day)
      : format.dateTime(new Date(`${day}T12:00:00`), { day: 'numeric', month: 'short' });
  return groupByDay(items).map((g) => (
    <section key={g.day} aria-label={dayLabel(g.day)} className="mb-1.5">
      <h3 className="text-2xs tracking-label text-muted px-3 pt-2.5 pb-1 font-mono uppercase">
        {dayLabel(g.day)}
      </h3>
      <ul className="flex flex-col gap-0.5">
        {g.items.map((n) => {
          const { title, body } = text(n);
          const actions = ACTIONS[n.kind](n);
          return (
            <li
              key={n.id}
              className="hover:bg-raised has-focus-visible:ring-accent relative flex gap-3 rounded-lg p-3 has-focus-visible:ring-2 has-focus-visible:ring-inset"
            >
              <KindIcon n={n} />
              <div className="flex min-w-0 flex-1 flex-col">
                <p className={cn('text-sm', n.read ? 'font-medium' : 'font-bold')}>
                  <NotificationLink
                    href={safeHref(n.href)}
                    onClick={() => onPick(n)}
                    className="outline-none after:absolute after:inset-0 after:rounded-lg"
                  >
                    {title}
                    {!n.read && <span className="sr-only"> ({t('unread')})</span>}
                  </NotificationLink>
                </p>
                <p className="text-muted line-clamp-2 text-xs">{body}</p>
                {n.kind === 'search_done' && n.data.refunded ? (
                  <span className="bg-positive/15 text-positive mt-2 inline-flex h-5.5 items-center self-start rounded-full px-2 text-xs font-semibold">
                    {t('kind.search_done.refunded', { count: n.data.refunded })}
                  </span>
                ) : null}
                {n.quote && (
                  <blockquote className="bg-side mt-2 rounded-sm px-2.5 py-2 text-xs">
                    {`«${n.quote}»`}
                  </blockquote>
                )}
                {actions.length > 0 && (
                  <div className="relative z-10 mt-2.5 flex flex-wrap gap-1.5">
                    {actions.map((a) => (
                      <Button
                        key={a.label}
                        asChild
                        size="xs"
                        variant={a.primary ? 'primary' : 'outline'}
                      >
                        <NotificationLink
                          href={safeHref(a.href)}
                          onClick={() => onPick(n)}
                          aria-label={t('actions.for', {
                            action: t(`actions.${a.label}`),
                            title,
                          })}
                        >
                          {t(`actions.${a.label}`)}
                        </NotificationLink>
                      </Button>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex shrink-0 flex-col items-end gap-2 pt-0.5">
                <time dateTime={n.createdAt} className="text-2xs text-muted font-mono tabular-nums">
                  {format.dateTime(new Date(n.createdAt), { timeStyle: 'short' })}
                </time>
                {!n.read && <span aria-hidden className="bg-accent size-2 rounded-full" />}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  ));
}
