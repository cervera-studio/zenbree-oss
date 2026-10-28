import { useRouter } from 'next/navigation';
import { useCallback, useSyncExternalStore } from 'react';
import { z } from 'zod';
import { followStream } from '@/lib/api/event-stream';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { useCurrentProjectId } from '@/lib/projects';

const sessionSchema = z.looseObject({
  id: z.string(),
  title: z.string().nullable(),
  updatedAt: z.string(),
  pinnedAt: z.string().nullable(),
  status: z.enum(['asking', 'plan', 'running', 'done', 'error']).nullable(),
  progressDone: z.number(),
  progressTotal: z.number(),
  listCount: z.number(),
  people: z.number(),
  companies: z.number(),
});

const navSchema = z.looseObject({
  lists: z.number(),
  campaigns: z.number(),
  approvals: z.number(),
  views: z.number(),
  replies: z.number(),
  sessions: z.array(sessionSchema),
});

export type Nav = z.infer<typeof navSchema>;
export type NavSession = Nav['sessions'][number];

function parseNavFrame(data: string): Nav | null {
  try {
    const parsed = navSchema.safeParse(toCamelCase(JSON.parse(data)));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

/** `failed`: the last attempt to connect was refused, or the stream ended for good, so `nav` may be
 *  missing or stale. */
type NavState = { nav: Nav | null; failed: boolean };
type Store = { state: NavState; listeners: Set<() => void>; stream: AbortController };

const EMPTY: NavState = { nav: null, failed: false };

// One stream per project for the whole tab: the desktop rail and the phone drawer share it.
const stores = new Map<string, Store>();

function subscribe(projectId: string, listener: () => void, refresh: () => void) {
  let store = stores.get(projectId);
  if (!store) {
    const created: Store = { state: EMPTY, listeners: new Set(), stream: new AbortController() };
    const set = (state: NavState) => {
      if (created.stream.signal.aborted) return;
      created.state = state;
      for (const l of created.listeners) l();
    };
    stores.set(projectId, created);
    void followStream(
      '/v1/home/nav/stream',
      null,
      ({ event, data }) => {
        // The stream ended for good: no snapshot will come.
        if (event === 'error') return set({ ...created.state, failed: true });
        if (event !== 'snapshot' && event !== 'nav') return;
        const nav = parseNavFrame(data);
        if (nav) set({ nav, failed: false });
      },
      created.stream.signal,
      {
        onRefused: () => {
          if (!created.state.failed) set({ ...created.state, failed: true });
        },
        rejoin: refresh,
      },
    );
    store = created;
  }
  const own = store;
  own.listeners.add(listener);
  return () => {
    own.listeners.delete(listener);
    if (own.listeners.size > 0) return;
    own.stream.abort();
    stores.delete(projectId);
  };
}

/** The sidebar's counts and chats (`GET /v1/home/nav/stream`), live; `nav` is null until the
 *  first frame. The stream reconnects by itself with backoff, and follows a removed member into
 *  their remaining organization (`rejoin` in `followStream`). */
export function useNavState(): NavState {
  const projectId = useCurrentProjectId();
  const router = useRouter();
  const sub = useCallback(
    (listener: () => void) =>
      projectId ? subscribe(projectId, listener, () => router.refresh()) : () => {},
    [projectId, router],
  );
  return useSyncExternalStore(
    sub,
    () => (projectId ? (stores.get(projectId)?.state ?? EMPTY) : EMPTY),
    () => EMPTY,
  );
}

export function useNav(): Nav | null {
  return useNavState().nav;
}
