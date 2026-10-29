// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

vi.mock('@/app/(app)/actions', () => ({ dismissWelcomeAction: vi.fn() }));
let tokenDown = false;
vi.mock('@/lib/auth-client', () => ({
  authClient: {
    token: () =>
      tokenDown
        ? Promise.reject(new Error('offline'))
        : Promise.resolve({ data: { token: 'jwt' } }),
  },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}));
let params = new URLSearchParams();
const refresh = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh }),
  usePathname: () => '/',
  useSearchParams: () => params,
}));
vi.mock('@/components/lists/csv-import-dialog', () => ({ CsvImportDialog: () => null }));
vi.mock('@/components/projects/project-breadcrumb', () => ({ ProjectBreadcrumb: () => null }));

import { TestIntl } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import { turnLive } from '@/lib/chat/chat-turn';
import { ChatStreamProvider, useChatStreamStore } from '@/lib/chat/stream-provider';
import type { ChatStreamStore } from '@/lib/chat/stream-store';
import { DRAFT_KEY } from '@/lib/chat/stream-store-types';
import { ChatApp } from './chat-app';
import type { WelcomeView } from './home-welcome';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const overview = Promise.resolve(null);
let users = 0;

function session(id: string) {
  return Response.json({
    session: {
      id,
      project_id: null,
      title: 'Agenzie a Milano',
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
      pinned_at: null,
    },
    messages: [
      { id: `${id}-u`, role: 'user', content: 'Ciao', created_at: '2026-10-01T00:00:00Z' },
      {
        id: `${id}-a`,
        role: 'assistant',
        content: 'Ciao, chi cerchi?',
        status: 'success',
        created_at: '2026-10-01T00:00:01Z',
      },
    ],
    lists: [],
    active_job: null,
    fill_credits: 0,
  });
}

/** The API: chats load, and `POST /v1/chat` waits for the test to answer it. */
function api() {
  let answer: (reply: () => Response) => void = () => {};
  const replied = new Promise<() => Response>((resolve) => {
    answer = resolve;
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const chat = /\/v1\/chat\/sessions\/([^/?]+)$/.exec(url);
      if (chat?.[1]) return session(chat[1]);
      if (url.endsWith('/v1/chat')) return replied.then((reply) => reply());
      return Response.json({});
    }),
  );
  return {
    /** The second tab's send while the first tab's job runs. */
    refuse: () =>
      answer(() =>
        Response.json(
          { detail: { code: 'CHAT.JOB_RUNNING', message: 'Another request is still running' } },
          { status: 409 },
        ),
      ),
    /** The card's segment was proposed again after the page loaded. */
    gone: () =>
      answer(() =>
        Response.json(
          { detail: { code: 'CHAT.SEGMENT_GONE', message: 'This segment is no longer available' } },
          { status: 422 },
        ),
      ),
    /** The queue is down: the server kept nothing of the turn. */
    down: () =>
      answer(() =>
        Response.json(
          { detail: { code: 'CHAT.QUEUE_UNAVAILABLE', message: 'Try again in a moment' } },
          { status: 503 },
        ),
      ),
    unreachable: () =>
      answer(() => {
        throw new TypeError('Failed to fetch');
      }),
    /** The stream ends before its `session` frame. */
    cut: () =>
      answer(
        () => new Response(': ping\n\n', { headers: { 'Content-Type': 'text/event-stream' } }),
      ),
    take: () =>
      answer(
        () =>
          new Response('event: done\ndata: {}\n\n', {
            headers: { 'Content-Type': 'text/event-stream' },
          }),
      ),
  };
}

function mount({
  welcome = null,
  credits = null,
}: {
  welcome?: WelcomeView | null;
  credits?: number | null;
} = {}) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  const root = createRoot(container);
  const userId = `u${++users}`;
  let store: ChatStreamStore | null = null;
  function Probe() {
    store = useChatStreamStore();
    return null;
  }
  const render = () =>
    act(async () =>
      root.render(
        <TestIntl>
          <ChatStreamProvider userId={userId}>
            <Probe />
            <ChatApp
              initialCredits={credits}
              topUpPack={null}
              overviewRequest={overview}
              welcome={welcome}
              viewer={{ firstName: null, id: null }}
            />
          </ChatStreamProvider>
        </TestIntl>,
      ),
    );
  return {
    render,
    box: () => container.querySelector('textarea') as HTMLTextAreaElement,
    text: () => container.textContent ?? '',
    button: (text: string) =>
      [...container.querySelectorAll('button')].find((b) => b.textContent?.includes(text)),
    store: () => store as unknown as ChatStreamStore,
  };
}

async function type(box: HTMLTextAreaElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(box, text);
    box.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submit(box: HTMLTextAreaElement) {
  await act(async () => box.form?.requestSubmit());
}

async function ended(store: ChatStreamStore, key: string) {
  await act(async () => {
    await vi.waitFor(() => expect(turnLive(store.getChat(key).turn)).toBe(false));
  });
}

beforeEach(() => {
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
  vi.stubGlobal('cancelAnimationFrame', (h: ReturnType<typeof setTimeout>) => clearTimeout(h));
  vi.mocked(toast.error).mockClear();
  refresh.mockClear();
  tokenDown = false;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a send refused with CHAT.JOB_RUNNING (#1171)', () => {
  it('puts the text back in the home composer', async () => {
    params = new URLSearchParams();
    const server = api();
    const app = mount();
    await app.render();
    await vi.waitFor(() => expect(app.box()).not.toBeNull());
    await type(app.box(), 'Trova 50 agenzie a Milano');
    await submit(app.box());
    expect(app.box().value).toBe('');

    server.refuse();
    await ended(app.store(), DRAFT_KEY);
    expect(toast.error).toHaveBeenCalledOnce();
    expect(app.box().value).toBe('Trova 50 agenzie a Milano');
    expect(document.activeElement).toBe(app.box());
  });

  it('keeps the home mode, so a retry asks for the same goal', async () => {
    params = new URLSearchParams();
    const server = api();
    const app = mount();
    await app.render();
    await vi.waitFor(() => expect(app.button('La lista diventa')).toBeDefined());
    await act(async () => app.button('La lista diventa')?.click());
    await type(app.box(), 'Trova 50 agenzie a Milano');
    await submit(app.box());
    server.refuse();
    await ended(app.store(), DRAFT_KEY);

    await submit(app.box());
    await ended(app.store(), DRAFT_KEY);
    const goals = vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).endsWith('/v1/chat'))
      .map(([, init]) => JSON.parse(String(init?.body)).goal);
    expect(goals).toEqual(['campaign', 'campaign']);
  });

  it('keeps the newer text typed on home while the send was in flight', async () => {
    params = new URLSearchParams();
    const server = api();
    const app = mount();
    await app.render();
    await vi.waitFor(() => expect(app.box()).not.toBeNull());
    await type(app.box(), 'Trova 50 agenzie a Milano');
    await submit(app.box());
    await type(app.box(), 'Solo a Torino');

    server.refuse();
    await ended(app.store(), DRAFT_KEY);
    expect(app.box().value).toBe('Solo a Torino');
  });

  it('puts the text back in the composer of a chat with history', async () => {
    params = new URLSearchParams({ s: 's1' });
    const server = api();
    const app = mount();
    await app.render();
    await vi.waitFor(() => expect(app.text()).toContain('Ciao, chi cerchi?'));
    await type(app.box(), 'Trova 50 agenzie a Milano');
    await submit(app.box());
    expect(app.box().value).toBe('');

    server.refuse();
    await ended(app.store(), 's1');
    expect(app.store().getChat('s1').messages).toHaveLength(2);
    expect(app.box().value).toBe('Trova 50 agenzie a Milano');
  });

  it('leaves the composer of the chat opened meanwhile empty', async () => {
    params = new URLSearchParams({ s: 's1' });
    const server = api();
    const app = mount();
    await app.render();
    await vi.waitFor(() => expect(app.text()).toContain('Ciao, chi cerchi?'));
    await type(app.box(), 'Trova 50 agenzie a Milano');
    await submit(app.box());

    params = new URLSearchParams({ s: 's2' });
    await app.render();
    await vi.waitFor(() => expect(app.store().getChat('s2').messages).toHaveLength(2));
    server.refuse();
    await ended(app.store(), 's1');
    expect(app.box().value).toBe('');
  });

  it('leaves the box empty after a send the server takes', async () => {
    params = new URLSearchParams({ s: 's1' });
    const server = api();
    const app = mount();
    await app.render();
    await vi.waitFor(() => expect(app.text()).toContain('Ciao, chi cerchi?'));
    await type(app.box(), 'Trova 50 agenzie a Milano');
    await submit(app.box());

    server.take();
    await ended(app.store(), 's1');
    expect(app.store().getChat('s1').messages[2]?.content).toBe('Trova 50 agenzie a Milano');
    expect(app.box().value).toBe('');
  });
});

describe('a segment card send that fails before its session', () => {
  it.each(['refuse', 'gone', 'down', 'unreachable', 'cut', 'noToken'] as const)(
    'leaves no draft (%s)',
    async (answer) => {
      params = new URLSearchParams();
      const server = api();
      const welcome: WelcomeView = {
        firstName: null,
        companyName: 'Acme',
        knowsFrom: 'website',
        segments: [
          {
            key: '0123456789abcdef',
            name: 'Agenzie',
            titles: ['CEO'],
            employeeRange: null,
            message: 'Crea la mia prima lista di contatti per il segmento "Agenzie".',
            locale: 'it',
          },
        ],
        steps: [
          { kind: 'profile', done: true },
          { kind: 'list', done: false },
          { kind: 'mailbox', done: false },
        ],
      };
      const app = mount({ welcome });
      await app.render();
      await vi.waitFor(() => expect(app.button('Agenzie')).toBeDefined());
      tokenDown = answer === 'noToken';
      await act(async () => app.button('Agenzie')?.click());

      if (answer !== 'noToken') server[answer]();
      await ended(app.store(), DRAFT_KEY);
      // the line alone would start a chat without the card's filters: the card stays to click again
      expect(app.box().value).toBe('');
      expect(app.button('Agenzie')).toBeDefined();
      // no line, failed bubble or title of the card is left for the next message's chat
      expect(app.store().getChat(DRAFT_KEY).messages).toEqual([]);
      expect(app.store().getChat(DRAFT_KEY).title).toBeNull();
      expect(toast.error).toHaveBeenCalledOnce();
      // the cards come again from the server, so a card whose segment is gone leaves the screen
      expect(refresh).toHaveBeenCalled();
    },
  );
});

describe('a send blocked for no credits (#1195)', () => {
  it.each([
    ['home', new URLSearchParams()],
    ['a chat with history', new URLSearchParams({ s: 's1' })],
  ])('keeps the text in the composer of %s', async (_name, search) => {
    params = search;
    api();
    const app = mount({ credits: 0 });
    await app.render();
    await vi.waitFor(() => expect(app.box()).not.toBeNull());
    await type(app.box(), 'Trova 50 agenzie a Milano');
    await submit(app.box());

    expect(toast.error).toHaveBeenCalledOnce();
    expect(app.box().value).toBe('Trova 50 agenzie a Milano');
    expect(
      vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/v1/chat')),
    ).toHaveLength(0);
  });
});
