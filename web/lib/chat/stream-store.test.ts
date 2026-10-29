import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const token = vi.fn();
vi.mock('@/lib/auth-client', () => ({ authClient: { token: () => token() } }));
vi.mock('@/components/ui/sonner', () => ({
  toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}));

import framesEmailRefunded from '@/lib/api/fixtures/frames-email-refunded.json';
import summaryEmailRefunded from '@/lib/api/fixtures/summary-email-refunded.json';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { listsFromSession } from './chat-lists';
import { IDLE, turnLive, turnOffline } from './chat-turn';
import { ChatStreamStore } from './stream-store';
import type { RecoveredSession } from './stream-store-types';
import { DRAFT_KEY, type UseChatLabels } from './stream-store-types';

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

/** The wire keys of a saved message that no test here sets. */
const SAVED_ROW = { sources: null, status: null, terminalCode: null, question: null };

function frame(event: string, data: unknown, id?: number): string {
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

/** An SSE response the test keeps open and feeds frame by frame. */
function liveSSE() {
  let feed: ReadableStreamDefaultController<Uint8Array> | undefined;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      feed = c;
    },
  });
  return {
    response: new Response(body, { status: 200 }),
    send: (...frames: string[]) => feed?.enqueue(new TextEncoder().encode(frames.join(''))),
    close: () => feed?.close(),
  };
}

const TURN = [
  frame('session', { session_id: 's1', job_id: 'j1' }),
  frame('agent_thinking', { turn_seq: 1 }, 1),
  frame(
    'tool_start',
    { tool_id: 't1', tool_name: 'search_companies', tool_input: { query: 'x' } },
    2,
  ),
  frame(
    'tool_result',
    {
      tool_id: 't1',
      tool_name: 'search_companies',
      result_preview: 'ok',
      status: 'success',
      summary: { kind: 'count', count: 3, noun: 'company' },
    },
    3,
  ),
  frame('credits', { tool_id: 't1', tool_name: 'search_companies', cost: 5, remaining: 95 }, 4),
  frame('list_updated', { list_id: 'L1', entity: 'company', rows_added: 3, row_count: 3 }, 5),
  frame('text', { content: 'Found 3.' }, 6),
  frame('message_saved', { message_id: 'm1', status: 'success' }, 7),
  frame(
    'done',
    {
      sources: [{ url: 'https://a.it', title: 'A', domain: 'a.it', kind: 'web' }],
      list_id: 'L1',
      job_id: 'j1',
    },
    8,
  ),
];

async function settle(store: ChatStreamStore, key: string) {
  await vi.waitFor(() => {
    const chat = store.getChat(key);
    expect(chat.messages.length).toBeGreaterThan(0);
    expect(turnLive(chat.turn)).toBe(false);
  });
}

beforeEach(async () => {
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
  vi.stubGlobal('cancelAnimationFrame', (h: ReturnType<typeof setTimeout>) => clearTimeout(h));
  token.mockResolvedValue({ data: { token: 'jwt-1' } });
  token.mockClear();
});

const stores: ChatStreamStore[] = [];
function trackedStore(opts?: ConstructorParameters<typeof ChatStreamStore>[0]) {
  const store = new ChatStreamStore(opts);
  stores.push(store);
  return store;
}

afterEach(() => {
  for (const store of stores.splice(0)) store.dispose();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const TWO_LISTS_TURN = [
  frame('session', { session_id: 's5', job_id: 'j5' }),
  frame('credits', { tool_id: 't1', tool_name: 'search_companies', cost: 5, remaining: 95 }, 1),
  frame('list_updated', { list_id: 'LC', entity: 'company', rows_added: 4, row_count: 4 }, 2),
  frame('credits', { tool_id: 't2', tool_name: 'find_people', cost: 8, remaining: 87 }, 3),
  frame('list_updated', { list_id: 'LP', entity: 'person', rows_added: 6, row_count: 6 }, 4),
  frame(
    'credits',
    { tool_id: 't2', tool_name: 'find_people', cost: -3, remaining: 90, refund: true },
    5,
  ),
  frame('list_updated', { list_id: 'LP', entity: 'person', scored: 6 }, 6),
  frame('message_saved', { message_id: 'm5', status: 'success' }, 7),
  frame('done', {}, 8),
];

describe('ChatStreamStore over the Zenbree SSE protocol', () => {
  it('rejects a send with no credits left and starts no turn', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();
    store.seedCredits(0);
    const onRejected = vi.fn();

    store.send(DRAFT_KEY, { message: 'agenzie a Milano', labels, onRejected });

    expect(onRejected).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(store.getChat(DRAFT_KEY).messages).toEqual([]);
  });

  it('migrates the draft and folds every event into the turn', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sse(...TURN));
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();

    store.send(DRAFT_KEY, { message: 'agenzie a Milano', labels });
    await settle(store, 's1');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/chat$/);
    expect(JSON.parse(init.body as string)).toEqual({ message: 'agenzie a Milano' });
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer jwt-1');

    const chat = store.getChat('s1');
    expect(chat).toMatchObject({ sessionId: 's1', jobId: 'j1' });
    expect(store.run('s1').lastEventId).toBe(8);
    expect(chat.lists.company).toMatchObject({ id: 'L1', rowCount: 3, version: 1 });
    const [, assistant] = chat.messages;
    expect(assistant).toMatchObject({ id: 'm1', status: 'success', content: 'Found 3.' });
    expect(assistant?.toolCalls[0]).toMatchObject({
      id: 't1',
      status: 'done',
      result: 'ok',
      creditsUsed: 5,
      summary: { kind: 'count', count: 3, noun: 'company' },
    });
    expect(assistant?.parts.map((p) => p.kind)).toEqual(['tool', 'text']);
    expect(assistant?.sources?.[0]).toMatchObject({ url: 'https://a.it', sourceKind: 'web' });
    expect(store.getGlobal().credits).toBe(95);
    // The draft alias is released at the end of the turn.
    expect(store.getChat(DRAFT_KEY).messages).toEqual([]);
  });

  it('reads the typed brief and memory note of a tool result', async () => {
    const brief = { who: ['CEO'], companies: [], where: ['Milano'], signal: [], email: null };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse(
          frame('session', { session_id: 's8', job_id: 'j8' }),
          frame('tool_start', { tool_id: 'b', tool_name: 'set_brief', tool_input: {} }, 1),
          frame(
            'tool_result',
            {
              tool_id: 'b',
              tool_name: 'set_brief',
              result_preview: '{"who":',
              status: 'success',
              brief,
            },
            2,
          ),
          frame('tool_start', { tool_id: 'r', tool_name: 'remember', tool_input: {} }, 3),
          frame(
            'tool_result',
            {
              tool_id: 'r',
              tool_name: 'remember',
              result_preview: '{',
              status: 'success',
              memory_saved: 'Vende HR',
            },
            4,
          ),
          frame('done', {}, 5),
        ),
      ),
    );
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'agencies', labels });
    await settle(store, 's8');
    expect(store.getChat('s8').messages[1]?.toolCalls).toMatchObject([
      { name: 'set_brief', brief },
      { name: 'remember', memorySaved: 'Vende HR' },
    ]);
  });

  it('sends a plan approval with the message and reads back a plan question', async () => {
    const plan = { list_id: 'L1', version: 2, estimated_credits: 203, credits_per_row: 2 };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sse(
          frame('session', { session_id: 's7', job_id: 'j7' }),
          frame(
            'message_saved',
            { message_id: 'm7', status: 'success', question: { question: '', options: [], plan } },
            1,
          ),
          frame('done', {}, 2),
        ),
      );
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, {
      message: 'Approved',
      planApproval: { listId: 'L1', version: 2, rows: 40, droppedStages: [1] },
      labels,
    });
    await settle(store, 's7');
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      message: 'Approved',
      plan_approval: { list_id: 'L1', version: 2, rows: 40, dropped_stages: [1] },
    });
    expect(store.getChat('s7').messages[1]?.question?.plan).toMatchObject({
      listId: 'L1',
      estimatedCredits: 203,
      creditsPerRow: 2,
    });
  });

  it('sends the goal on the message that creates the session, never on a later one', async () => {
    const turn = (sid: string) =>
      sse(frame('session', { session_id: sid, job_id: 'j' }), frame('done', {}, 1));
    const fetchMock = vi.fn().mockImplementation(async () => turn('s9'));
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'find CEOs', goal: 'campaign', labels });
    await settle(store, 's9');
    store.send('s9', { message: 'more', goal: 'campaign', labels });
    await settle(store, 's9');
    const bodies = fetchMock.mock.calls.map(([, init]) =>
      JSON.parse((init as RequestInit).body as string),
    );
    expect(bodies[0]).toMatchObject({ message: 'find CEOs', goal: 'campaign' });
    expect(bodies[1]).toEqual({ message: 'more', session_id: 's9' });
  });

  it('sends a segment on the message that creates the session, never on a later one', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () =>
        sse(frame('session', { session_id: 's9', job_id: 'j' }), frame('done', {}, 1)),
      );
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'line', segment: 'k1', title: 'Agenzie', labels });
    // the header names the chat after the segment before the server answers
    expect(store.getChat(DRAFT_KEY).title).toBe('Agenzie');
    await settle(store, 's9');
    store.send('s9', { message: 'more', segment: 'k2', title: 'y', labels });
    await settle(store, 's9');
    const bodies = fetchMock.mock.calls.map(([, init]) =>
      JSON.parse((init as RequestInit).body as string),
    );
    // the server writes the chat's message and title from the segment
    expect(bodies[0]).toEqual({ segment: 'k1' });
    expect(bodies[1]).toEqual({ message: 'more', session_id: 's9' });
  });

  it('sends the interface language with each message, and none it does not declare', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () =>
        sse(frame('session', { session_id: 's9', job_id: 'j' }), frame('done', {}, 1)),
      );
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();
    vi.stubGlobal('document', { cookie: '', documentElement: { lang: 'it' } });
    store.send(DRAFT_KEY, { message: 'trovami clienti', labels });
    await settle(store, 's9');
    vi.stubGlobal('document', { cookie: '', documentElement: { lang: 'de' } });
    store.send('s9', { message: 'altri', labels });
    await settle(store, 's9');
    const bodies = fetchMock.mock.calls.map(([, init]) =>
      JSON.parse((init as RequestInit).body as string),
    );
    expect(bodies).toEqual([
      { message: 'trovami clienti', locale: 'it' },
      { message: 'altri', session_id: 's9' },
    ]);
  });

  it('keeps a terminal code localized instead of gluing text', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(
            frame('session', { session_id: 's2', job_id: 'j2' }),
            frame('error', { message: 'Stopped', code: 'max_turns_reached' }, 1),
            frame('done', {}, 2),
          ),
        ),
    );
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's2');
    expect(store.getChat('s2').messages[1]).toMatchObject({
      status: 'partial',
      terminalCode: 'max_turns_reached',
      content: '',
    });
  });

  it('marks a failed job on the message without touching its text', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(
            frame('session', { session_id: 's9', job_id: 'j9' }),
            frame('text', { content: 'half' }, 1),
            frame('error', { message: 'Something went wrong while running this request.' }, 2),
            frame('done', {}, 3),
          ),
        ),
    );
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's9');
    const msg = store.getChat('s9').messages[1];
    expect(msg).toMatchObject({ status: 'failed', turnFailed: true, content: 'half' });
    expect(msg?.parts.map((p) => p.kind)).toEqual(['text']);
  });

  it('marks a turn cut when the stream ends without done', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(frame('session', { session_id: 's3' }), frame('text', { content: 'half' }, 1)),
        ),
    );
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's3');
    expect(store.getChat('s3').messages[1]).toMatchObject({
      content: 'half',
      interrupted: 'partial',
    });
    expect(turnOffline(store.getChat('s3').turn)).toBe(true);
  });

  it('refreshes the token once on 401 and retries', async () => {
    token
      .mockResolvedValueOnce({ data: { token: 'stale' } })
      .mockResolvedValueOnce({ data: { token: 'fresh' } });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(sse(...TURN));
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();

    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's1');

    const auth = fetchMock.mock.calls.map(
      ([, init]) =>
        (init as RequestInit & { headers: Record<string, string> }).headers.Authorization,
    );
    expect(auth).toEqual(['Bearer stale', 'Bearer fresh']);
    expect(store.getChat('s1').messages[1]?.content).toBe('Found 3.');
  });

  it('a 409 (another job running) drops the rejected turn and keeps the question that offered the pick', async () => {
    const { toast } = await import('@/components/ui/sonner');
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(sse(...TURN))
        .mockResolvedValueOnce(
          Response.json({ detail: { code: 'CHAT.JOB_RUNNING', message: 'x' } }, { status: 409 }),
        ),
    );
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's1');
    const before = store.getChat('s1').messages;
    store.send('s1', { message: 'Senza il segnale', pick: 0, labels });
    await vi.waitFor(() => expect(turnLive(store.getChat('s1').turn)).toBe(false));
    expect(store.getChat('s1').messages).toEqual(before);
    expect(toast.error).toHaveBeenCalledWith('error CHAT.JOB_RUNNING');
  });

  it('sends the index of the picked alternative with the message', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sse(...TURN));
    vi.stubGlobal('fetch', fetchMock);
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'Senza il segnale', pick: 1, labels });
    await settle(store, 's1');
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ message: 'Senza il segnale', pick: 1 });
  });

  it('fails the turn with the code of a PLAN 409, never as another running job', async () => {
    const body = JSON.stringify({ detail: { code: 'PLAN.STALE', message: 'x' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 409 })));
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, DRAFT_KEY);
    expect(store.getChat(DRAFT_KEY).messages[1]).toMatchObject({
      content: 'error PLAN.STALE',
      status: 'failed',
    });
  });

  it('keeps both lists of a turn, and a scoring frame bumps only its own list', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sse(...TWO_LISTS_TURN)));
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's5');
    const chat = store.getChat('s5');
    expect(chat.lists).toEqual({
      company: { id: 'LC', entity: 'company', name: null, rowCount: 4, version: 1 },
      person: { id: 'LP', entity: 'person', name: null, rowCount: 6, version: 2 },
    });
    expect(chat.activeEntity).toBe('person');
  });

  it('gives a refund back to the call that charged it, and names the refunded rows on its chip: the frame replaces the summary (#659)', async () => {
    const call = framesEmailRefunded.map(([event, data], i) => frame(String(event), data, i + 1));
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(
            frame('session', { session_id: 's9', job_id: 'j9' }),
            ...call,
            frame('message_saved', { message_id: 'm9', status: 'success' }, call.length + 1),
            frame('done', {}, call.length + 2),
          ),
        ),
    );
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's9');
    const assistant = store.getChat('s9').messages[1];
    expect(assistant?.creditsUsed).toBe(2);
    // The chip reads what the turn stores for the call once it ends.
    expect(assistant?.toolCalls[0]).toMatchObject({
      creditsUsed: 2,
      summary: toCamelCase(summaryEmailRefunded),
    });
  });

  it('registers the list of a pending person plan with zero rows', async () => {
    const plan = { list_id: 'LP', entity: 'person', version: 1 };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse(
          frame('session', { session_id: 's6', job_id: 'j6' }),
          frame(
            'message_saved',
            {
              message_id: 'm6',
              status: 'success',
              question: { question: '', options: [], plan },
            },
            1,
          ),
          frame('done', {}, 2),
        ),
      ),
    );
    const store = new ChatStreamStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's6');
    expect(store.getChat('s6').lists).toEqual({
      person: { id: 'LP', entity: 'person', name: null, rowCount: 0, version: 0 },
    });
  });

  it('rebuilds the lists and credits from a loaded session', () => {
    const store = new ChatStreamStore();
    store.loadSession(
      's8',
      's8',
      [
        {
          id: 'u',
          sessionId: 's8',
          role: 'user',
          content: 'q',
          toolCalls: null,
          creditsUsed: 0,
          createdAt: '2026-10-01T10:00:00Z',
          ...SAVED_ROW,
        },
        {
          id: 'a',
          sessionId: 's8',
          role: 'assistant',
          content: 'r',
          toolCalls: null,
          creditsUsed: 7,
          createdAt: '2026-10-01T10:00:05Z',
          ...SAVED_ROW,
        },
      ],
      {
        lists: listsFromSession([
          { id: 'old', name: 'Old people', entity: 'person', rowCount: 1 },
          { id: 'LC', name: 'Agenzie', entity: 'company', rowCount: 31 },
          { id: 'LP', name: 'Persone', entity: 'person', rowCount: 12 },
          { id: 'x', name: 'Import', entity: 'enrich', rowCount: 9 },
        ]),
        title: 'Agenzie a Milano',
      },
    );
    const chat = store.getChat('s8');
    expect(chat.lists).toEqual({
      company: { id: 'LC', entity: 'company', name: 'Agenzie', rowCount: 31, version: 0 },
      person: { id: 'LP', entity: 'person', name: 'Persone', rowCount: 12, version: 0 },
    });
    expect(chat.title).toBe('Agenzie a Milano');
    expect(chat.activeEntity).toBeNull();
    expect(chat.messages.map((m) => m.creditsUsed)).toEqual([undefined, 7]);
  });
});

const STAGES = [
  { tool: 'people', label: 'Ricerca persone', units: 100, done: 100 },
  { tool: 'emails', label: 'Ricerca email', units: 100, done: 64, error: { code: 'provider' } },
];

describe('plan progress and connection loss (#213)', () => {
  it('keeps the plan stages from a stages frame, error included', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(
            frame('session', { session_id: 's10', job_id: 'j10' }),
            frame('stages', { stages: STAGES, phase: null, credits_spent: 86 }, 1),
            frame('message_saved', { message_id: 'm10', status: 'success' }, 2),
            frame('done', {}, 3),
          ),
        ),
    );
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's10');
    expect(store.getChat('s10').progress).toEqual({
      stages: STAGES,
      phase: null,
      creditsSpent: 86,
      retryStage: null,
      listId: null,
    });
  });

  it('sends the retry with the message, and keeps the card until the new turn reports', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sse(frame('session', { session_id: 's11', job_id: 'j12' }), frame('done', {}, 1)),
      );
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.loadSession('s11', 's11', [], {
      jobId: 'j11',
      progress: {
        stages: STAGES as never,
        phase: null,
        creditsSpent: 86,
        retryStage: null,
        listId: null,
      },
    });
    store.send('s11', {
      message: 'Riprova: Ricerca email',
      retryStage: { jobId: 'j11', stage: 1 },
      labels,
    });
    expect(store.getChat('s11').progress?.stages).toHaveLength(2);
    await settle(store, 's11');
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      message: 'Riprova: Ricerca email',
      session_id: 's11',
      retry_stage: { job_id: 'j11', stage: 1 },
    });
  });

  it('forgets the plan card when the next turn is not a retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sse(frame('session', { session_id: 's11' }))));
    const store = trackedStore();
    store.loadSession('s11', 's11', [], {
      progress: {
        stages: STAGES as never,
        phase: null,
        creditsSpent: 86,
        retryStage: null,
        listId: null,
      },
    });
    store.send('s11', { message: 'altro', labels });
    expect(store.getChat('s11').progress).toBeNull();
    await settle(store, 's11');
  });

  it('flags a cut stream as lost without writing text, then rejoins from the last frame', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        sse(frame('session', { session_id: 's12', job_id: 'j12' }), frame('agent_thinking', {}, 1)),
      )
      .mockResolvedValueOnce(
        sse(
          frame('text', { content: 'Done.' }, 2),
          frame('message_saved', { message_id: 'm12', status: 'success' }, 3),
          frame('done', {}, 4),
        ),
      );
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(500);
    expect(turnOffline(store.getChat('s12').turn)).toBe(true);
    expect(store.getChat('s12').messages[1]?.content).toBe('');

    await vi.advanceTimersByTimeAsync(500);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init] = fetchMock.mock.calls[1] as [
      string,
      RequestInit & { headers: Record<string, string> },
    ];
    expect(url).toMatch(/\/v1\/chat\/sessions\/s12\/stream$/);
    expect(init.headers['Last-Event-ID']).toBe('1');
    await vi.advanceTimersByTimeAsync(100);
    expect(store.getChat('s12').messages[1]?.id).toBe('m12');
    expect(store.getChat('s12').turn).toEqual(IDLE);
    expect(store.getChat('s12').messages[1]).toMatchObject({
      content: 'Done.',
      interrupted: undefined,
    });
  });

  it('keeps trying while the network is down', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sse(frame('session', { session_id: 's13', job_id: 'j13' })))
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValue(
        sse(
          frame('message_saved', { message_id: 'm13', status: 'success' }, 1),
          frame('done', {}, 2),
        ),
      );
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(1000 + 2000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(turnOffline(store.getChat('s13').turn)).toBe(false);
    expect(store.getChat('s13').messages[1]?.content).toBe('');
  });

  it('reloads the session from the server when the job is gone', async () => {
    vi.useFakeTimers();
    const saved: RecoveredSession = {
      messages: [
        {
          id: 'u',
          sessionId: 's14',
          role: 'user',
          content: 'x',
          toolCalls: null,
          creditsUsed: 0,
          createdAt: '2026-10-05T10:00:00Z',
          ...SAVED_ROW,
        },
        {
          id: 'a',
          sessionId: 's14',
          role: 'assistant',
          content: 'Saved answer',
          toolCalls: null,
          creditsUsed: 4,
          createdAt: '2026-10-05T10:00:05Z',
          ...SAVED_ROW,
        },
      ],
      lists: {},
      jobId: 'j14',
      title: 'x',
      progress: {
        stages: STAGES as never,
        phase: null,
        creditsSpent: 4,
        retryStage: null,
        listId: null,
      },
    };
    const recoverSession = vi.fn().mockResolvedValue(saved);
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(sse(frame('session', { session_id: 's14', job_id: 'j14' })))
        .mockResolvedValueOnce(new Response(null, { status: 404 })),
    );
    const store = trackedStore({ recoverSession });
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(1000 + 2000);
    expect(recoverSession).toHaveBeenCalledWith('s14');
    expect(store.getChat('s14').messages[1]?.content).toBe('Saved answer');
    expect(turnOffline(store.getChat('s14').turn)).toBe(false);
    expect(store.getChat('s14').progress?.creditsSpent).toBe(4);
  });

  it('does not rejoin a stream the user stopped', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(sse(frame('session', { session_id: 's15' })));
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(500);
    expect(turnOffline(store.getChat('s15').turn)).toBe(true);
    store.stop('s15');
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('text the model wrote before a tool call (#567)', () => {
  const SEARCH_AND_ANSWER = [
    frame('text_dropped', {}, 2),
    frame('tool_start', { tool_id: 't1', tool_name: 'search_companies', tool_input: {} }, 3),
    frame('tool_result', { tool_id: 't1', tool_name: 'search_companies', status: 'success' }, 4),
    frame('agent_thinking', { turn_seq: 1 }, 5),
    frame('text', { content: 'Found 3.' }, 6),
    frame('message_saved', { message_id: 'm40', status: 'success' }, 7),
    frame('done', {}, 8),
  ];

  it('drops the line still waiting to be typed', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(
            frame('session', { session_id: 's40', job_id: 'j40' }),
            frame('text', { content: 'I will look them up first.' }, 1),
            ...SEARCH_AND_ANSWER,
          ),
        ),
    );
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'agencies', labels });
    await settle(store, 's40');
    const msg = store.getChat('s40').messages[1];
    expect(msg?.content).toBe('Found 3.');
    expect(msg?.parts.map((p) => p.kind)).toEqual(['tool', 'text']);
  });

  it('drops the line already on screen', async () => {
    const live = liveSSE();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(live.response));
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'agencies', labels });
    live.send(
      frame('session', { session_id: 's40', job_id: 'j40' }),
      frame('text', { content: 'I will look them up first.' }, 1),
    );
    await vi.waitFor(() =>
      expect(store.getChat('s40').messages[1]?.content).toBe('I will look them up first.'),
    );
    live.send(...SEARCH_AND_ANSWER);
    live.close();
    await settle(store, 's40');
    const msg = store.getChat('s40').messages[1];
    expect(msg?.content).toBe('Found 3.');
    expect(msg?.parts.map((p) => p.kind)).toEqual(['tool', 'text']);
  });

  it('«Nuova chat» mid-turn empties the draft and the turn keeps streaming in its own chat (#686)', async () => {
    const live = liveSSE();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(live.response));
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'agencies', labels });
    live.send(frame('session', { session_id: 's86', job_id: 'j86' }));
    await vi.waitFor(() => expect(store.getChat('s86').sessionId).toBe('s86'));

    store.startNewChat();

    expect(store.getChat(DRAFT_KEY).sessionId).toBeNull();
    expect(turnLive(store.getChat('s86').turn)).toBe(true);
    live.send(...SEARCH_AND_ANSWER);
    live.close();
    await settle(store, 's86');
    expect(store.getChat('s86').messages[1]?.content).toBe('Found 3.');
    expect(store.getChat(DRAFT_KEY).messages).toEqual([]);
  });

  it('«Nuova chat» before the session frame releases the draft once it arrives (#686)', async () => {
    const live = liveSSE();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(live.response));
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'agencies', labels });
    store.startNewChat();
    live.send(frame('session', { session_id: 's87', job_id: 'j87' }));
    await vi.waitFor(() => expect(store.getChat('s87').sessionId).toBe('s87'));

    expect(store.getChat(DRAFT_KEY).sessionId).toBeNull();
    expect(turnLive(store.getChat('s87').turn)).toBe(true);
    live.close();
  });

  it('a cut first turn reattaches to its own chat after the draft sent another brief (#686)', async () => {
    vi.useFakeTimers();
    const first = liveSSE();
    const second = new Response('{}', { status: 409 });
    const fetchMock = vi.fn().mockResolvedValueOnce(first.response).mockResolvedValueOnce(second);
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'agencies', labels });
    first.send(frame('session', { session_id: 's88', job_id: 'j88' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getChat('s88').sessionId).toBe('s88');
    store.startNewChat();
    store.send(DRAFT_KEY, { message: 'again', labels });

    first.close();
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock.mock.calls.length).toBeGreaterThan(2);
    expect(String(fetchMock.mock.calls[2]?.[0])).toContain('s88');
  });
});

describe('«Ferma» (#260)', () => {
  it('asks the server to stop the job and keeps the stream for its closing frames', async () => {
    const live = liveSSE();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(live.response));
    const cancelJob = vi.fn().mockResolvedValue('cancelling');
    const store = trackedStore({ cancelJob });
    store.send(DRAFT_KEY, {
      message: 'x',
      labels: { ...labels, terminals: { cancelled: 'fermato' } },
    });
    live.send(
      frame('session', { session_id: 's30', job_id: 'j30' }),
      frame('text', { content: 'Cerco' }, 1),
    );
    await vi.waitFor(() => expect(store.getChat('s30').jobId).toBe('j30'));

    store.stop('s30', labels);
    store.stop('s30', labels);
    expect(cancelJob).toHaveBeenCalledTimes(1);
    expect(cancelJob).toHaveBeenCalledWith('j30');
    expect(store.getChat('s30').turn).toEqual({ phase: 'stopping', jobId: 'j30' });

    live.send(
      frame('error', { message: 'Stopped', code: 'cancelled' }, 2),
      frame('message_saved', { message_id: 'm30', status: 'partial' }, 3),
      frame('done', {}, 4),
    );
    live.close();
    await settle(store, 's30');
    expect(store.getChat('s30').turn).toEqual(IDLE);
    expect(store.getChat('s30').messages[1]).toMatchObject({
      status: 'partial',
      terminalCode: 'cancelled',
      content: 'Cerco',
    });
  });

  it('says so when the stop request fails, and the turn goes on', async () => {
    const { toast } = await import('@/components/ui/sonner');
    const live = liveSSE();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(live.response));
    const store = trackedStore({ cancelJob: vi.fn().mockResolvedValue(null) });
    store.send(DRAFT_KEY, { message: 'x', labels });
    live.send(frame('session', { session_id: 's31', job_id: 'j31' }));
    await vi.waitFor(() => expect(store.getChat('s31').jobId).toBe('j31'));

    store.stop('s31', labels);
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith(labels.stopFailed));
    expect(store.getChat('s31').turn).toEqual({ phase: 'streaming', jobId: 'j31' });
    live.close();
  });

  it('stops the new turn’s job once its `session` frame names it, never the last turn’s', async () => {
    const live = liveSSE();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sse(...TURN))
      .mockResolvedValueOnce(live.response);
    vi.stubGlobal('fetch', fetchMock);
    const cancelJob = vi.fn().mockResolvedValue('cancelling');
    const store = trackedStore({ cancelJob });
    store.send(DRAFT_KEY, { message: 'x', labels });
    await settle(store, 's1');
    store.send('s1', { message: 'y', labels });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    store.stop('s1', labels);
    expect(cancelJob).not.toHaveBeenCalled();
    live.send(frame('session', { session_id: 's1', job_id: 'j2' }));
    await vi.waitFor(() => expect(cancelJob).toHaveBeenCalledWith('j2'));
    expect(cancelJob).toHaveBeenCalledTimes(1);
    expect(store.getChat('s1').turn).toEqual({ phase: 'stopping', jobId: 'j2' });
    live.close();
  });

  it('stops the running job of a chat reloaded mid-turn before its stream answers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
    const cancelJob = vi.fn().mockResolvedValue('cancelling');
    const store = trackedStore({ cancelJob });
    const question = {
      id: 'u',
      sessionId: 's32',
      role: 'user' as const,
      content: 'x',
      toolCalls: null,
      creditsUsed: 0,
      createdAt: '2026-10-05T10:00:00Z',
      ...SAVED_ROW,
    };
    store.loadSession('s32', 's32', [question], { jobId: 'j32' });
    store.reattachUnanswered('s32', labels);

    store.stop('s32', labels);
    expect(cancelJob).toHaveBeenCalledWith('j32');
  });
});

describe('reconnecting (#213)', () => {
  const countReattaches = (fetchMock: ReturnType<typeof vi.fn>) =>
    fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/stream')).length;

  it('backs off 1 s, 2 s, 4 s while the API is down, and gives up at the cap', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sse(frame('session', { session_id: 's20', job_id: 'j20' })))
      .mockRejectedValue(new TypeError('offline'));
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(7500);
    expect(countReattaches(fetchMock)).toBe(3);
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(countReattaches(fetchMock)).toBe(10);
  });

  it('treats a 502 on rejoin as the connection, never as the answer', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sse(frame('session', { session_id: 's21', job_id: 'j21' })))
      .mockResolvedValueOnce(new Response(null, { status: 502 }))
      .mockResolvedValue(
        sse(
          frame('text', { content: 'Done.' }, 1),
          frame('message_saved', { message_id: 'm21', status: 'success' }, 2),
          frame('done', {}, 3),
        ),
      );
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(1100);
    expect(turnOffline(store.getChat('s21').turn)).toBe(true);
    expect(store.getChat('s21').messages[1]).toMatchObject({ content: '' });
    expect(store.getChat('s21').messages[1]?.status).toBeUndefined();
    await vi.advanceTimersByTimeAsync(2100);
    expect(store.getChat('s21').messages[1]).toMatchObject({ content: 'Done.' });
  });

  it('keeps following when the session reload fails after a cut rejoin', async () => {
    vi.useFakeTimers();
    const recoverSession = vi.fn().mockResolvedValue(null);
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(sse(frame('session', { session_id: 's22', job_id: 'j22' })))
        .mockResolvedValueOnce(sse(frame('agent_thinking', {}, 1)))
        .mockResolvedValue(new Response(null, { status: 404 })),
    );
    const store = trackedStore({ recoverSession });
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(recoverSession.mock.calls.length).toBeGreaterThan(1);
    expect(turnOffline(store.getChat('s22').turn)).toBe(true);
  });

  it('spaces the reloads of a gone job instead of firing them back to back', async () => {
    vi.useFakeTimers();
    const saved: RecoveredSession = {
      messages: [
        {
          id: 'u',
          sessionId: 's23',
          role: 'user',
          content: 'x',
          toolCalls: null,
          creditsUsed: 0,
          createdAt: '2026-10-05T10:00:00Z',
          ...SAVED_ROW,
        },
      ],
      lists: {},
      jobId: 'j23',
      title: 'x',
      progress: null,
    };
    const recoverSession = vi.fn().mockResolvedValue(saved);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sse(frame('session', { session_id: 's23', job_id: 'j23' })))
      .mockResolvedValue(new Response(null, { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore({ recoverSession });
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(1500);
    expect(recoverSession.mock.calls.length).toBeLessThanOrEqual(2);
    expect(countReattaches(fetchMock)).toBeLessThanOrEqual(2);
  });

  it('drops a retry the server calls stale: no bubble, the card reloads', async () => {
    const stale = new Response(JSON.stringify({ detail: { code: 'RETRY.STALE' } }), {
      status: 409,
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stale));
    const recoverSession = vi.fn().mockResolvedValue(null);
    const store = trackedStore({ recoverSession });
    store.loadSession('s24', 's24', [], {
      jobId: 'j24',
      progress: {
        stages: STAGES as never,
        phase: null,
        creditsSpent: 86,
        retryStage: null,
        listId: null,
      },
    });
    store.send('s24', { message: 'Riprova: x', retryStage: { jobId: 'j24', stage: 1 }, labels });
    await vi.waitFor(() => expect(recoverSession).toHaveBeenCalledWith('s24'));
    expect(store.getChat('s24').messages).toHaveLength(0);
    expect(turnLive(store.getChat('s24').turn)).toBe(false);
  });

  it('gives up the rejoin when «Ferma» is pressed during it', async () => {
    vi.useFakeTimers();
    const hang = (_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) =>
        init.signal?.addEventListener('abort', () => reject(new DOMException('', 'AbortError'))),
      );
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sse(frame('session', { session_id: 's25', job_id: 'j25' })))
      .mockImplementation(hang);
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(1100);
    expect(countReattaches(fetchMock)).toBe(1);

    store.stop('s25', labels);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(countReattaches(fetchMock)).toBe(1);
    expect(turnOffline(store.getChat('s25').turn)).toBe(false);
  });

  it('sends nothing while the connection is lost', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sse(frame('session', { session_id: 's26', job_id: 'j26' })))
      .mockResolvedValue(new Response(null, { status: 502 }));
    vi.stubGlobal('fetch', fetchMock);
    const store = trackedStore();
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(100);

    store.send('s26', { message: 'y', labels });
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(store.getChat('s26').messages).toHaveLength(2);
  });

  it('keeps a turn sent during the reload backoff live when that reload fails', async () => {
    vi.useFakeTimers();
    const recoverSession = vi.fn().mockResolvedValue(null);
    const next = liveSSE();
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(sse(frame('session', { session_id: 's40', job_id: 'j40' })))
        .mockResolvedValueOnce(new Response(null, { status: 404 }))
        .mockResolvedValueOnce(next.response),
    );
    const store = trackedStore({ recoverSession });
    store.send(DRAFT_KEY, { message: 'x', labels });
    await vi.advanceTimersByTimeAsync(1500);
    expect(store.getChat('s40').turn).toEqual(IDLE);

    store.send('s40', { message: 'y', labels });
    await vi.advanceTimersByTimeAsync(3000);
    expect(recoverSession).toHaveBeenCalled();
    expect(store.getChat('s40').turn.phase).toBe('streaming');
  });

  it('stops listening for the network coming back once disposed', () => {
    const win = new EventTarget();
    vi.stubGlobal('window', win);
    const store = trackedStore();
    const listeners = vi.spyOn(win, 'removeEventListener');
    store.dispose();
    expect(listeners).toHaveBeenCalledWith('online', expect.any(Function));
  });
});
