/** Follow an API SSE stream (#242, #252): fetch + reader (it needs the auth header), `Last-Event-ID`
 *  resume, backoff and a stall watchdog. Each feature maps frames to its own typed events. */
import { authFetch } from '@/lib/api/browser';
import { toAppError } from '@/lib/api/error';

export type Frame = { event: string; id: string; data: string };

const STALL_MS = 45_000; // the API pings every 15 s; silence this long is a wedged transport
const MAX_BACKOFF_MS = 30_000;

/** Complete `event:` / `id:` / `data:` frames off the front of `buffer`, and the unfinished rest. */
export function parseFrames(buffer: string): { frames: Frame[]; rest: string } {
  const blocks = buffer.split('\n\n');
  const rest = blocks.pop() ?? '';
  const frames: Frame[] = [];
  for (const block of blocks) {
    const frame = { event: '', id: '', data: '' };
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) frame.event = line.slice(7);
      else if (line.startsWith('id: ')) frame.id = line.slice(4);
      else if (line.startsWith('data: ')) frame.data = line.slice(6);
    }
    if (frame.event && frame.data) frames.push(frame);
  }
  return { frames, rest };
}

const NOT_A_MEMBER = 'AUTH.NOT_A_MEMBER';

/** The code of an `error` frame, which ends a stream on purpose: the caller left the organization
 *  (`AUTH.NOT_A_MEMBER`, on every stream) or what it follows is gone. Show it with `useErrorText()`. */
export function errorFrameCode({ data }: Frame): string {
  let body: unknown = data;
  try {
    body = JSON.parse(data);
  } catch {
    // not JSON: toAppError gives a code of its own
  }
  return toAppError(500, { detail: body }).code;
}

/** A `: ping` completes no frame, so its batch is empty. */
export async function* readFrames(body: NonNullable<Response['body']>): AsyncGenerator<Frame[]> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    const parsed = parseFrames(buffer + value);
    buffer = parsed.rest;
    yield parsed.frames;
  }
}

export function backoff(attempt: number): number {
  return Math.min(MAX_BACKOFF_MS, 1000 * 2 ** attempt);
}

const wait = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener('abort', done);
  });

/** One connection from `after`; resolves when it ends. `connected` = the server accepted it;
 *  `status` = its answer, 0 when none came (network, abort). */
async function readOnce(
  path: string,
  after: string | null,
  handle: (frame: Frame) => void,
  signal: AbortSignal,
  onOpen: () => void,
): Promise<{ connected: boolean; status: number }> {
  const ctrl = new AbortController();
  const abort = () => ctrl.abort();
  signal.addEventListener('abort', abort, { once: true });
  let stall: ReturnType<typeof setTimeout> | undefined;
  const arm = () => {
    clearTimeout(stall);
    stall = setTimeout(abort, STALL_MS);
  };
  let connected = false;
  let status = 0;
  try {
    arm();
    const res = await authFetch(path, {
      headers: after ? { 'Last-Event-ID': after } : {},
      signal: ctrl.signal,
    });
    status = res.status;
    if (!res.ok || !res.body) return { connected, status };
    connected = true;
    onOpen();
    for await (const frames of readFrames(res.body)) {
      arm(); // every byte is liveness, `: ping` included
      for (const frame of frames) handle(frame);
    }
    return { connected, status };
  } catch {
    return { connected, status };
  } finally {
    clearTimeout(stall);
    signal.removeEventListener('abort', abort);
  }
}

type Resume = { cursor: string | null } | 'end';

export type FollowOptions = {
  /** Runs after each attempt the server did not accept (down, 5xx, network). */
  onRefused?: () => void;
  /** For a stream that holds none of the user's work (the bell, the sidebar, Home). On an
   *  `AUTH.NOT_A_MEMBER` frame the session already moved to the user's remaining organization
   *  (`afterRemoveMember`): `rejoin` refreshes the app's server state, then the stream reconnects
   *  once with a fresh token, into that organization. A 401 on that reconnect means no organization
   *  is left: the stream ends, and the refreshed page's signed-out or no-organization handling
   *  takes over. The frame never reaches `onFrame`. */
  rejoin?: () => void;
  /** For a stream whose consumer loaded a list before it (the bell): after `rejoin`, loads that
   *  list again in the remaining organization. The reconnect waits for it and resumes from the
   *  cursor it returns, so nothing falls between the load and the stream; `'end'` stops the
   *  stream, as a 401 on the reconnect does. */
  reload?: () => Promise<Resume>;
};

/** Tail `path` from `cursor` until `signal` aborts: reconnects with backoff and `Last-Event-ID`.
 *  An `error` frame ends it for good: the server stopped the stream on purpose (the caller left
 *  the organization, or the list is gone), so a reconnect would only be refused. A stream that
 *  passes `rejoin` follows the user into their remaining organization instead. A consumer that
 *  shows a wait (a spinner, a skeleton) handles the end too, or the wait never ends. */
export async function followStream(
  path: string,
  cursor: string | null,
  onFrame: (frame: Frame) => void,
  signal: AbortSignal,
  { onRefused, rejoin, reload }: FollowOptions = {},
): Promise<void> {
  let last = cursor;
  let attempt = 0;
  let ended = false;
  let rejoining = false;
  // An object, because TypeScript does not see `handle` set it during the loop's `await`.
  const pending: { reload: Promise<Resume> | null } = { reload: null };
  const handle = (frame: Frame) => {
    if (frame.event === 'error' && rejoin && errorFrameCode(frame) === NOT_A_MEMBER) {
      rejoining = true;
      rejoin();
      pending.reload = reload?.() ?? null;
      return;
    }
    ended ||= frame.event === 'error';
    onFrame(frame);
    if (frame.id) last = frame.id; // after handling, so a cut mid-frame asks for it again
  };
  // An accepted reconnect is in the new organization: a later removal from it may rejoin again.
  const opened = () => {
    rejoining = false;
  };
  while (!signal.aborted && !ended) {
    const { connected, status } = await readOnce(path, last, handle, signal, opened);
    if (pending.reload) {
      const resumed = await pending.reload;
      pending.reload = null;
      if (resumed === 'end') ended = true;
      else last = resumed.cursor;
    }
    if (connected) attempt = 0;
    else if (!signal.aborted) onRefused?.();
    // authFetch already retried the 401 once with a fresh token.
    ended ||= rejoining && status === 401;
    if (!signal.aborted && !ended) await wait(backoff(attempt++), signal);
  }
}
