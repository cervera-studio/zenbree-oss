/** The bell's live feed (#242): `GET /v1/notifications/stream`, frames in docs/contracts-m1.md. */
import { api } from '@/lib/api/browser';
import { type Frame, followStream } from '@/lib/api/event-stream';
import { toCamelCase } from '@/lib/http-client/case-transform';
import type { AppNotification } from './group';

export type Feed = { items: AppNotification[]; unread: number };

const NOTIFICATION_EVENT_TYPES = ['notification', 'read'] as const;
type NotificationEventType = (typeof NOTIFICATION_EVENT_TYPES)[number];

export type NotificationEvent =
  | { type: 'notification'; item: AppNotification; unread: number }
  | { type: 'read'; ids: string[] | null; unread: number };

const PANEL_LIMIT = 50; // api/app/notifications/routes.py

/** One frame applied. The count is the server's; a replayed item the panel already has adds nothing. */
export function applyEvent(feed: Feed, ev: NotificationEvent): Feed {
  if (ev.type === 'read') {
    const items = feed.items.map((n) =>
      !ev.ids || ev.ids.includes(n.id) ? { ...n, read: true } : n,
    );
    return { items, unread: ev.unread };
  }
  if (feed.items.some((n) => n.id === ev.item.id)) return { ...feed, unread: ev.unread };
  return { items: [ev.item, ...feed.items].slice(0, PANEL_LIMIT), unread: ev.unread };
}

function isEventType(value: string): value is NotificationEventType {
  return NOTIFICATION_EVENT_TYPES.some((t) => t === value);
}

function toEvent({ event, data }: Frame): NotificationEvent | null {
  if (!isEventType(event)) return null;
  try {
    return { type: event, ...toCamelCase<object>(JSON.parse(data)) } as NotificationEvent;
  } catch {
    return null;
  }
}

const EMPTY: Feed = { items: [], unread: 0 };

/** `GET /v1/notifications` into `onFeed`, and the cursor its stream resumes from. A failed load
 *  empties the panel rather than keep a list the user may no longer see; the stream then starts
 *  from now. `'end'`: the API refused the user (a 401 after `authFetch`'s retry). */
async function loadFeed(
  onFeed: (feed: Feed) => void,
  signal: AbortSignal,
): Promise<{ cursor: string | null } | 'end'> {
  const res = await api<Feed & { cursor: string | null }>('/v1/notifications');
  if (signal.aborted) return 'end';
  if (!res.success) {
    onFeed(EMPTY);
    return res.error.status === 401 ? 'end' : { cursor: null };
  }
  onFeed({ items: res.data.items, unread: res.data.unread });
  return { cursor: res.data.cursor };
}

/** Load the panel, then tail the stream from its cursor until `signal` aborts: reconnects with
 *  backoff and `Last-Event-ID`. The feed is the user's across organizations, but its items are
 *  those of the organizations they belong to. After a removal (`rejoin` in `followStream`),
 *  `router.refresh()` renders the organization their session moved to, and the panel loads again
 *  before the stream reconnects from the new cursor, so the removed organization's items and
 *  count leave the bell. */
export async function followFeed(
  onFeed: (feed: Feed) => void,
  onEvent: (ev: NotificationEvent) => void,
  signal: AbortSignal,
  router: { refresh: () => void },
): Promise<void> {
  const first = await loadFeed(onFeed, signal);
  if (signal.aborted) return;
  return followStream(
    '/v1/notifications/stream',
    // A first load the API refused still follows, as before the rejoin existed.
    first === 'end' ? null : first.cursor,
    (frame) => {
      const ev = toEvent(frame);
      if (ev) onEvent(ev);
    },
    signal,
    { rejoin: () => router.refresh(), reload: () => loadFeed(onFeed, signal) },
  );
}
