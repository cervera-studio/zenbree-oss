import { useSyncExternalStore } from 'react';
import { type Project, projectsApi } from './index';

type Loaded = Awaited<ReturnType<typeof projectsApi.list>>;

let leaving = false;
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => (leaving = true));
  // A page restored from the back/forward cache is live again.
  window.addEventListener('pageshow', () => (leaving = false));
}

/** True once the page is unloading: the browser aborts its fetches, which is not a failure. */
export const isLeaving = () => leaving;

// Keyed by the signed-in user: sign-out, sign-in and impersonation are client navigations in
// the same tab, and a list must never outlive the identity it was loaded for.
let cached: { userId: string; load: Promise<Loaded> } | null = null;
// The last list that loaded, for readers that do not load it themselves (the page's client crumb).
let shown: { userId: string; projects: Project[] } | null = null;
const listeners = new Set<() => void>();

function show(next: typeof shown) {
  shown = next;
  for (const listener of listeners) listener();
}

const RETRY_DELAY_MS = 400;

/** Unreachable (503), a server error, or a 401: a restarting web container answers the token
 * request with an error, so the API sees no bearer. Other 4xx repeat their answer. */
const isTransient = (status: number) => status >= 500 || status === 401;

async function fetchOnce(): Promise<Loaded> {
  const first = await projectsApi.list();
  if (first.success || leaving || !isTransient(first.error.status)) return first;
  // A restarting or OOM-killed web container is back within a moment.
  await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
  return projectsApi.list();
}

/** The client list, shared: the sidebar and the phone drawer switchers load it once between them.
 * `fresh` refetches after a create, rename or delete. A failure is not kept. */
export function loadProjects(userId: string, fresh = false): Promise<Loaded> {
  if (fresh || cached?.userId !== userId) {
    const entry = { userId, load: fetchOnce() };
    cached = entry;
    void entry.load.then((res) => {
      if (cached !== entry) return;
      if (res.success) show({ userId, projects: res.data });
      else cached = null;
    });
  }
  return (cached as NonNullable<typeof cached>).load;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** `userId`'s client list, once the switcher has loaded it; never on the server, where nothing loads.
 *  The previous user's list in the tab reads as `null` until the new one arrives. */
export function useLoadedProjects(userId: string | null): Project[] | null {
  return useSyncExternalStore(
    subscribe,
    () => (shown && shown.userId === userId ? shown.projects : null),
    () => null,
  );
}
