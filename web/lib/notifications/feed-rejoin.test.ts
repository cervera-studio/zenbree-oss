import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { NOT_A_MEMBER_FRAME, routeServer } from '@/lib/api/test-stream-server';
import { type Feed, followFeed, type NotificationEvent } from './feed';

const tokens = vi.hoisted(() => ({ calls: 0 }));
vi.mock('@/lib/chat/auth', () => ({
  getAuthToken: async () => `token-${++tokens.calls}`,
}));

const LOAD = '/v1/notifications';
const STREAM = '/v1/notifications/stream';

const item = (id: string) => ({
  id,
  kind: 'search_done',
  href: '/',
  data: {},
  read: false,
  created_at: '2026-10-14T03:00:00Z',
  browser: false,
});
// Cy's panel in A, and in his own organization after Ada removes him from A.
const inA = { json: { items: [item('a1'), item('own1')], unread: 2, cursor: '6-0' } };
const own = { json: { items: [item('own1')], unread: 1, cursor: '9-0' } };

const notification =
  'event: notification\nid: 10-0\ndata: {"item":{"id":"n2","created_at":"x"},"unread":2}\n\n';

function lastEventIds(fetch: ReturnType<typeof routeServer>) {
  return fetch.mock.calls
    .filter(([url]) => url.includes(STREAM))
    .map(([, init]) => new Headers(init?.headers).get('Last-Event-ID'));
}

beforeEach(() => {
  tokens.calls = 0;
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('followFeed after a removal', () => {
  it('loads the panel again in the remaining organization, then reconnects from its cursor', async () => {
    const fetch = routeServer({
      [LOAD]: [inA, own],
      [STREAM]: [{ frames: NOT_A_MEMBER_FRAME }, { frames: notification, open: true }],
    });
    const refresh = vi.fn();
    const feeds: Feed[] = [];
    const seen: NotificationEvent[] = [];
    const ctrl = new AbortController();
    const following = followFeed(
      (feed) => feeds.push(feed),
      (ev) => seen.push(ev),
      ctrl.signal,
      { refresh },
    );
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    await vi.advanceTimersByTimeAsync(1000); // the existing backoff
    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(feeds.map((f) => [f.items.map((n) => n.id), f.unread])).toEqual([
      [['a1', 'own1'], 2],
      [['own1'], 1],
    ]);
    expect(seen[0]).toMatchObject({ type: 'notification', item: { id: 'n2' }, unread: 2 });
    // Nothing falls between the reload and the stream: the reconnect resumes from its cursor.
    expect(lastEventIds(fetch)).toEqual(['6-0', '9-0']);
    expect(fetch.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
      LOAD,
      STREAM,
      LOAD,
      STREAM,
    ]);
    expect(refresh).toHaveBeenCalledOnce();
    ctrl.abort();
    await following;
  });

  it('empties the panel when the reload fails, and follows from now', async () => {
    const fetch = routeServer({
      [LOAD]: [inA, { status: 503 }],
      [STREAM]: [{ frames: NOT_A_MEMBER_FRAME }, { frames: notification, open: true }],
    });
    const feeds: Feed[] = [];
    const ctrl = new AbortController();
    const following = followFeed(
      (feed) => feeds.push(feed),
      () => {},
      ctrl.signal,
      {
        refresh: () => {},
      },
    );
    await vi.waitFor(() => expect(feeds).toHaveLength(2));
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(lastEventIds(fetch)).toEqual(['6-0', null]));
    expect(feeds[1]).toEqual({ items: [], unread: 0 });
    ctrl.abort();
    await following;
  });

  it('stops when the reload answers 401: no organization is left', async () => {
    const fetch = routeServer({
      [LOAD]: [inA, { status: 401 }, { status: 401 }],
      [STREAM]: [{ frames: NOT_A_MEMBER_FRAME }],
    });
    const feeds: Feed[] = [];
    const refresh = vi.fn();
    const following = followFeed(
      (feed) => feeds.push(feed),
      () => {},
      new AbortController().signal,
      { refresh },
    );
    await vi.advanceTimersByTimeAsync(1000);
    await following;
    // authFetch retried the reload's 401 once with a fresh token; then the stream ended, with no
    // reconnect and no backoff left.
    expect(lastEventIds(fetch)).toEqual(['6-0']);
    expect(fetch).toHaveBeenCalledTimes(4);
    expect(tokens.calls).toBe(4);
    expect(feeds.at(-1)).toEqual({ items: [], unread: 0 });
    expect(refresh).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('stops when the reconnect answers 401 again: no organization is left', async () => {
    const fetch = routeServer({
      [LOAD]: [inA, own],
      [STREAM]: [{ frames: NOT_A_MEMBER_FRAME }, { status: 401 }, { status: 401 }],
    });
    const refresh = vi.fn();
    const following = followFeed(
      () => {},
      () => {},
      new AbortController().signal,
      {
        refresh,
      },
    );
    await vi.advanceTimersByTimeAsync(1000);
    await following;
    // authFetch retried the stream's 401 once with a fresh token; then the stream ended, with no
    // backoff left.
    expect(lastEventIds(fetch)).toEqual(['6-0', '9-0', '9-0']);
    expect(fetch).toHaveBeenCalledTimes(5);
    expect(refresh).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});
