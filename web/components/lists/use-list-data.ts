import { startTransition, useCallback, useEffect, useRef, useState } from 'react';
import { type ListDetail, type ListRow, listsApi } from '@/lib/lists/api';
import { useFillStream } from '@/lib/lists/fill-stream';
import type { Result } from '@/lib/types';
import { useLoadTimeout } from './loading';

type RowsPage = { rows: ListRow[]; next_cursor: number | null };

/** Reads the rows from `cursor` on; the list page reads them all, the chat panel one page. */
export type RowsLoader = (listId: string, cursor: number) => Promise<Result<RowsPage>>;

export type MutationKind = 'exclude' | 'rate';

export type SaveChange = { optimistic?: Partial<ListRow>; revert: Partial<ListRow> };

type Saving = { id: string; patch: Partial<ListRow> };

/**
 * The list, its rows and how they stay current: the load (list first, then rows, so the rows are
 * never older than `fill_cursor`), the refetch, the live fill stream and the row saves.
 * `loadRows` must be stable (module scope or `useCallback`). `reloadKey` refetches when it changes, as `refetch()` does.
 */
export function useListData(
  listId: string,
  loadRows: RowsLoader,
  { reloadKey = 0, onFillEnd }: { reloadKey?: number; onFillEnd?: (credits: number) => void } = {},
) {
  const [list, setList] = useState<ListDetail | null>(null);
  const [rows, setRows] = useState<ListRow[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const timedOut = useLoadTimeout(!list && !error, tick);
  const loaded = useRef(false);
  // Newest request per row and kind: an older response must not overwrite a newer click.
  const newest = useRef(new Map<string, { token: number; optimistic: boolean }>());
  // Optimistic state of the saves still in flight, so a refetch does not undo it.
  const saving = useRef(new Map<string, Saving>());

  const refetch = useCallback(() => setTick((n) => n + 1), []);
  const retry = useCallback(() => {
    setError(null);
    refetch();
  }, [refetch]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `reloadKey` and `tick` are refetch triggers
  useEffect(() => {
    let stale = false;
    void (async () => {
      const l = await listsApi.get(listId);
      const r = await loadRows(listId, 0);
      if (stale) return;
      const failed = !l.success ? l.error.code : !r.success ? r.error.code : null;
      // A refetch that fails keeps the rows on screen, unless the list is gone.
      if (failed && (!loaded.current || failed === 'LIST.NOT_FOUND')) return setError(failed);
      if (!l.success || !r.success) return;
      loaded.current = true;
      const pending = [...saving.current.values()];
      // A transition, so React renders a page of rows in slices between frames: the chat panel
      // slides in while its table renders, and one long render would freeze the slide.
      startTransition(() => {
        setError(null);
        setList(l.data);
        setRows(
          pending.reduce(
            (acc, { id, patch }) => acc.map((row) => (row.id === id ? { ...row, ...patch } : row)),
            r.data.rows,
          ),
        );
        setCursor(r.data.next_cursor);
      });
    })();
    return () => {
      stale = true;
    };
  }, [listId, loadRows, reloadKey, tick]);

  useFillStream(list, setList, setRows, onFillEnd, setError);

  const patchRow = useCallback(
    (id: string, patch: Partial<ListRow>) =>
      setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r))),
    [],
  );

  const loadMore = useCallback(async () => {
    if (cursor === null) return null;
    const r = await loadRows(listId, cursor);
    if (!r.success) return r;
    setRows((prev) => [...prev, ...r.data.rows]);
    setCursor(r.data.next_cursor);
    return r;
  }, [cursor, listId, loadRows]);

  /** `kind` names the mutation, so a rate and an exclude of one row do not cancel each other. */
  const save = useCallback(
    async (
      row: ListRow,
      kind: MutationKind,
      change: SaveChange,
      request: () => Promise<Result<Partial<ListRow>>>,
    ) => {
      const key = `${kind}:${row.id}`;
      const token = (newest.current.get(key)?.token ?? 0) + 1;
      newest.current.set(key, { token, optimistic: !!change.optimistic });
      if (change.optimistic) {
        saving.current.set(key, { id: row.id, patch: change.optimistic });
        patchRow(row.id, change.optimistic);
      }
      const res = await request();
      // A save with no optimistic state applies its answer as it comes, unless an optimistic one is
      // pending or this one is: then the newest click wins.
      const latest = newest.current.get(key);
      if (latest && latest.token !== token && (change.optimistic || latest.optimistic)) return null;
      saving.current.delete(key);
      patchRow(row.id, res.success ? res.data : change.revert);
      return res;
    },
    [patchRow],
  );

  return {
    list,
    setList,
    rows,
    cursor,
    error,
    timedOut,
    refetch,
    retry,
    patchRow,
    loadMore,
    save,
  };
}
