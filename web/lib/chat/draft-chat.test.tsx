// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const token = vi.fn();
vi.mock('@/lib/auth-client', () => ({ authClient: { token: () => token() } }));
vi.mock('@/components/ui/sonner', () => ({
  toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}));
vi.mock('next/navigation', () => ({
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('next/link', () => ({
  default: ({ children, ...props }: React.ComponentProps<'a'>) => <a {...props}>{children}</a>,
}));
vi.mock('@/components/projects/project-switcher', () => ({ ProjectSwitcher: () => null }));
vi.mock('next-intl', () => ({ useTranslations: () => (k: string) => k }));

import { NewChatLink } from '@/components/app/sidebar-chats';
import { IDLE, turnLive } from './chat-turn';
import { ChatStreamProvider, useChatStreamStore } from './stream-provider';
import { ChatStreamStore } from './stream-store';
import { DRAFT_KEY, type UIMessage, type UseChatLabels } from './stream-store-types';
import { useChatEmpty, useChatField, useChatView } from './use-chat';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const labels = {
  networkError: 'network',
  streamTimeout: 'timeout',
  streamInterrupted: 'interrupted',
  creditsExhausted: 'no credits',
  creditsLow: 'low',
  stopFailed: 'not stopped',
  upgradeCta: 'top up',
  terminals: { max_turns_reached: 'stopped early' },
  errorText: ({ code }) => `error ${code}`,
} satisfies UseChatLabels;

function frame(event: string, data: object, id?: number): string {
  return `event: ${event}\n${id ? `id: ${id}\n` : ''}data: ${JSON.stringify(data)}\n\n`;
}

function sse(...frames: string[]): Response {
  const body = new ReadableStream({
    start(c) {
      c.enqueue(new TextEncoder().encode(frames.join('')));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

function openSse() {
  let ctrl: ReadableStreamDefaultController<Uint8Array> | null = null;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c;
    },
  });
  const res = new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  return {
    res,
    push: (s: string) => ctrl?.enqueue(new TextEncoder().encode(s)),
    close: () => ctrl?.close(),
  };
}

const turn = (sid: string) => [
  frame('session', { session_id: sid, job_id: 'j1' }),
  frame('agent_thinking', { turn_seq: 1 }, 1),
  frame('text', { content: 'Found 3.' }, 2),
  frame('message_saved', { message_id: 'm1', status: 'success' }, 3),
  frame('done', {}, 4),
];

async function settled(store: ChatStreamStore, key: string) {
  await vi.waitFor(() => {
    const chat = store.getChat(key);
    expect(chat.messages.length).toBeGreaterThan(0);
    expect(turnLive(chat.turn)).toBe(false);
  });
}

beforeEach(() => {
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
  vi.stubGlobal('cancelAnimationFrame', (h: ReturnType<typeof setTimeout>) => clearTimeout(h));
  token.mockResolvedValue({ data: { token: 'jwt-1' } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('draft chat lifecycle', () => {
  it('a reset after a failed draft turn starts clean, and the next draft turn becomes a session', async () => {
    const store = new ChatStreamStore();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('offline')));
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settled(store, DRAFT_KEY);
    expect(store.getChat(DRAFT_KEY).messages[1]).toMatchObject({ status: 'failed' });

    store.reset(DRAFT_KEY);
    expect(store.getChat(DRAFT_KEY)).toMatchObject({ messages: [], turn: IDLE });
    expect(store.run(DRAFT_KEY).assistantId).toBe('');

    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(sse(...turn('s1'))));
    store.send(DRAFT_KEY, { message: 'again', labels });
    await settled(store, 's1');
    expect(store.getChat('s1').messages.at(-1)).toMatchObject({
      id: 'm1',
      content: 'Found 3.',
      status: 'success',
    });
    expect(store.run('s1')).toMatchObject({ assistantId: 'm1', lastEventId: 4, pendingText: '' });
    expect(store.getChat(DRAFT_KEY).messages).toEqual([]);
    expect(store.state.getState().aliases).toEqual({});
    store.dispose();
  });

  it('a text frame after the first one wakes no store subscriber until the ticker reveals it', async () => {
    vi.stubGlobal('requestAnimationFrame', () => 1);
    const store = new ChatStreamStore();
    const stream = openSse();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(stream.res));
    store.send('s1', { message: 'x', labels });
    await vi.waitFor(() => expect(turnLive(store.getChat('s1').turn)).toBe(true));
    stream.push(frame('text', { content: 'a' }, 1));
    await vi.waitFor(() => expect(store.run('s1').pendingText).toBe('a'));

    let writes = 0;
    const unsubscribe = store.state.subscribe(() => writes++);
    stream.push(frame('text', { content: 'b' }, 2) + frame('text', { content: 'c' }, 3));
    await vi.waitFor(() => expect(store.run('s1').pendingText).toBe('abc'));
    expect(writes).toBe(0);
    unsubscribe();
    stream.close();
    store.dispose();
  });

  it('a reader under the draft key follows the new session, then reads an empty draft', async () => {
    const seen: { draft: UIMessage[]; sid: UIMessage[]; draftEmpty: boolean; status: unknown }[] =
      [];
    let mounted: ChatStreamStore | null = null;
    function Probe() {
      mounted = useChatStreamStore();
      seen.push({
        draft: useChatField(DRAFT_KEY, 'messages'),
        sid: useChatField('s9', 'messages'),
        draftEmpty: useChatEmpty(DRAFT_KEY),
        status: useChatView('s9')?.status,
      });
      return null;
    }
    const root = createRoot(document.createElement('div'));
    await act(async () =>
      root.render(
        <ChatStreamProvider userId="u3">
          <Probe />
        </ChatStreamProvider>,
      ),
    );
    if (!mounted) throw new Error('no store');
    const store: ChatStreamStore = mounted;
    const stream = openSse();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(stream.res));

    await act(async () => store.send(DRAFT_KEY, { message: 'agenzie', labels }));
    await act(async () => {
      await vi.waitFor(() => expect(turnLive(store.getChat(DRAFT_KEY).turn)).toBe(true));
      stream.push(frame('session', { session_id: 's9', job_id: 'j9' }));
      await vi.waitFor(() => expect(seen.at(-1)?.status).toBe('running'));
    });
    const mid = seen.at(-1);
    expect(store.state.getState().aliases).toEqual({ [DRAFT_KEY]: 's9' });
    expect(mid?.draft).toBe(mid?.sid);
    expect(mid?.draft).toHaveLength(2);
    expect(mid?.draftEmpty).toBe(false);
    expect(mid?.status).toBe('running');

    await act(async () => {
      stream.push(frame('text', { content: 'Ciao' }, 1));
      await vi.waitFor(() => expect(seen.at(-1)?.draft.at(-1)?.content).toBe('Ciao'));
    });

    await act(async () => {
      stream.push(
        frame('message_saved', { message_id: 'm9', status: 'success' }, 2) + frame('done', {}, 3),
      );
      stream.close();
      await vi.waitFor(() => expect(turnLive(store.getChat('s9').turn)).toBe(false));
      await vi.waitFor(() => expect(seen.at(-1)?.status).toBe('done'));
    });
    const end = seen.at(-1);
    expect(store.state.getState().aliases).toEqual({});
    expect(end?.draft).toEqual([]);
    expect(end?.draftEmpty).toBe(true);
    expect(end?.sid.at(-1)).toMatchObject({ id: 'm9', content: 'Ciao', status: 'success' });
    expect(end?.status).toBe('done');
    await act(async () => root.unmount());
  });

  it('«Nuova chat» mid first turn empties the draft and the turn finishes in its own chat (#686)', async () => {
    let mounted: ChatStreamStore | null = null;
    function Probe() {
      mounted = useChatStreamStore();
      return null;
    }
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    await act(async () =>
      root.render(
        <ChatStreamProvider userId="u4">
          <Probe />
          <NewChatLink />
        </ChatStreamProvider>,
      ),
    );
    if (!mounted) throw new Error('no store');
    const store: ChatStreamStore = mounted;
    const stream = openSse();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(stream.res));
    await act(async () => store.send(DRAFT_KEY, { message: 'agenzie', labels }));
    await act(async () => {
      stream.push(frame('session', { session_id: 's10', job_id: 'j10' }));
      await vi.waitFor(() =>
        expect(store.state.getState().aliases).toEqual({ [DRAFT_KEY]: 's10' }),
      );
    });
    expect(store.getChat(DRAFT_KEY).messages).toHaveLength(2);

    await act(async () => container.querySelector('a')?.click());

    expect(store.getChat(DRAFT_KEY).messages).toEqual([]);
    expect(turnLive(store.getChat('s10').turn)).toBe(true);
    await act(async () => {
      stream.push(frame('text', { content: 'Ciao' }, 1) + frame('done', {}, 2));
      stream.close();
      await vi.waitFor(() => expect(store.getChat('s10').messages.at(-1)?.content).toBe('Ciao'));
    });
    await act(async () => root.unmount());
  });
});
