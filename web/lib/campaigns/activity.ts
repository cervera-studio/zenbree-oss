import type { Activity, ActivityEvent } from './api';

/** `sent` and `approved` share the send's id: the kind is part of an event's identity. */
export const eventKey = (e: Pick<ActivityEvent, 'kind' | 'id'>) => `${e.kind}:${e.id}`;

const order = (a: ActivityEvent, b: ActivityEvent) =>
  Date.parse(b.at) - Date.parse(a.at) ||
  (b.id > a.id ? 1 : b.id < a.id ? -1 : 0) ||
  (b.kind > a.kind ? 1 : b.kind < a.kind ? -1 : 0);

/**
 * The panel's events: the server's first page, refreshed after every decision, over the older pages
 * loaded since. The pages overlap once new events push old ones down, so each event shows once,
 * newest first, in the API's keyset order (at, id, kind). The first page's copy wins.
 */
export function mergeActivity(...pages: ActivityEvent[][]): ActivityEvent[] {
  const byKey = new Map<string, ActivityEvent>();
  for (const e of pages.flat()) if (!byKey.has(eventKey(e))) byKey.set(eventKey(e), e);
  return [...byKey.values()].sort(order);
}

/**
 * The refreshed first page joins the loaded pages only when no event can hide between them: it is
 * complete, or it shares an event with them. A full page of newer events and no shared one could
 * hide some, so the loaded pages show alone until a reload.
 */
export function panelActivity(first: Activity | null, loaded: ActivityEvent[] | null) {
  const live = first?.items ?? [];
  if (!loaded) return mergeActivity(live);
  const keys = new Set(loaded.map(eventKey));
  const joins = !first?.nextCursor || live.some((e) => keys.has(eventKey(e)));
  return joins ? mergeActivity(live, loaded) : mergeActivity(loaded);
}
