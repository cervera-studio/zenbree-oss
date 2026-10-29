import { followStream } from '@/lib/api/event-stream';
import { type HomeEvent, type HomeLive, homeReducer, initHome, rest } from './live';
import { type HomeOverview, parseOverviewFrame } from './overview';

export interface HomeStore {
  subscribe(listener: () => void): () => void;
  get(): HomeLive;
  dispatch(event: Extract<HomeEvent, { type: 'held' | 'left' }>): void;
}

interface Deps {
  follow?: typeof followStream;
  now?: () => number;
  /** Refreshes the app's server state: Home follows a removed member into their remaining
   *  organization (`rejoin` in `followStream`). */
  refresh?: () => void;
}

export function createHomeStore(initial: HomeOverview, deps: Deps = {}): HomeStore {
  const follow = deps.follow ?? followStream;
  const now = deps.now ?? Date.now;
  const listeners = new Set<() => void>();
  let state = initHome(initial);
  let stream: AbortController | null = null;

  const apply = (event: HomeEvent) => {
    const next = homeReducer(state, event);
    if (next === state) return;
    state = next;
    for (const listener of listeners) listener();
  };

  const open = () => {
    const ctrl = new AbortController();
    stream = ctrl;
    void follow(
      '/v1/home/stream',
      null,
      ({ event, data }) => {
        if (ctrl.signal.aborted || (event !== 'snapshot' && event !== 'overview')) return;
        const overview = parseOverviewFrame(data);
        if (overview)
          apply({ type: 'snapshot', overview, silent: event === 'snapshot', at: now() });
      },
      ctrl.signal,
      { rejoin: deps.refresh },
    );
  };

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (!stream) open();
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) {
          stream?.abort();
          stream = null;
          state = rest(state);
        }
      };
    },
    get: () => state,
    dispatch: apply,
  };
}

const stores = new WeakMap<HomeOverview, HomeStore>();

export function homeStoreFor(overview: HomeOverview, refresh: () => void): HomeStore {
  let store = stores.get(overview);
  if (!store) {
    store = createHomeStore(overview, { refresh });
    stores.set(overview, store);
  }
  return store;
}
