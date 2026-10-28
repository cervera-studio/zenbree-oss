/** The CSV import job (#344): its state, its stream (`progress`, `done`, `failed`) and the discarded rows. */
import { type Dispatch, type SetStateAction, useEffect } from 'react';
import { z } from 'zod';
import { api, authFetch } from '@/lib/api/browser';
import { errorFrameCode, type Frame, followStream } from '@/lib/api/event-stream';
import type { Schema } from '@/lib/api/schema';
import { API_ROUTES } from '@/lib/config/api-routes';
import { saveBlob } from './api';

const num = z.number();

export const csvImportSchema = z.looseObject({
  id: z.string(),
  status: z.enum(['queued', 'running', 'done', 'failed']),
  name: z.string(),
  total: num,
  processed: num,
  rows_added: num,
  duplicates: num,
  in_lists: num,
  excluded: num,
  skipped: num,
  discarded: num,
  list_id: z.string().nullable(),
  chat_session_id: z.string().nullable(),
  error: z.string().nullable(),
  cursor: z.string().nullable(),
}) satisfies z.ZodType<Schema<'CsvImportState'>>;

export const csvImportApi = {
  /** The import to resume on reopening the dialog: running, or finished and not yet seen. */
  active: () =>
    api(API_ROUTES.lists.activeImport, { camel: false, schema: csvImportSchema.nullable() }),
  get: (id: string) =>
    api(API_ROUTES.lists.csvImport(id), { camel: false, schema: csvImportSchema }),
  seen: (id: string) => api(API_ROUTES.lists.csvImportSeen(id), { method: 'POST' }),
  /** Authenticated download: a plain <a href> can't carry the bearer. */
  async downloadDiscarded(id: string, name: string): Promise<boolean> {
    const res = await authFetch(API_ROUTES.lists.csvImportDiscarded(id)).catch(() => null);
    if (!res?.ok) return false;
    saveBlob(await res.blob(), `${name} - scartate.csv`);
    return true;
  },
};

/** A frame applied to the import: `progress` moves the counters, `done` and `failed` end it with what they carry
 * (the caller then refetches the final counts). `error` ends the stream, not the import (the caller left the
 * organization): the dialog shows its code instead of a progress that never moves again. */
export function applyFrame(imp: Schema<'CsvImportState'>, frame: Frame): Schema<'CsvImportState'> {
  const { event, data } = frame;
  if (event === 'error') return { ...imp, status: 'failed', error: errorFrameCode(frame) };
  try {
    const body = JSON.parse(data);
    if (event === 'progress')
      return { ...imp, status: 'running', processed: body.processed, total: body.total };
    if (event === 'done')
      return {
        ...imp,
        status: 'done',
        processed: imp.total,
        list_id: body.list_id,
        chat_session_id: body.chat_session_id,
        rows_added: body.rows_added,
      };
    if (event === 'failed') return { ...imp, status: 'failed', error: body.code };
  } catch {
    // not JSON: the next frame carries the same counters
  }
  return imp;
}

/** Follow the import's stream from the snapshot's cursor until it ends; a refetch restarts it with a new cursor. */
export function useImportStream(
  imp: Schema<'CsvImportState'> | null,
  setImp: Dispatch<SetStateAction<Schema<'CsvImportState'> | null>>,
) {
  const id = imp?.id;
  const cursor = imp?.cursor ?? null;
  const live = imp?.status === 'queued' || imp?.status === 'running';
  useEffect(() => {
    if (!id || !live) return;
    const ctrl = new AbortController();
    const onFrame = (frame: Frame) => {
      setImp((i) => i && applyFrame(i, frame));
      if (frame.event === 'done' || frame.event === 'failed')
        void csvImportApi.get(id).then((res) => res.success && setImp(res.data));
    };
    void followStream(API_ROUTES.lists.csvImportStream(id), cursor, onFrame, ctrl.signal);
    return () => ctrl.abort();
  }, [id, cursor, live, setImp]);
}
