// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { NOT_A_MEMBER_FRAME, routeServer } from '@/lib/api/test-stream-server';
import { MESSAGES } from '@/messages';
import { NotificationBell } from './notification-bell';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const router = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));
vi.mock('@/lib/chat/auth', () => ({ getAuthToken: async () => 'token' }));
vi.mock('./use-phone', () => ({ usePhone: () => false }));
vi.mock('@/lib/notifications/push', () => ({
  syncPush: async () => {},
  pushActive: async () => false,
  pushTurnedOff: () => true,
}));

const item = (id: string) => ({
  id,
  kind: 'low_credits',
  href: '/billing',
  data: { credits: 100 },
  read: false,
  created_at: new Date().toISOString(),
  browser: false,
});

beforeEach(() => {
  router.refresh.mockClear();
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('the bell after a removal', () => {
  it("drops the removed organization's item and count with no page reload", async () => {
    const served = routeServer({
      '/v1/notifications': [
        { json: { items: [item('a1'), item('own1')], unread: 2, cursor: '6-0' } },
        { json: { items: [item('own1')], unread: 1, cursor: '9-0' } },
      ],
      '/v1/notifications/stream': [
        { frames: NOT_A_MEMBER_FRAME },
        { frames: ': ping\n\n', open: true },
      ],
    });
    // The removal comes when the test says, so the label is read before and after it.
    let remove = () => {};
    const removed = new Promise<void>((resolve) => {
      remove = resolve;
    });
    let streams = 0;
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (url.includes('/stream') && streams++ === 0) await removed;
      return served(url, init);
    });
    const container = document.createElement('div');
    const root = createRoot(container);
    await act(async () =>
      root.render(
        <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS} timeZone="UTC">
          <NotificationBell />
        </NextIntlClientProvider>,
      ),
    );
    const label = () => container.querySelector('button')?.getAttribute('aria-label');
    await vi.waitFor(() => expect(label()).toBe('Notifiche, 2 da leggere'));

    await act(async () => remove());
    await act(() => vi.advanceTimersByTimeAsync(1000)); // the existing backoff
    await vi.waitFor(() => expect(label()).toBe('Notifiche, 1 da leggere'));
    expect(router.refresh).toHaveBeenCalledOnce();
    act(() => root.unmount());
  });
});
