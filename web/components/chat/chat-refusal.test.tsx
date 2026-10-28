import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

vi.mock('@/app/(app)/actions', () => ({ dismissWelcomeAction: vi.fn() }));
vi.mock('@/lib/auth-client', () => ({
  authClient: { token: () => Promise.resolve({ data: { token: 'jwt' } }) },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}));
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams() }));

import { TestIntl } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import { turnLive } from '@/lib/chat/chat-turn';
import { ChatStreamStore } from '@/lib/chat/stream-store';
import { DRAFT_KEY, type UseChatLabels } from '@/lib/chat/stream-store-types';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { ChatScroll } from './chat-scroll';
import { ChatWindow } from './chat-window';
import { ComposerTextProvider } from './composer-text';

const labels = {
  networkError: 'Rete assente',
  streamTimeout: 'timeout',
  streamInterrupted: 'interrupted',
  creditsExhausted: 'no credits',
  creditsLow: 'low',
  stopFailed: 'not stopped',
  upgradeCta: 'top up',
  terminals: {},
  errorText: ({ code }) => `error ${code}`,
} satisfies UseChatLabels;

const plan = {
  list_id: 'L1',
  version: 1,
  mode: 'build',
  entity: 'company',
  target_rows: 50,
  columns: [],
  stages: [],
  credits_fixed: 3,
  credits_per_row: 2,
  estimated_credits: 103,
  large_run_credits: 100,
};

function frame(event: string, data: object, id?: number): string {
  return `event: ${event}\n${id ? `id: ${id}\n` : ''}data: ${JSON.stringify(data)}\n\n`;
}

function planTurn(): Response {
  const body = [
    frame('session', { session_id: 's1', job_id: 'j1' }),
    frame(
      'message_saved',
      { message_id: 'm1', status: 'success', question: { question: '', options: [], plan } },
      1,
    ),
    frame('done', {}, 2),
  ].join('');
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

function refusal(status: number, code?: string): Response {
  const body = code ? JSON.stringify({ detail: { code, message: 'x' } }) : '{}';
  return new Response(body, { status });
}

async function settled(store: ChatStreamStore, key: string) {
  await vi.waitFor(() => {
    const chat = store.getChat(key);
    expect(chat.messages.length).toBeGreaterThan(0);
    expect(turnLive(chat.turn)).toBe(false);
  });
}

function screen(store: ChatStreamStore, key: string) {
  const { messages } = store.getChat(key);
  return renderToStaticMarkup(
    <TestIntl>
      <ChatScroll chatKey="test" sessionId="test" empty={false}>
        <ComposerTextProvider sessionId={null}>
          <ChatWindow
            isNewChat={false}
            messages={messages}
            isStreaming={false}
            credits={null}
            companyRows={null}
            brief={null}
            creditsUsed={0}
            progress={null}
            jobId={null}
            offline={false}
            overviewRequest={Promise.resolve(null)}
            viewer={{ firstName: null, id: null }}
            onRetry={() => {}}
            onSend={() => {}}
            onStop={() => {}}
          />
        </ComposerTextProvider>
      </ChatScroll>
    </TestIntl>,
  );
}

const approveButton =
  /<button(?![^>]*\sdisabled="")[^>]*>(?:<svg(?:(?!<\/svg>).)*<\/svg>)Approva e avvia/;

beforeEach(() => {
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
  vi.stubGlobal('cancelAnimationFrame', (h: ReturnType<typeof setTimeout>) => clearTimeout(h));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a refused request in the rendered chat', () => {
  it('shows a 422 PLAN.STAGE_REQUIRED and leaves the plan open', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(planTurn());
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'brief', labels });
    await settled(store, 's1');
    expect(screen(store, 's1')).toMatch(approveButton);

    fetchMock.mockResolvedValueOnce(refusal(422, 'PLAN.STAGE_REQUIRED'));
    store.send('s1', {
      message: 'Piano v1 approvato',
      planApproval: { listId: 'L1', version: 1, rows: 50, droppedStages: [] },
      labels,
    });
    await settled(store, 's1');

    const html = screen(store, 's1');
    expect(html).toContain('error PLAN.STAGE_REQUIRED');
    expect(html).not.toContain('Piano v1 approvato');
    expect(html).toMatch(approveButton);
  });

  it('reloads a stale plan instead of keeping the old version (#260)', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(planTurn());
    vi.stubGlobal('fetch', fetchMock);
    const newer = { ...toCamelCase<typeof plan>(plan), version: 2 };
    const recoverSession = vi.fn().mockResolvedValue({
      messages: [
        { id: 'u1', role: 'user', content: 'brief', createdAt: '2026-10-05T10:00:00Z' },
        {
          id: 'm2',
          role: 'assistant',
          content: '',
          status: 'success',
          createdAt: '2026-10-05T10:01:00Z',
          question: { question: '', options: [], plan: newer },
        },
      ],
      lists: {},
      jobId: null,
      title: null,
      progress: null,
    });
    const store = new ChatStreamStore({ recoverSession });
    store.send(DRAFT_KEY, { message: 'brief', labels });
    await settled(store, 's1');

    fetchMock.mockResolvedValueOnce(refusal(409, 'PLAN.STALE'));
    store.send('s1', {
      message: 'Piano v1 approvato',
      planApproval: { listId: 'L1', version: 1, rows: 50, droppedStages: [] },
      labels,
    });
    await vi.waitFor(() => expect(screen(store, 's1')).toContain('Piano v2'));

    expect(recoverSession).toHaveBeenCalledWith('s1');
    expect(toast.error).toHaveBeenCalledWith('error PLAN.STALE');
    const html = screen(store, 's1');
    expect(html).not.toContain('Piano v1');
    expect(html).toMatch(approveButton);
  });
});
