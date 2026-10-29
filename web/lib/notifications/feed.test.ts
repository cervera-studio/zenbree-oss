import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { authFetch } from '@/lib/api/browser';
import { backoff, parseFrames } from '@/lib/api/event-stream';
import { applyEvent, type Feed, followFeed, type NotificationEvent } from './feed';
import type { AppNotification } from './group';

vi.mock('@/lib/api/browser', () => ({
  authFetch: vi.fn(),
  // The panel load before the stream, which resumes from its cursor.
  api: async () => ({ success: true, data: { items: [], unread: 0, cursor: '4-0' } }),
}));

const item = (id: string, read = false): AppNotification => ({
  id,
  kind: 'low_credits',
  href: '/billing',
  data: { credits: 120 },
  read,
  createdAt: '2026-10-05T10:00:00Z',
  browser: true,
});

describe('applyEvent', () => {
  const feed: Feed = { items: [item('a'), item('b')], unread: 2 };

  it('prepends a new item and takes the server count', () => {
    const next = applyEvent(feed, { type: 'notification', item: item('c'), unread: 3 });
    expect(next.items.map((n) => n.id)).toEqual(['c', 'a', 'b']);
    expect(next.unread).toBe(3);
  });

  it('keeps a replayed item once', () => {
    const next = applyEvent(feed, { type: 'notification', item: item('a'), unread: 2 });
    expect(next.items).toBe(feed.items);
  });

  it('marks the listed ids read, or all of them', () => {
    const one = applyEvent(feed, { type: 'read', ids: ['a'], unread: 1 });
    expect(one.items.map((n) => n.read)).toEqual([true, false]);
    expect(one.unread).toBe(1);
    const all = applyEvent(feed, { type: 'read', ids: null, unread: 0 });
    expect(all.items.every((n) => n.read)).toBe(true);
  });
});

describe('parseFrames', () => {
  it('splits complete frames, skips pings and keeps the partial tail', () => {
    const { frames, rest } = parseFrames(
      ': ping\n\nevent: read\nid: 1-0\ndata: {"ids":null,"unread":0}\n\nevent: notif',
    );
    expect(frames).toEqual([{ event: 'read', id: '1-0', data: '{"ids":null,"unread":0}' }]);
    expect(rest).toBe('event: notif');
  });
});

describe('followFeed', () => {
  afterEach(() => vi.mocked(authFetch).mockReset());

  it('backs off up to 30 s', () => {
    expect([0, 1, 2, 10].map(backoff)).toEqual([1000, 2000, 4000, 30000]);
  });

  it('reconnects from the last frame it handled', async () => {
    const ctrl = new AbortController();
    const seen: NotificationEvent[] = [];
    const body =
      'event: notification\nid: 5-0\ndata: {"item":{"id":"c","created_at":"x"},"unread":3}\n\n' +
      'event: shutdown\nid: 6-0\ndata: {}\n\n';
    vi.mocked(authFetch)
      .mockResolvedValueOnce(new Response(body, { status: 200 }))
      .mockImplementationOnce(async () => {
        ctrl.abort();
        return new Response(null, { status: 503 });
      });
    await followFeed(
      () => {},
      (ev) => seen.push(ev),
      ctrl.signal,
      { refresh: () => {} },
    );
    const headers = vi.mocked(authFetch).mock.calls.map(([, init]) => init?.headers);
    expect(headers).toEqual([{ 'Last-Event-ID': '4-0' }, { 'Last-Event-ID': '6-0' }]);
    expect(seen).toEqual([{ type: 'notification', item: { id: 'c', createdAt: 'x' }, unread: 3 }]);
    const paths = vi.mocked(authFetch).mock.calls.map(([path]) => path);
    expect(paths).toEqual(Array(2).fill('/v1/notifications/stream'));
  });
});
