import { describe, expect, it } from 'vite-plus/test';
import type { List, ListRow } from './api';
import { isFilling } from './columns';
import { applyFillToList, applyFillToRows, endedFillCredits } from './fill-stream';

const list: List = {
  id: 'l1',
  name: 'Hospitals',
  entity: 'company',
  mode: 'build',
  chat_session_id: null,
  project_id: 'p1',
  can_expand: false,
  findable: null,
  created_at: '2026-10-05T10:00:00Z',
  row_count: 2,
  status: 'ready',
  delivered_count: 2,
  target_rows: null,
  credits_used: 0,
  brief: null,
  chat_title: null,
  fill_cursor: '1-0',
  columns: [
    { key: 'name', label: 'Name', kind: 'static', position: 1 },
    {
      key: 'ai_h',
      label: 'H',
      kind: 'ai',
      position: 2,
      config: { prompt: 'Hospitals?', fill: { status: 'queued' } },
    },
  ],
};

const row = (id: string): ListRow => ({
  id,
  seq: 1,
  cells: { name: id },
  cell_meta: { name: { status: 'filled', provider: 'apollo' } },
  relevance_score: null,
  excluded: false,
  lookups: { email: { state: 'not_searched' }, phone: { state: 'not_searched' } },
});

describe('applyFillToList', () => {
  it('replaces the column fill state and keeps its prompt', () => {
    const running = applyFillToList(list, {
      type: 'fill',
      key: 'ai_h',
      fill: { status: 'running' },
    });
    expect(running.columns?.[1]?.config).toEqual({
      prompt: 'Hospitals?',
      fill: { status: 'running' },
    });
    expect(running.columns?.[0]).toBe(list.columns?.[0]);
    expect(isFilling(running.columns ?? [])).toBe(true);
    const done = applyFillToList(running, { type: 'fill', key: 'ai_h', fill: { status: 'done' } });
    expect(isFilling(done.columns ?? [])).toBe(false); // the stream closes
    expect(done.fill_cursor).toBe('1-0'); // same cursor: no restart
  });

  it('ignores row frames', () => {
    const ev = { type: 'row', row_id: 'a', cells: {}, cell_meta: {} } as const;
    expect(applyFillToList(list, ev)).toBe(list);
  });
});

describe('applyFillToRows', () => {
  const rows = [row('a'), row('b')];

  it('merges the written cell into its row', () => {
    const next = applyFillToRows(rows, {
      type: 'row',
      row_id: 'a',
      cells: { ai_h: 'Yes' },
      cell_meta: { ai_h: { status: 'filled', source: { url: 'https://a.test', title: 'A' } } },
    });
    expect(next[0]?.cells).toEqual({ name: 'a', ai_h: 'Yes' });
    expect(Object.keys(next[0]?.cell_meta ?? {})).toEqual(['name', 'ai_h']);
    expect(next[1]).toBe(rows[1]);
  });

  it('records an empty cell without a value, and replaying a frame changes nothing', () => {
    const ev = {
      type: 'row',
      row_id: 'b',
      cells: {},
      cell_meta: { ai_h: { status: 'empty', source: null } },
    } as const;
    const once = applyFillToRows(rows, ev);
    expect(once[1]?.cells).toEqual({ name: 'b' });
    expect(once[1]?.cell_meta?.ai_h?.status).toBe('empty');
    expect(applyFillToRows(once, ev)).toEqual(once);
  });

  it('skips a row that is not loaded and fill frames', () => {
    expect(
      applyFillToRows(rows, { type: 'row', row_id: 'z', cells: { ai_h: 'x' }, cell_meta: {} }),
    ).toBe(rows);
    expect(applyFillToRows(rows, { type: 'fill', key: 'ai_h', fill: { status: 'done' } })).toBe(
      rows,
    );
  });
});

describe('endedFillCredits', () => {
  const fill = (status: string, started_at: string, credits = 5) =>
    ({ type: 'fill', key: 'ai_h', fill: { status, started_at, credits } }) as const;

  it('counts a run once, even when a restarted stream replays its last frame', () => {
    const seen = new Set<string>();
    expect(endedFillCredits(seen, fill('running', 't1'))).toBeNull();
    expect(endedFillCredits(seen, fill('done', 't1'))).toBe(5);
    expect(endedFillCredits(seen, fill('done', 't1'))).toBeNull();
    expect(endedFillCredits(seen, fill('failed', 't2', 3))).toBe(3); // a refill is a new run
  });
});
