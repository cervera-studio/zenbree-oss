/**
 * Every sequence of five user, network and page events against the real `ChatStreamStore`, with a fake
 * server behind `fetch` and `cancelJob`. It checks seven invariants after each step and counts the
 * shortest sequences that break each one. The file runs unchanged on main and on the turn union;
 * only the adapter (the `./chat-turn` import and `live` / `offline`) differs.
 *
 * Limits: each step waits 5 s, so no event lands inside the rejoin backoff, and the 45 s stall
 * timeout never fires. The fixed regression below also covers Stop during a known-job rejoin;
 * longer sequences are outside this exhaustive bound. `answer` and `moreRows` reach the same
 * `send` code, so one send defect counts about three times.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vite-plus/test';

const token = vi.fn();
vi.mock('@/lib/auth-client', () => ({ authClient: { token: () => token() } }));
vi.mock('@/components/ui/sonner', () => ({
  toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}));

import { turnLive, turnOffline } from './chat-turn';
import { ChatStreamStore } from './stream-store';
import type { ChatState, UseChatLabels } from './stream-store-types';

const live = (c: ChatState) => turnLive(c.turn);
const offline = (c: ChatState) => turnOffline(c.turn);

const KEY = 's1';
const STEPS = 5;
// Long enough for the 1 s / 2 s / 4 s rejoin backoff, short enough that five steps stay under
// the 45 s stall timeout.
const STEP_MS = 5000;

const labels = {
  networkError: 'network',
  streamTimeout: 'timeout',
  streamInterrupted: 'interrupted',
  creditsExhausted: 'no credits',
  creditsLow: 'low',
  stopFailed: 'not stopped',
  upgradeCta: 'top up',
  terminals: {},
  errorText: ({ code }) => `error ${code}`,
} satisfies UseChatLabels;

const EVENTS = [
  'send',
  'session',
  'stop',
  'cancelResult',
  'drop',
  'online',
  'done',
  'resumeResult',
  'answer',
  'moreRows',
  'reload',
] as const;
type Event = (typeof EVENTS)[number];

const INVARIANTS = [
  'cancels only the current turn job',
  'no rejoin after a Stop that let go',
  'no send while offline',
  'at most one open stream',
  'an ended turn is not live',
  'Stop in view stops the turn job',
  'Stop asks to stop a running job the client knows',
] as const;
type Invariant = (typeof INVARIANTS)[number];

interface Stream {
  kind: 'send' | 'reattach';
  job: string | null;
  state: 'pending' | 'open' | 'closed';
  sawSession: boolean;
  feed?: ReadableStreamDefaultController<Uint8Array>;
  settle?: (r: Response | Error) => void;
}

/** The saved row of a turn whose job still runs: the user's message has no answer yet. */
const QUESTION = {
  id: 'u',
  sessionId: KEY,
  role: 'user' as const,
  content: 'm',
  toolCalls: null,
  creditsUsed: 0,
  createdAt: '2026-10-19T10:00:00Z',
  sources: null,
  status: null,
  terminalCode: null,
  question: null,
};

const encoder = new TextEncoder();
const frame = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** The server, the network and the browser around one chat. */
class World {
  readonly streams: Stream[] = [];
  readonly cancels: { job: string; answer: (status: string | null) => void }[] = [];
  readonly broken = new Set<Invariant>();
  readonly win = new EventTarget();
  store: ChatStreamStore;
  private posts = 0;
  private sendRequests = 0;
  private serverJob: string | null = null;
  private readonly finished = new Set<string>();
  private networkDown = false;
  private aborts = 0;
  private letGo = false;
  private sendCheck: { wasOffline: boolean; requests: number } | null = null;
  /** Jobs this page learned, from a `session` frame or a loaded chat. */
  private known = new Set<string>();
  private readonly cancelAsked = new Set<string>();
  /** «Ferma» kept the stream open with no job to stop yet: the job the stream names next is owed. */
  private stopWaits = false;

  constructor() {
    vi.stubGlobal('window', this.win);
    vi.stubGlobal('fetch', (url: string, init: RequestInit) => this.fetch(url, init));
    this.store = this.page();
    this.store.loadSession(KEY, KEY, []);
  }

  private page(): ChatStreamStore {
    return new ChatStreamStore({
      cancelJob: (job) => {
        if (job !== this.serverJob) this.broken.add('cancels only the current turn job');
        this.cancelAsked.add(job);
        return new Promise((answer) => this.cancels.push({ job, answer }));
      },
      logger: { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} },
      recoverSession: async () =>
        this.networkDown
          ? null
          : { messages: [], lists: {}, jobId: this.serverJob, title: null, progress: null },
    });
  }

  private running(): string | null {
    return this.serverJob && !this.finished.has(this.serverJob) ? this.serverJob : null;
  }

  private chat(): ChatState {
    return this.store.getChat(KEY);
  }

  private fetch(url: string, init: RequestInit): Promise<Response> {
    const kind = url.endsWith('/stream') ? 'reattach' : 'send';
    if (kind === 'send') {
      this.letGo = false;
      this.stopWaits = false;
      this.sendRequests += 1;
    } else if (this.letGo) this.broken.add('no rejoin after a Stop that let go');
    if (this.networkDown) return Promise.reject(new TypeError('offline'));
    if (kind === 'send') {
      this.posts += 1;
      this.serverJob = `j${this.posts}`;
    }
    const stream: Stream = { kind, job: this.serverJob, state: 'pending', sawSession: false };
    this.streams.push(stream);
    init.signal?.addEventListener('abort', () => {
      this.aborts += 1;
      this.cut(stream, new DOMException('aborted', 'AbortError'));
    });
    const response = new Promise<Response>((resolve, reject) => {
      stream.settle = (r) => (r instanceof Error ? reject(r) : resolve(r));
    });
    if (kind === 'send') this.accept(stream);
    return response;
  }

  private accept(stream: Stream): void {
    const body = new ReadableStream<Uint8Array>({
      start: (c) => {
        stream.feed = c;
      },
      cancel: () => {
        stream.state = 'closed';
      },
    });
    stream.state = 'open';
    stream.settle?.(new Response(body, { status: 200 }));
    // The rejoin endpoint always opens with the `session` frame (api/app/agent/routes.py).
    if (stream.kind === 'reattach') this.session(stream);
  }

  private cut(stream: Stream, error: Error): void {
    if (stream.state === 'pending') stream.settle?.(error);
    if (stream.state === 'open') stream.feed?.error(error);
    stream.state = 'closed';
  }

  private session(stream: Stream): void {
    stream.feed?.enqueue(encoder.encode(frame('session', { session_id: KEY, job_id: stream.job })));
    stream.sawSession = true;
    if (stream.job) this.known.add(stream.job);
  }

  private send(extra: { pick?: number }): void {
    this.sendCheck = { wasOffline: offline(this.chat()), requests: this.sendRequests };
    this.store.send(KEY, { message: 'm', labels, ...extra });
  }

  private readonly handlers: Record<Event, () => void> = {
    send: () => this.send({}),
    answer: () => this.send({ pick: 0 }),
    moreRows: () => this.send({}),
    session: () => {
      const stream = this.streams.findLast((s) => s.state === 'open' && !s.sawSession);
      if (stream) this.session(stream);
    },
    // «Ferma» shows only while the turn is live. In view (no banner), it must stop a running job:
    // at once when the page knows it, or once the stream names it.
    stop: () => {
      if (!live(this.chat())) return;
      const job = this.running();
      const inView = job !== null && !offline(this.chat()) && !this.cancelAsked.has(job);
      const before = this.aborts;
      this.store.stop(KEY, labels);
      const aborted = this.aborts > before;
      if (aborted) this.letGo = true;
      if (job && this.known.has(job) && !this.cancelAsked.has(job))
        this.broken.add('Stop asks to stop a running job the client knows');
      if (!inView || this.cancelAsked.has(job)) return;
      if (aborted || this.known.has(job)) this.broken.add('Stop in view stops the turn job');
      else this.stopWaits = true;
    },
    // The page reloads: the browser closes its streams, and the new page loads the saved chat
    // and rejoins a job that still runs, as `ChatApp` does. Offline, the load fails.
    reload: () => {
      for (const s of this.streams) this.cut(s, new DOMException('page closed', 'AbortError'));
      const closed = this.store;
      closed.dispose();
      // A closed page runs nothing: its late stream callbacks must not rejoin.
      Object.assign(closed, { resume: () => {} });
      this.store = this.page();
      this.known = new Set();
      this.letGo = false;
      this.stopWaits = false;
      if (this.networkDown) return;
      const job = this.running();
      this.store.loadSession(KEY, KEY, job ? [QUESTION] : [], { jobId: this.serverJob });
      if (this.serverJob) this.known.add(this.serverJob);
      this.store.reattachUnanswered(KEY, labels);
    },
    cancelResult: () => {
      const cancel = this.cancels.shift();
      if (!cancel) return;
      const running = cancel.job === this.serverJob && !this.finished.has(cancel.job);
      cancel.answer(running ? 'cancelling' : 'done');
    },
    drop: () => {
      this.networkDown = true;
      for (const s of this.streams) this.cut(s, new TypeError('network'));
    },
    online: () => {
      this.networkDown = false;
      this.win.dispatchEvent(new Event('online'));
    },
    done: () => {
      const stream = this.streams.findLast((s) => s.state === 'open');
      if (!stream?.job) return;
      if (!stream.sawSession) this.session(stream);
      stream.feed?.enqueue(
        encoder.encode(
          frame('message_saved', { message_id: `m-${stream.job}`, status: 'success' }) +
            frame('done', {}),
        ),
      );
      stream.feed?.close();
      stream.state = 'closed';
      this.finished.add(stream.job);
    },
    resumeResult: () => {
      const stream = this.streams.find((s) => s.kind === 'reattach' && s.state === 'pending');
      if (!stream) return;
      if (!stream.job || this.finished.has(stream.job)) {
        stream.state = 'closed';
        stream.settle?.(new Response(null, { status: 404 }));
      } else {
        this.accept(stream);
      }
    },
  };

  async step(event: Event): Promise<void> {
    this.handlers[event]();
    await vi.advanceTimersByTimeAsync(STEP_MS);
    if (this.sendCheck?.wasOffline && this.sendRequests > this.sendCheck.requests)
      this.broken.add('no send while offline');
    this.sendCheck = null;
    const job = this.running();
    if (this.stopWaits && job && this.known.has(job) && !this.cancelAsked.has(job))
      this.broken.add('Stop in view stops the turn job');
    const openStreams = this.streams.filter((s) => s.state !== 'closed').length;
    if (openStreams > 1) this.broken.add('at most one open stream');
    if (openStreams === 0 && live(this.chat())) this.broken.add('an ended turn is not live');
  }
}

function* sequences(length: number): Generator<Event[]> {
  if (length === 0) {
    yield [];
    return;
  }
  for (const head of sequences(length - 1)) for (const e of EVENTS) yield [...head, e];
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
  vi.stubGlobal('cancelAnimationFrame', (h: ReturnType<typeof setTimeout>) => clearTimeout(h));
  token.mockResolvedValue({ data: { token: 'jwt' } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('keeps every invariant over every sequence of five events', { timeout: 600_000 }, async () => {
  // The shortest prefix that breaks each invariant: a longer sequence that only extends it is
  // not counted again.
  const shortest = new Map<Invariant, Set<string>>(INVARIANTS.map((i) => [i, new Set()]));
  for (const seq of sequences(STEPS)) {
    const world = new World();
    const seen = new Set<Invariant>();
    for (const [i, event] of seq.entries()) {
      await world.step(event);
      for (const broken of world.broken) {
        if (seen.has(broken)) continue;
        seen.add(broken);
        shortest.get(broken)?.add(seq.slice(0, i + 1).join(' → '));
      }
    }
    world.store.dispose();
    vi.clearAllTimers();
  }
  // Each broken invariant with its count and its shortest example; the diff shows them on failure.
  const broken = Object.fromEntries(
    INVARIANTS.flatMap((i) => {
      const found = [...(shortest.get(i) ?? [])].sort((a, b) => a.length - b.length);
      return found.length ? [[i, `${found.length}, e.g. ${found[0]}`]] : [];
    }),
  );
  expect(broken).toEqual({});
});

it('stops the known job while its rejoin is pending', async () => {
  const world = new World();
  for (const event of ['send', 'session', 'drop', 'online', 'stop'] as const)
    await world.step(event);
  expect(world.cancels.map(({ job }) => job)).toEqual(['j1']);
  expect([...world.broken]).toEqual([]);
  world.store.dispose();
});

it.each([false, true])(
  'keeps Stop before the session frame across a drop (previous turn: %s)',
  async (previous) => {
    const world = new World();
    if (previous) for (const event of ['send', 'done'] as const) await world.step(event);
    for (const event of ['send', 'stop', 'drop', 'online', 'resumeResult'] as const)
      await world.step(event);
    expect(world.cancels.map(({ job }) => job)).toEqual([previous ? 'j2' : 'j1']);
    expect([...world.broken]).toEqual([]);
    world.store.dispose();
  },
);
