import { describe, expect, it, vi } from 'vite-plus/test';
import type { Frame } from '@/lib/api/event-stream';
import { createHomeStore } from './store';
import { finishedOf, overviewOf, runOf } from './test-overview';

function harness() {
  let push: (frame: Frame) => void = () => {};
  let signal: AbortSignal | null = null;
  const follow = vi.fn(
    async (_path: string, _cursor: string | null, onFrame: (f: Frame) => void, s: AbortSignal) => {
      push = onFrame;
      signal = s;
    },
  );
  const store = createHomeStore(overviewOf(), { follow, now: () => 0 });
  const frame = (event: string, body: object): Frame => ({
    event,
    id: '',
    data: JSON.stringify(body),
  });
  return { store, follow, push: (f: Frame) => push(f), frame, aborted: () => signal?.aborted };
}

const body = (approvals: number) => ({
  approvals: { total: approvals, campaigns: [] },
  replies: { count: 0, latest: null },
  running: [],
  finished: [],
  lists: [],
  campaigns: [],
  campaign_hint: null,
});

describe('createHomeStore', () => {
  it('opens one stream with the first listener and aborts it with the last', () => {
    const { store, follow, aborted } = harness();
    const a = store.subscribe(() => {});
    const b = store.subscribe(() => {});
    expect(follow).toHaveBeenCalledTimes(1);
    expect(follow.mock.calls[0]?.[0]).toBe('/v1/home/stream');
    a();
    expect(aborted()).toBe(false);
    b();
    expect(aborted()).toBe(true);
  });

  it('reopens after every listener left', () => {
    const { store, follow } = harness();
    store.subscribe(() => {})();
    store.subscribe(() => {});
    expect(follow).toHaveBeenCalledTimes(2);
  });

  it('applies a snapshot silently and an overview with a pop, and notifies', () => {
    const { store, push, frame } = harness();
    const listener = vi.fn();
    store.subscribe(listener);

    push(frame('snapshot', body(5)));
    expect(store.get().overview.approvals.total).toBe(5);
    expect(store.get().pulses).toEqual({});

    push(frame('overview', body(7)));
    expect(store.get().pulses.approvals).toBe(1);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('keeps the state object and stays quiet for an equal frame', () => {
    const { store, push, frame } = harness();
    const listener = vi.fn();
    store.subscribe(listener);
    push(frame('snapshot', body(0)));
    expect(listener).not.toHaveBeenCalled();
  });

  it('drops a frame it cannot read and a frame it does not know', () => {
    const { store, push, frame } = harness();
    const before = store.get();
    store.subscribe(() => {});
    push(frame('overview', { approvals: 'x' }));
    push({ event: 'overview', id: '', data: '{' });
    push(frame('other', body(9)));
    expect(store.get()).toBe(before);
  });

  it('comes back to a remounted Home with nothing left to replay', () => {
    const { store, push, frame } = harness();
    const unsubscribe = store.subscribe(() => {});
    const runs = [runOf({ jobId: 'j1', listId: 'l1' }), runOf({ jobId: 'j2', listId: 'l2' })];
    push(frame('snapshot', { ...body(0), running: runs }));
    push(
      frame('overview', {
        ...body(2),
        finished: runs.map((r) => finishedOf({ jobId: r.jobId, listId: r.listId ?? '' })),
      }),
    );
    store.dispatch({ type: 'held', jobId: 'j1' });
    store.dispatch({ type: 'left', jobId: 'j1' });
    expect(store.get().pulses).toEqual({ approvals: 1 });
    expect(store.get().fresh).toEqual(['l1']);
    expect(store.get().runs.map((r) => r.phase)).toEqual(['done']);
    unsubscribe();
    expect(store.get()).toMatchObject({ pulses: {}, fresh: [], runs: [], announced: [] });
    store.subscribe(() => {});
    expect(store.get()).toMatchObject({ pulses: {}, fresh: [], runs: [], announced: [] });
    expect(store.get().overview.approvals.total).toBe(2);
  });
});
