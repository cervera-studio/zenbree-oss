// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { api } from '@/lib/api/browser';
import { signOut } from '@/lib/auth-client';
import { enablePush, keyBytes, syncPush, turnOffPush } from './push';

vi.mock('@/lib/api/browser', () => ({ api: vi.fn() }));
vi.mock('better-auth/react', () => ({
  createAuthClient: () => ({ signOut: vi.fn(async () => calls.push('auth signOut')) }),
}));

const calls: string[] = [];
const key = (byte: (i: number) => number) =>
  Buffer.from(Uint8Array.from({ length: 65 }, (_, i) => byte(i))).toString('base64url');
const OLD_KEY = key((i) => i);
const NEW_KEY = key((i) => 255 - i);
const FIRST = 'https://fcm.googleapis.com/fcm/send/1';
const SECOND = 'https://fcm.googleapis.com/fcm/send/2';

/** A browser with the Push API: one service worker registration and at most one subscription. */
function browser({
  permission = 'granted' as NotificationPermission,
  subscribedWith = null as string | null,
}) {
  let n = 0;
  const subscription = (applicationServerKey: Uint8Array) => {
    const endpoint = `https://fcm.googleapis.com/fcm/send/${++n}`;
    return {
      endpoint,
      options: { applicationServerKey: applicationServerKey.slice().buffer },
      unsubscribe: vi.fn(async () => {
        calls.push(`unsubscribe ${endpoint}`);
        state.current = null;
        return true;
      }),
      toJSON: () => ({ endpoint, keys: { p256dh: 'BKey', auth: 'aSecret' } }),
    };
  };
  const state = {
    permission,
    current: subscribedWith ? subscription(keyBytes(subscribedWith)) : null,
  };
  const registration = {
    pushManager: {
      getSubscription: async () => state.current,
      subscribe: async ({ applicationServerKey }: { applicationServerKey: Uint8Array }) => {
        state.current = subscription(applicationServerKey);
        return state.current;
      },
    },
  };
  vi.stubGlobal('PushManager', class {});
  vi.stubGlobal('Notification', {
    get permission() {
      return state.permission;
    },
    requestPermission: async () => {
      calls.push('requestPermission');
      return state.permission;
    },
  });
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: {
      register: async () => registration,
      ready: Promise.resolve(registration),
      getRegistration: async () => registration,
    },
  });
  return state;
}

function server(publicKey: string | null, subscribed: string[] = []) {
  vi.mocked(api).mockImplementation(async (path, opts = {}) => {
    const method = opts.method ?? 'GET';
    const endpoint = (opts.body as { endpoint?: string } | undefined)?.endpoint;
    calls.push([method, path, endpoint].filter(Boolean).join(' '));
    return {
      success: true,
      data: method === 'GET' ? { publicKey, subscribed } : {},
    } as never;
  });
}

beforeEach(() => {
  calls.length = 0;
  localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('keyBytes', () => {
  it('decodes the URL-safe base64 of a VAPID public key, with or without padding', () => {
    // Bytes chosen so the base64url form contains - and _.
    const bytes = Uint8Array.from({ length: 65 }, (_, i) => (i === 0 ? 4 : (i * 37 + 250) % 256));
    const base64Url = Buffer.from(bytes).toString('base64url');
    expect(base64Url).toMatch(/[-_]/);
    expect(keyBytes(base64Url)).toEqual(bytes);
    expect(keyBytes(`${base64Url}=`)).toEqual(bytes);
  });
});

describe('sign-out', () => {
  it('forgets this browser on the server while the session is valid, then in the browser', async () => {
    browser({ subscribedWith: NEW_KEY });
    server(NEW_KEY);
    await signOut();
    expect(calls).toEqual([
      `DELETE /v1/notifications/push ${FIRST}`,
      `unsubscribe ${FIRST}`,
      'auth signOut',
    ]);
  });

  it('still signs out when the API is down, or when this browser has no subscription', async () => {
    const state = browser({ subscribedWith: NEW_KEY });
    vi.mocked(api).mockRejectedValue(new Error('offline'));
    await signOut();
    expect(calls).toEqual([`unsubscribe ${FIRST}`, 'auth signOut']);
    expect(state.current).toBeNull();
    calls.length = 0;
    await signOut();
    expect(calls).toEqual(['auth signOut']);
  });

  it('does not wait long on an API that never answers, and still unsubscribes the browser', async () => {
    vi.useFakeTimers();
    try {
      browser({ subscribedWith: NEW_KEY });
      vi.mocked(api).mockReturnValue(new Promise(() => {}));
      const done = signOut();
      await vi.advanceTimersByTimeAsync(3000);
      await done;
      expect(calls).toEqual([`unsubscribe ${FIRST}`, 'auth signOut']);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('enablePush', () => {
  it('replaces a subscription made with an old key: the push service refuses every send to it', async () => {
    const state = browser({ subscribedWith: OLD_KEY });
    server(NEW_KEY);
    expect(await enablePush(NEW_KEY)).toBe(SECOND);
    expect(calls).toEqual([
      'requestPermission',
      `DELETE /v1/notifications/push ${FIRST}`,
      `unsubscribe ${FIRST}`,
      `PUT /v1/notifications/push ${SECOND}`,
    ]);
    expect(new Uint8Array(state.current?.options.applicationServerKey ?? [])).toEqual(
      keyBytes(NEW_KEY),
    );
  });

  it('keeps a subscription made with the current key', async () => {
    browser({ subscribedWith: NEW_KEY });
    server(NEW_KEY);
    expect(await enablePush(NEW_KEY)).toBe(FIRST);
    expect(calls).toEqual(['requestPermission', `PUT /v1/notifications/push ${FIRST}`]);
  });
});

describe('syncPush, on every signed-in load', () => {
  it('subscribes a browser granted before push existed, with no prompt', async () => {
    browser({});
    server(NEW_KEY);
    await syncPush();
    expect(calls).toEqual(['GET /v1/notifications/push', `PUT /v1/notifications/push ${FIRST}`]);
  });

  it('replaces a subscription after the key rotated', async () => {
    browser({ subscribedWith: OLD_KEY });
    server(NEW_KEY, [FIRST]);
    await syncPush();
    expect(calls).toEqual([
      'GET /v1/notifications/push',
      `DELETE /v1/notifications/push ${FIRST}`,
      `unsubscribe ${FIRST}`,
      `PUT /v1/notifications/push ${SECOND}`,
    ]);
  });

  it('saves a subscription the server does not list for this user', async () => {
    browser({ subscribedWith: NEW_KEY });
    server(NEW_KEY, []);
    await syncPush();
    expect(calls).toEqual(['GET /v1/notifications/push', `PUT /v1/notifications/push ${FIRST}`]);
  });

  it('only reads when this browser is already the user’s with the current key', async () => {
    browser({ subscribedWith: NEW_KEY });
    server(NEW_KEY, [FIRST]);
    await syncPush();
    expect(calls).toEqual(['GET /v1/notifications/push']);
  });

  it.each([
    ['permission is not granted', 'default', NEW_KEY],
    ['push is inert', 'granted', null],
  ] as const)('changes nothing when %s', async (_, permission, publicKey) => {
    browser({ permission });
    server(publicKey);
    await syncPush();
    expect(calls.filter((c) => !c.startsWith('GET'))).toEqual([]);
  });

  it('stays off on a browser where the user turned it off, until they turn it on', async () => {
    browser({ subscribedWith: NEW_KEY });
    server(NEW_KEY, [FIRST]);
    await turnOffPush();
    expect(calls).toEqual([`DELETE /v1/notifications/push ${FIRST}`, `unsubscribe ${FIRST}`]);
    calls.length = 0;
    await syncPush();
    expect(calls).toEqual([]);
    await enablePush(NEW_KEY);
    server(NEW_KEY, [SECOND]);
    calls.length = 0;
    await syncPush();
    expect(calls).toEqual(['GET /v1/notifications/push']);
  });
});
