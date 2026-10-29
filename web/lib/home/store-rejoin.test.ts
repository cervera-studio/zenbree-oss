import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { NOT_A_MEMBER_FRAME, streamServer } from '@/lib/api/test-stream-server';
import { createHomeStore } from './store';
import { overviewOf } from './test-overview';

const tokens = vi.hoisted(() => ({ calls: 0 }));
vi.mock('@/lib/chat/auth', () => ({ getAuthToken: async () => `token-${++tokens.calls}` }));

const overview = (approvals: number) =>
  `event: snapshot\ndata: ${JSON.stringify({
    approvals: { total: approvals, campaigns: [] },
    replies: { count: 0, latest: null },
    running: [],
    finished: [],
    lists: [],
    campaigns: [],
    campaign_hint: null,
  })}\n\n`;

beforeEach(() => {
  tokens.calls = 0;
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('createHomeStore after a removal', () => {
  it('refreshes the app and reconnects once, with a fresh token, into the remaining organization', async () => {
    const fetch = streamServer(
      { frames: overview(4) + NOT_A_MEMBER_FRAME },
      { frames: overview(1), open: true },
    );
    const refresh = vi.fn();
    const store = createHomeStore(overviewOf(), { refresh, now: () => 0 });
    const leave = store.subscribe(() => {});
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(store.get().overview.approvals.total).toBe(4);
    await vi.advanceTimersByTimeAsync(1000); // the existing backoff
    await vi.waitFor(() => expect(store.get().overview.approvals.total).toBe(1));
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(tokens.calls).toBe(2);
    expect(refresh).toHaveBeenCalledOnce();
    leave();
  });

  it('stops when the reconnect answers 401 again: no organization is left', async () => {
    const fetch = streamServer({ frames: NOT_A_MEMBER_FRAME }, { status: 401 }, { status: 401 });
    const refresh = vi.fn();
    const store = createHomeStore(overviewOf(), { refresh, now: () => 0 });
    const leave = store.subscribe(() => {});
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    await vi.advanceTimersByTimeAsync(60_000);
    // The reconnect and authFetch's one retry with a fresh token; then nothing more.
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(tokens.calls).toBe(3);
    expect(refresh).toHaveBeenCalledOnce();
    leave();
  });
});
