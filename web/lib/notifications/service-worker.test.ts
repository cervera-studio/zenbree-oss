import vm from 'node:vm';
import { describe, expect, it } from 'vite-plus/test';
import { GET } from '@/app/sw.js/route';
import { SERVICE_WORKER_SOURCE } from './service-worker';

type Handler = (event: unknown) => void;

function boot(windows: { url: string; focus: () => Promise<unknown>; navigate: unknown }[] = []) {
  const handlers: Record<string, Handler> = {};
  const shown: [string, NotificationOptions][] = [];
  const opened: string[] = [];
  const self = {
    location: { origin: 'https://app.zenbree.test' },
    addEventListener: (type: string, h: Handler) => {
      handlers[type] = h;
    },
    skipWaiting: () => {},
    registration: {
      showNotification: (title: string, options: NotificationOptions) => {
        shown.push([title, options]);
        return Promise.resolve();
      },
    },
    clients: {
      claim: () => Promise.resolve(),
      matchAll: () => Promise.resolve(windows),
      openWindow: (path: string) => {
        opened.push(path);
        return Promise.resolve(null);
      },
    },
  };
  vm.runInNewContext(SERVICE_WORKER_SOURCE, { self, URL });
  return { handlers, shown, opened };
}

async function pushed(handlers: Record<string, Handler>, data: unknown) {
  let done: Promise<unknown> = Promise.resolve();
  handlers.push?.({
    data: { json: () => data },
    waitUntil: (p: Promise<unknown>) => {
      done = p;
    },
  });
  await done;
}

async function clicked(handlers: Record<string, Handler>, href: unknown) {
  let done: Promise<unknown> = Promise.resolve();
  handlers.notificationclick?.({
    notification: { close: () => {}, data: { href } },
    waitUntil: (p: Promise<unknown>) => {
      done = p;
    },
  });
  await done;
}

describe('service worker', () => {
  it('shows the push with its id as tag and its href as data', async () => {
    const { handlers, shown } = boot();
    await pushed(handlers, {
      id: 'n1',
      title: 'Risposta interessata',
      body: 'Ada ha risposto',
      href: '/inbox?m=1',
    });
    expect(shown).toHaveLength(1);
    expect(shown[0]?.[0]).toBe('Risposta interessata');
    expect(shown[0]?.[1]).toMatchObject({
      body: 'Ada ha risposto',
      tag: 'n1',
      data: { href: '/inbox?m=1' },
    });
  });

  it('still shows something when the payload is unreadable', async () => {
    const { handlers, shown } = boot();
    handlers.push?.({
      data: {
        json: () => {
          throw new Error('not json');
        },
      },
      waitUntil: (p: Promise<unknown>) => p.then(() => {}),
    });
    await Promise.resolve();
    expect(shown[0]?.[0]).toBe('Zenbree');
  });

  it('focuses an open Zenbree window and moves it to the item', async () => {
    const navigated: string[] = [];
    const focused: string[] = [];
    const win = {
      url: 'https://app.zenbree.test/campaigns',
      focus: () => {
        focused.push('x');
        return Promise.resolve(win);
      },
      navigate: (p: string) => {
        navigated.push(p);
        return Promise.resolve(win);
      },
    };
    const { handlers, opened } = boot([win]);
    await clicked(handlers, '/inbox?m=1#reply');
    expect(focused).toHaveLength(1);
    expect(navigated).toEqual(['/inbox?m=1#reply']);
    expect(opened).toEqual([]);
  });

  it('opens a window when no Zenbree one is open', async () => {
    const other = {
      url: 'https://other.test/',
      focus: () => Promise.resolve(),
      navigate: () => {},
    };
    const { handlers, opened } = boot([other]);
    await clicked(handlers, '/inbox?m=1');
    expect(opened).toEqual(['/inbox?m=1']);
  });

  it.each([
    'https://evil.test/x',
    '//evil.test/x',
    '/\\evil.test',
    '/\t/evil.test/x',
    '/\n/evil.test',
    '/\r\\evil.test',
    'javascript:alert(1)',
    undefined,
    42,
  ])('never leaves the app: %s opens the home page', async (href) => {
    const { handlers, opened } = boot();
    await clicked(handlers, href);
    expect(opened).toEqual(['/']);
  });
});

describe('GET /sw.js', () => {
  it('serves the worker as JavaScript for the whole origin, never cached', async () => {
    const res = GET();
    expect(res.headers.get('Content-Type')).toContain('javascript');
    expect(res.headers.get('Service-Worker-Allowed')).toBe('/');
    expect(res.headers.get('Cache-Control')).toBe('no-cache');
    expect(await res.text()).toBe(SERVICE_WORKER_SOURCE);
  });
});
