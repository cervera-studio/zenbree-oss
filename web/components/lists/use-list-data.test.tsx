// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import type { List, ListRow } from '@/lib/lists/api';
import { AppError, fail, succeed } from '@/lib/types';
import { type RowsLoader, useListData } from './use-list-data';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/lib/lists/api', () => ({ listsApi: { get } }));
vi.mock('./loading', () => ({ useLoadTimeout: () => false }));
vi.mock('@/lib/lists/fill-stream', () => ({ useFillStream: () => undefined }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const list = { id: 'l1', columns: [], fill_cursor: null } as unknown as List;
const row = (id: string, extra: Partial<ListRow> = {}) =>
  ({ id, excluded: false, ...extra }) as ListRow;
const page = (rows: ListRow[], next_cursor: number | null = null) => succeed({ rows, next_cursor });
const flush = () => act(async () => {});

type Data = ReturnType<typeof useListData>;

function mount(loadRows: RowsLoader, reloadKey = 0) {
  const ref: { current: Data | null } = { current: null };
  function Probe({ k }: { k: number }) {
    ref.current = useListData('l1', loadRows, { reloadKey: k });
    return null;
  }
  const root = createRoot(document.createElement('div'));
  act(() => root.render(<Probe k={reloadKey} />));
  const rerender = (k: number) => act(() => root.render(<Probe k={k} />));
  const data = () => {
    if (!ref.current) throw new Error('not rendered');
    return ref.current;
  };
  return { data, rerender };
}

beforeEach(() => {
  vi.clearAllMocks();
  get.mockResolvedValue(succeed(list));
});

describe('useListData', () => {
  it('loads the list, then the first page, and pages on with the cursor', async () => {
    const loadRows = vi.fn<RowsLoader>(async (_id, cursor) =>
      cursor === 0 ? page([row('a')], 1) : page([row('b')]),
    );
    const { data } = mount(loadRows);
    await flush();
    expect(data().rows.map((r) => r.id)).toEqual(['a']);
    expect(data().cursor).toBe(1);
    await act(async () => void (await data().loadMore()));
    expect(data().rows.map((r) => r.id)).toEqual(['a', 'b']);
    expect(data().cursor).toBeNull();
  });

  it('refetches when the reload key changes', async () => {
    const loadRows = vi.fn<RowsLoader>(async () => page([row('a')]));
    const { rerender } = mount(loadRows);
    await flush();
    await rerender(1);
    await flush();
    expect(loadRows).toHaveBeenCalledTimes(2);
  });

  it('keeps the rows on screen when a refetch fails, but not when the list is gone', async () => {
    const loadRows = vi.fn<RowsLoader>(async () => page([row('a')]));
    const { data, rerender } = mount(loadRows);
    await flush();
    get.mockResolvedValue(fail(new AppError('NET', 'down')));
    await rerender(1);
    await flush();
    expect(data().error).toBeNull();
    expect(data().rows).toHaveLength(1);
    get.mockResolvedValue(fail(new AppError('LIST.NOT_FOUND', 'gone')));
    await rerender(2);
    await flush();
    expect(data().error).toBe('LIST.NOT_FOUND');
  });

  it('ignores a stale save response and keeps the optimistic state through a refetch', async () => {
    const loadRows = vi.fn<RowsLoader>(async () => page([row('a')]));
    const { data, rerender } = mount(loadRows);
    await flush();
    let first: (r: ReturnType<typeof succeed<ListRow>>) => void = () => {};
    const firstRequest = new Promise<ReturnType<typeof succeed<ListRow>>>((res) => {
      first = res;
    });
    let firstSave: Promise<unknown> = Promise.resolve();
    act(() => {
      firstSave = data().save(
        row('a'),
        'rate',
        { optimistic: { rating: 'good' }, revert: {} },
        () => firstRequest,
      );
    });
    await rerender(1);
    await flush();
    expect(data().rows[0]?.rating).toBe('good');
    await act(async () => {
      await data().save(row('a'), 'rate', { optimistic: { rating: 'bad' }, revert: {} }, async () =>
        succeed(row('a', { rating: 'bad' })),
      );
    });
    first(succeed(row('a', { rating: 'good' })));
    await act(async () => void (await firstSave));
    expect(data().rows[0]?.rating).toBe('bad');
  });

  it('reverts a failed save and returns the failure', async () => {
    const loadRows = vi.fn<RowsLoader>(async () => page([row('a')]));
    const { data } = mount(loadRows);
    await flush();
    let res: Awaited<ReturnType<Data['save']>> = null;
    await act(async () => {
      res = await data().save(
        row('a'),
        'exclude',
        { optimistic: { excluded: true }, revert: { excluded: false } },
        async () => fail(new AppError('X', 'no')),
      );
    });
    expect(res).toMatchObject({ success: false });
    expect(data().rows[0]?.excluded).toBe(false);
  });

  it('applies a late success of a save with no optimistic state after a newer one failed', async () => {
    const loadRows = vi.fn<RowsLoader>(async () => page([row('a')]));
    const { data } = mount(loadRows);
    await flush();
    let first: (r: ReturnType<typeof succeed<ListRow>>) => void = () => {};
    const firstRequest = new Promise<ReturnType<typeof succeed<ListRow>>>((res) => {
      first = res;
    });
    let firstSave: Promise<unknown> = Promise.resolve();
    act(() => {
      firstSave = data().save(row('a'), 'rate', { revert: {} }, () => firstRequest);
    });
    await act(async () => {
      await data().save(row('a'), 'rate', { revert: {} }, async () =>
        fail(new AppError('X', 'no')),
      );
    });
    first(succeed(row('a', { rating: 'good' })));
    await act(async () => void (await firstSave));
    expect(data().rows[0]?.rating).toBe('good');
  });

  it('sets the error when the first load fails', async () => {
    get.mockResolvedValue(fail(new AppError('NET', 'down')));
    const { data } = mount(vi.fn<RowsLoader>(async () => page([])));
    await flush();
    expect(data().error).toBe('NET');
  });

  it('drops a late include response once a newer optimistic exclude is pending', async () => {
    const loadRows = vi.fn<RowsLoader>(async () => page([row('a', { excluded: true })]));
    const { data } = mount(loadRows);
    await flush();
    let include: (r: ReturnType<typeof succeed<ListRow>>) => void = () => {};
    const includeRequest = new Promise<ReturnType<typeof succeed<ListRow>>>((res) => {
      include = res;
    });
    let includeSave: Promise<unknown> = Promise.resolve();
    act(() => {
      includeSave = data().save(
        row('a', { excluded: true }),
        'exclude',
        { revert: {} },
        () => includeRequest,
      );
    });
    act(() => {
      void data().save(
        row('a'),
        'exclude',
        { optimistic: { excluded: true }, revert: {} },
        () => new Promise(() => {}),
      );
    });
    include(succeed(row('a', { excluded: false })));
    await act(async () => void (await includeSave));
    expect(data().rows[0]?.excluded).toBe(true);
  });
});
