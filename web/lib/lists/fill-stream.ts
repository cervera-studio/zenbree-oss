/** Live AI column fills (#252): `GET /v1/lists/{id}/stream`, frames in docs/contracts-m1.md. */
import { type Dispatch, type SetStateAction, useEffect, useRef } from 'react';
import { errorFrameCode, type Frame, followStream } from '@/lib/api/event-stream';
import type { List, ListColumn, ListRow } from './api';
import { isFilling } from './columns';

const FILL_EVENT_TYPES = ['fill', 'row'] as const;
type FillEventType = (typeof FILL_EVENT_TYPES)[number];
type Fill = NonNullable<NonNullable<ListColumn['config']>['fill']>;

// Snake_case like the rest of the lists wire shape (see api.ts).
export type FillEvent =
  | { type: 'fill'; key: string; fill: Fill }
  | {
      type: 'row';
      row_id: string;
      cells: ListRow['cells'];
      cell_meta: NonNullable<ListRow['cell_meta']>;
    };

/** A `fill` frame replaces that column's fill state; anything else leaves the list alone. */
export function applyFillToList<L extends List>(list: L, ev: FillEvent): L {
  if (ev.type !== 'fill' || !list.columns) return list;
  const columns = list.columns.map((c) =>
    c.key === ev.key ? { ...c, config: { ...c.config, fill: ev.fill } } : c,
  );
  return { ...list, columns };
}

/** A `row` frame merges the cell it wrote, as the API did. A row not loaded (a later page) is skipped. */
export function applyFillToRows(rows: ListRow[], ev: FillEvent): ListRow[] {
  if (ev.type !== 'row' || !rows.some((r) => r.id === ev.row_id)) return rows;
  return rows.map((r) =>
    r.id === ev.row_id
      ? { ...r, cells: { ...r.cells, ...ev.cells }, cell_meta: { ...r.cell_meta, ...ev.cell_meta } }
      : r,
  );
}

/**
 * What a run charged, on its last `fill` frame (done or failed), once per run: a refetch mid-fill
 * restarts the stream from an older cursor, so the same frame can arrive twice. `seen` holds the runs
 * counted, by column key and `started_at`.
 */
export function endedFillCredits(seen: Set<string>, ev: FillEvent): number | null {
  if (ev.type !== 'fill' || ev.fill.status === 'queued' || ev.fill.status === 'running')
    return null;
  const run = `${ev.key}:${String(ev.fill.started_at ?? '')}`;
  if (seen.has(run)) return null;
  seen.add(run);
  return typeof ev.fill.credits === 'number' ? ev.fill.credits : 0;
}

function isEventType(value: string): value is FillEventType {
  return FILL_EVENT_TYPES.some((t) => t === value);
}

export function toEvent({ event, data }: Frame): FillEvent | null {
  if (!isEventType(event)) return null;
  try {
    return { type: event, ...JSON.parse(data) } as FillEvent;
  } catch {
    return null;
  }
}

/**
 * While a column of the loaded `list` fills, follow its stream from the load's `fill_cursor` and
 * apply each frame; the stream closes once no column is queued or running. A reload brings a new
 * cursor and restarts it. The rows must be read after the list, so they are never older than the cursor.
 */
// ponytail: a fill that never ends sends no last `fill` frame: a worker killed outright (SIGKILL/OOM)
// mid-run, or a task never claimed (lost by the broker, stuck `queued`). The stream then stays open
// until the view closes; a reload after STALE_FILL_MINUTES shows `fill_stale`. Add a server-side
// timeout (end the SSE once the fill is stale) if it bites.
export function useFillStream<L extends List>(
  list: L | null,
  setList: Dispatch<SetStateAction<L | null>>,
  setRows: Dispatch<SetStateAction<ListRow[]>>,
  /** Once per run: its last `fill` frame (done or failed) carries what it charged. */
  onFillEnd?: (credits: number) => void,
  /** The stream ended on purpose: the caller left the organization, or the list is gone. The
   *  columns would otherwise show their fill running for good. */
  onStreamError?: (code: string) => void,
) {
  const listId = list?.id;
  const cursor = list?.fill_cursor ?? null;
  const filling = isFilling(list?.columns ?? []);
  const ended = useRef(new Set<string>());
  useEffect(() => {
    if (!listId || !filling) return;
    const ctrl = new AbortController();
    const onFrame = (frame: Frame) => {
      if (frame.event === 'error') return onStreamError?.(errorFrameCode(frame));
      const ev = toEvent(frame);
      if (!ev) return;
      setList((l) => l && applyFillToList(l, ev));
      setRows((r) => applyFillToRows(r, ev));
      const credits = onFillEnd && endedFillCredits(ended.current, ev);
      if (credits) onFillEnd(credits);
    };
    void followStream(
      `/v1/lists/${encodeURIComponent(listId)}/stream`,
      cursor,
      onFrame,
      ctrl.signal,
    );
    return () => ctrl.abort();
  }, [listId, cursor, filling, setList, setRows, onFillEnd, onStreamError]);
}
