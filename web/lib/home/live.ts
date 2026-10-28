import type { FinishedSearch, HomeOverview, RunningSearch } from './overview';

type Intent = NonNullable<HomeOverview['replies']['latest']>['intent'];

type PulseKey = 'approvals' | 'replies' | `campaign:${string}`;

export type Announcement =
  | { kind: 'reply'; name: string | null; intent: Intent; count: number }
  | { kind: 'approvals'; added: number; total: number }
  | { kind: 'finished'; title: string | null; entity: FinishedSearch['entity']; rows: number };

export type Announced = Announcement & { id: number };

export type RunRow =
  | { phase: 'running'; jobId: string; run: RunningSearch }
  | { phase: 'done' | 'leaving'; jobId: string; done: FinishedSearch; at: number };

export interface HomeLive {
  overview: HomeOverview;
  runs: RunRow[];
  fresh: readonly string[];
  pulses: Readonly<Partial<Record<PulseKey, number>>>;
  announced: readonly Announced[];
}

export type HomeEvent =
  | { type: 'snapshot'; overview: HomeOverview; silent: boolean; at: number }
  | { type: 'held'; jobId: string }
  | { type: 'left'; jobId: string };

export const DONE_ROW_MAX_MS = 3_000;

const LEAVE_ANIMATION = 'home-leave';

const MAX_LISTS = 5;

const MAX_ANNOUNCED = 3;

export function initHome(overview: HomeOverview): HomeLive {
  return {
    overview,
    runs: overview.running.map((run) => ({ phase: 'running', jobId: run.jobId, run })),
    fresh: [],
    pulses: {},
    announced: [],
  };
}

export function rest(state: HomeLive): HomeLive {
  return {
    ...state,
    runs: state.runs.filter((r) => r.phase === 'running'),
    fresh: [],
    pulses: {},
    announced: [],
  };
}

export function phaseEvent(
  jobId: string,
  kind: 'start' | 'end',
  animationName: string,
  onRow: boolean,
): HomeEvent | null {
  if (!onRow || animationName !== LEAVE_ANIMATION) return null;
  return { type: kind === 'start' ? 'held' : 'left', jobId };
}

export function visibleLists(state: HomeLive): HomeOverview['lists'] {
  const hidden = new Set<string>();
  for (const row of state.runs) {
    const run = row.phase === 'running' ? row.run : row.done;
    if (run.newList && run.listId) hidden.add(run.listId);
  }
  return state.overview.lists.filter((l) => !hidden.has(l.id)).slice(0, MAX_LISTS);
}

function sameBody(a: HomeOverview, b: HomeOverview): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

function risen(prev: HomeOverview, next: HomeOverview) {
  const keys: PulseKey[] = [];
  const items: Announcement[] = [];
  if (next.approvals.total > prev.approvals.total) {
    keys.push('approvals');
    items.push({
      kind: 'approvals',
      added: next.approvals.total - prev.approvals.total,
      total: next.approvals.total,
    });
  }
  if (next.replies.count > prev.replies.count) {
    keys.push('replies');
    const { latest } = next.replies;
    items.push({
      kind: 'reply',
      name: latest?.name ?? null,
      intent: latest?.intent ?? null,
      count: next.replies.count,
    });
  }
  const before = new Map(prev.campaigns.map((c) => [c.id, c.replies]));
  for (const c of next.campaigns) {
    if (c.replies > (before.get(c.id) ?? c.replies)) keys.push(`campaign:${c.id}`);
  }
  return { keys, items };
}

function announce(state: HomeLive, items: Announcement[]): readonly Announced[] {
  if (items.length === 0) return state.announced;
  const last = state.announced.at(-1)?.id ?? 0;
  const added = items.map((a, i) => ({ ...a, id: last + i + 1 }));
  return [...state.announced, ...added].slice(-MAX_ANNOUNCED);
}

function nextRuns(state: HomeLive, next: HomeOverview, silent: boolean, at: number): RunRow[] {
  const running = new Map(next.running.map((run) => [run.jobId, run]));
  const finished = new Map(next.finished.map((f) => [f.jobId, f]));
  const kept: RunRow[] = [];
  for (const row of state.runs) {
    if (row.phase !== 'running') {
      kept.push(row);
      continue;
    }
    const run = running.get(row.jobId);
    const done = finished.get(row.jobId);
    if (run) kept.push({ phase: 'running', jobId: row.jobId, run });
    else if (done && !silent) kept.push({ phase: 'done', jobId: row.jobId, done, at });
  }
  const known = new Set(kept.map((r) => r.jobId));
  for (const run of next.running) {
    if (!known.has(run.jobId)) kept.push({ phase: 'running', jobId: run.jobId, run });
  }
  return kept;
}

function leave(state: HomeLive, jobId: string): HomeLive {
  const row = state.runs.find((r) => r.jobId === jobId);
  if (!row || row.phase === 'running') return state;
  const { done } = row;
  const fresh =
    done.newList && !state.fresh.includes(done.listId)
      ? [...state.fresh, done.listId]
      : state.fresh;
  return {
    ...state,
    runs: state.runs.filter((r) => r.jobId !== jobId),
    fresh,
    announced: announce(state, [
      { kind: 'finished', title: done.title, entity: done.entity, rows: done.rows },
    ]),
  };
}

export function homeReducer(state: HomeLive, event: HomeEvent): HomeLive {
  if (event.type === 'left') return leave(state, event.jobId);
  if (event.type === 'held') {
    const row = state.runs.find((r) => r.jobId === event.jobId);
    if (row?.phase !== 'done') return state;
    return {
      ...state,
      runs: state.runs.map((r) => (r === row ? { ...row, phase: 'leaving' } : r)),
    };
  }

  const { overview, silent, at } = event;
  const stale = state.runs.filter((r) => r.phase !== 'running' && at - r.at > DONE_ROW_MAX_MS);
  if (stale.length === 0 && sameBody(state.overview, overview)) return state;

  let next: HomeLive = { ...state, overview, runs: nextRuns(state, overview, silent, at) };
  if (!silent) {
    const { keys, items } = risen(state.overview, overview);
    const pulses = { ...state.pulses };
    for (const key of keys) pulses[key] = (pulses[key] ?? 0) + 1;
    next = { ...next, pulses, announced: announce(state, items) };
  }
  for (const row of stale) next = leave(next, row.jobId);
  return next;
}
