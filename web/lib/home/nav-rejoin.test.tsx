// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { NOT_A_MEMBER_FRAME, streamServer } from '@/lib/api/test-stream-server';
import { type Nav, useNavState } from './nav';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const tokens = vi.hoisted(() => ({ calls: 0 }));
const router = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock('@/lib/chat/auth', () => ({ getAuthToken: async () => `token-${++tokens.calls}` }));
vi.mock('@/lib/projects', () => ({ useCurrentProjectId: () => 'p1' }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

const nav = (lists: number) =>
  `event: snapshot\ndata: ${JSON.stringify({ lists, campaigns: 0, approvals: 0, views: 0, replies: 0, sessions: [] })}\n\n`;

let seen: { nav: Nav | null; failed: boolean }[] = [];

function Probe() {
  seen.push(useNavState());
  return null;
}

async function mount() {
  const root = createRoot(document.createElement('div'));
  await act(async () => root.render(<Probe />));
  return () => act(() => root.unmount());
}

beforeEach(() => {
  tokens.calls = 0;
  seen = [];
  router.refresh.mockClear();
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('useNavState after a removal', () => {
  it('refreshes the app and reconnects once, with a fresh token, into the remaining organization', async () => {
    const fetch = streamServer(
      { frames: nav(3) + NOT_A_MEMBER_FRAME },
      { frames: nav(1), open: true },
    );
    const unmount = await mount();
    await vi.waitFor(() => expect(router.refresh).toHaveBeenCalledOnce());
    expect(seen.at(-1)).toMatchObject({ nav: { lists: 3 }, failed: false });
    await act(() => vi.advanceTimersByTimeAsync(1000)); // the existing backoff
    await vi.waitFor(() => expect(seen.at(-1)).toMatchObject({ nav: { lists: 1 }, failed: false }));
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(tokens.calls).toBe(2);
    expect(router.refresh).toHaveBeenCalledOnce();
    unmount();
  });

  it('stops when the reconnect answers 401 again, and stops the skeleton too', async () => {
    const fetch = streamServer({ frames: NOT_A_MEMBER_FRAME }, { status: 401 }, { status: 401 });
    const unmount = await mount();
    await vi.waitFor(() => expect(router.refresh).toHaveBeenCalledOnce());
    await act(() => vi.advanceTimersByTimeAsync(1000));
    await vi.waitFor(() => expect(seen.at(-1)).toEqual({ nav: null, failed: true }));
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(fetch).toHaveBeenCalledTimes(3); // the reconnect and authFetch's one retry with a fresh token
    expect(tokens.calls).toBe(3);
    expect(router.refresh).toHaveBeenCalledOnce();
    unmount();
  });
});
