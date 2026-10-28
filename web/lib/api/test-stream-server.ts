import { vi } from 'vite-plus/test';

/** One answer of the fake API: a status with no body, a JSON body on a 200, or SSE frames on a
 *  200 that stays open (until the client aborts) or closes after them. */
type Answer = { status: number } | { json: unknown } | { frames: string; open?: boolean };

export const NOT_A_MEMBER_FRAME =
  'event: error\ndata: {"message":"You are no longer a member of this organization.","code":"AUTH.NOT_A_MEMBER"}\n\n';

function respond(answer: Answer, signal: AbortSignal | null | undefined): Response {
  if ('status' in answer) return new Response(null, { status: answer.status });
  if ('json' in answer) return Response.json(answer.json);
  const bytes = new TextEncoder().encode(answer.frames);
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(bytes);
      if (!answer.open) c.close();
      else
        signal?.addEventListener('abort', () => c.error(new DOMException('aborted', 'AbortError')));
    },
  });
  return new Response(body);
}

/** The browser's `fetch` for the stream tests, behind the real `authFetch`: each call takes the
 *  next answer; with none left it waits for the client to abort, as a server that never answers. */
export function streamServer(...answers: Answer[]) {
  return routeServer({ '': answers });
}

/** `streamServer` with one queue of answers per path: a call takes the next answer of the longest
 *  key its URL contains, so a list load and its stream each get their own. */
export function routeServer(routes: Record<string, Answer[]>) {
  const queues = Object.entries(routes)
    .map(([key, answers]) => ({ key, answers: [...answers] }))
    .sort((a, b) => b.key.length - a.key.length);
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const next = queues.find((q) => url.includes(q.key))?.answers.shift();
    if (next) return respond(next, init?.signal);
    return new Promise<Response>((_, reject) =>
      init?.signal?.addEventListener('abort', () =>
        reject(new DOMException('aborted', 'AbortError')),
      ),
    );
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
