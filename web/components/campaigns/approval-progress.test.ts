import { describe, expect, it } from 'vite-plus/test';
import { afterDecision, afterUndo, reviewQueue, withIds } from './approval-progress';

const items = (...ids: string[]) => ids.map((id) => ({ id }));
const ids = (n: number, from = 1) => Array.from({ length: n }, (_, i) => `m${from + i}`);
const view = (list: { id: string }[], decided: ReadonlySet<string>) => {
  const q = reviewQueue(list, decided);
  return { current: q.current?.id, next: q.next.length, reviewed: q.reviewed, total: q.total };
};

describe('reviewQueue', () => {
  it('counts the decided ones against the queue', () => {
    const list = items('a', 'b', 'c');
    expect(view(list, new Set())).toEqual({ current: 'a', next: 2, reviewed: 0, total: 3 });
    // Decided, the server has not refreshed yet: it leaves the screen at once.
    expect(view(list, new Set(['a']))).toEqual({ current: 'b', next: 1, reviewed: 1, total: 3 });
    // Refreshed: the same count.
    expect(view(items('b', 'c'), new Set(['a']))).toEqual({
      current: 'b',
      next: 1,
      reviewed: 1,
      total: 3,
    });
    expect(view(items(), new Set(['a', 'b', 'c']))).toEqual({
      current: undefined,
      next: 0,
      reviewed: 3,
      total: 3,
    });
  });

  it('grows the total with arrivals mid-review', () => {
    // 2 reviewed of 12, then 3 arrive at the end of the queue.
    const decided = new Set(['m1', 'm2']);
    const refreshed = items(...ids(10, 3), 'n1', 'n2', 'n3');
    expect(view(refreshed, decided)).toEqual({ current: 'm3', next: 12, reviewed: 2, total: 15 });
  });

  it('shrinks the total when a message is decided elsewhere', () => {
    expect(view(items('b', 'c', 'd'), new Set(['a']))).toEqual({
      current: 'b',
      next: 2,
      reviewed: 1,
      total: 4,
    });
    expect(view(items('b', 'd'), new Set(['a']))).toEqual({
      current: 'b',
      next: 1,
      reviewed: 1,
      total: 3,
    });
  });
});

describe('afterDecision and afterUndo', () => {
  it('drops ids the API did not apply, so the next one shows', () => {
    const decided = afterDecision(withIds(new Set(['a']), ['b']), ['b'], 0);
    expect([...decided]).toEqual(['a']);
    // The refresh has not removed b: it is still on screen, as decided elsewhere it soon goes.
    expect(view(items('b', 'c'), decided)).toEqual({
      current: 'b',
      next: 1,
      reviewed: 1,
      total: 3,
    });
  });

  it('keeps ids the API applied, also part of a batch', () => {
    expect([...afterDecision(new Set(['a', 'b', 'c']), ['a', 'b', 'c'], 2)]).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('puts a restored id back at its place in the queue', () => {
    const decided = afterUndo(new Set(['a', 'b']), ['b'], 1);
    expect([...decided]).toEqual(['a']);
    // The server lists it again in created order.
    expect(view(items('b', 'c'), decided)).toEqual({
      current: 'b',
      next: 1,
      reviewed: 1,
      total: 3,
    });
  });

  it('keeps an id reviewed when the undo came too late', () => {
    expect([...afterUndo(new Set(['a', 'b']), ['b'], 0)]).toEqual(['a', 'b']);
  });
});
