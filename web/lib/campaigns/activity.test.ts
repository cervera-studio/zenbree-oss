import { describe, expect, it } from 'vite-plus/test';
import { mergeActivity, panelActivity } from './activity';
import type { ActivityEvent } from './api';

const ev = (id: string, at: string, kind: ActivityEvent['kind'] = 'sent'): ActivityEvent => ({
  id,
  kind,
  at,
  targetId: null,
  firstName: 'Ada',
  lastName: null,
  companyName: null,
  step: 1,
  channel: 'email',
  label: null,
});
const keys = (list: ActivityEvent[]) => list.map((e) => `${e.kind}:${e.id}`);

describe('mergeActivity', () => {
  const e5 = ev('5', '2026-10-16T10:05:00Z');
  const e4 = ev('4', '2026-10-16T10:04:00Z');
  const e3 = ev('3', '2026-10-16T10:03:00Z');
  const e2 = ev('2', '2026-10-16T10:02:00Z');
  const e1 = ev('1', '2026-10-16T10:01:00Z');

  it('shows overlapping pages once, newest first', () => {
    expect(keys(mergeActivity([e5, e4, e3], [e3, e2, e1]))).toEqual([
      'sent:5',
      'sent:4',
      'sent:3',
      'sent:2',
      'sent:1',
    ]);
  });

  it('keeps no gap when the first page changes after older pages loaded', () => {
    // «Carica altri» stored the first page it saw with the next one.
    const loaded = [e5, e4, e3, e2, e1];
    const n1 = ev('6', '2026-10-16T10:06:00Z', 'approved');
    // A decision refreshed the first page: e3 left it, and is still in the loaded pages.
    expect(keys(mergeActivity([n1, e5, e4], loaded))).toEqual([
      'approved:6',
      'sent:5',
      'sent:4',
      'sent:3',
      'sent:2',
      'sent:1',
    ]);
  });

  it('keeps an approval and the send of the same id apart', () => {
    const approved = ev('7', '2026-10-16T09:00:00Z', 'approved');
    const sent = ev('7', '2026-10-16T09:30:00Z', 'sent');
    expect(keys(mergeActivity([sent], [approved, sent]))).toEqual(['sent:7', 'approved:7']);
  });

  it('orders one timestamp by id, then kind, both descending', () => {
    const at = '2026-10-16T08:00:00Z';
    const list = mergeActivity([
      ev('a', at, 'approved'),
      ev('b', at, 'reply'),
      ev('a', at, 'sent'),
    ]);
    expect(keys(list)).toEqual(['reply:b', 'sent:a', 'approved:a']);
  });

  it('compares instants, not strings of different precision', () => {
    const late = ev('x', '2026-10-16T08:00:00.5Z');
    const early = ev('y', '2026-10-16T08:00:00Z');
    expect(keys(mergeActivity([early, late]))).toEqual(['sent:x', 'sent:y']);
  });
});

describe('panelActivity', () => {
  const e3 = ev('3', '2026-10-16T10:03:00Z');
  const e2 = ev('2', '2026-10-16T10:02:00Z');
  const e1 = ev('1', '2026-10-16T10:01:00Z');
  const n5 = ev('5', '2026-10-16T10:05:00Z', 'reply');
  const n4 = ev('4', '2026-10-16T10:04:00Z', 'reply');

  it('shows the first page alone before «Carica altri»', () => {
    expect(keys(panelActivity({ items: [e3, e2], nextCursor: 'c' }, null))).toEqual([
      'sent:3',
      'sent:2',
    ]);
  });

  it('joins a refreshed first page that shares an event with the loaded pages', () => {
    const first = { items: [n4, e3], nextCursor: 'c' };
    expect(keys(panelActivity(first, [e3, e2, e1]))).toEqual([
      'reply:4',
      'sent:3',
      'sent:2',
      'sent:1',
    ]);
  });

  it('keeps out a full first page of newer events that shares none, which could hide one', () => {
    const first = { items: [n5, n4], nextCursor: 'c' };
    expect(keys(panelActivity(first, [e3, e2, e1]))).toEqual(['sent:3', 'sent:2', 'sent:1']);
  });

  it('joins a complete first page even when it shares no event', () => {
    const first = { items: [n5, n4], nextCursor: null };
    expect(keys(panelActivity(first, [e3, e2, e1]))).toEqual([
      'reply:5',
      'reply:4',
      'sent:3',
      'sent:2',
      'sent:1',
    ]);
  });
});

describe('«Carica altri» after a first page was kept out', () => {
  const e3 = ev('3', '2026-10-16T10:03:00Z');
  const e2 = ev('2', '2026-10-16T10:02:00Z');
  const e1 = ev('1', '2026-10-16T10:01:00Z');
  const e0 = ev('0', '2026-10-16T10:00:00Z');
  const n5 = ev('5', '2026-10-16T10:05:00Z', 'reply');
  const n4 = ev('4', '2026-10-16T10:04:00Z', 'reply');

  it('keeps that first page out, so no event hides above the loaded ones', () => {
    const first = { items: [n5, n4], nextCursor: 'c' };
    // The panel loads the next page onto the events it shows.
    const loaded = mergeActivity(panelActivity(first, [e3, e2, e1]), [e0]);
    expect(keys(panelActivity(first, loaded))).toEqual(['sent:3', 'sent:2', 'sent:1', 'sent:0']);
  });
});
