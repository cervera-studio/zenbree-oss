/**
 * Lists + jobs from the browser (the panel refetches live while a job runs).
 * Wire shape kept snake_case: row `cells` are keyed by column `key`, and a
 * camelCase pass would rename `linkedin_url` in one but not the other.
 */
import { z } from 'zod';
import { api, authFetch } from '@/lib/api/browser';
import type { Schema } from '@/lib/api/schema';
import { API_ROUTES } from '@/lib/config/api-routes';
import { type Result, succeed } from '@/lib/types';
import { csvImportSchema } from './csv-import';
import { lookupsSchema, rowPhoneStatusSchema } from './lookups';

// NUMERIC columns may arrive as strings depending on the JSON encoder.
const num = z.union([z.number(), z.string().transform(Number)]);

// AI columns: the prompt and the state of the last fill run. The API types it as an open dict.
const columnConfigSchema = z.looseObject({
  prompt: z.string().optional(),
  mode: z.literal('snippet').optional(),
  /** A row missing this value is not delivered (#45). */
  required: z.boolean().optional(),
  fill: z.looseObject({ status: z.string(), error: z.string().nullable().optional() }).optional(),
});

const columnSchema = z.looseObject({
  key: z.string(),
  label: z.string(),
  kind: z.enum(['ai', 'code', 'score', 'static']),
  position: z.number(),
  config: columnConfigSchema.optional(),
}) satisfies z.ZodType<ListColumn>;
export type ListColumn = Omit<Schema<'ListColumn'>, 'config'> & {
  config?: z.infer<typeof columnConfigSchema>;
};

const snippetEstimateSchema = z.looseObject({
  research_rows: num,
  credits: num,
}) satisfies z.ZodType<Schema<'SnippetEstimate'>>;

/** What list work costs, as the API bills it (#660): the web keeps no copy. */
const creditPricesSchema = z.object({
  search: num,
  email: num,
  phone: num,
  ai_cell: num,
  ai_cells_per_run: num,
}) satisfies z.ZodType<CreditPrices>;
export type CreditPrices = Schema<'CreditPrices'>;

const LIST_STATUSES = ['ready', 'running', 'needs_input', 'plan_pending', 'empty'] as const;
export type ListStatus = Schema<'ListSummary'>['status'];

const listSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  entity: z.enum(['company', 'person']),
  mode: z.enum(['build', 'enrich']),
  chat_session_id: z.string().nullable(),
  project_id: z.string(),
  created_at: z.string(),
  row_count: num,
  // A paged search filled it: the agent's more_rows tool can fetch the next page.
  can_expand: z.boolean(),
  /** The vendor's total of the last search minus the rows held; null when unknown (#344). */
  findable: num.nullable(),
  /** Only the one-list read carries them; the index leaves them out. */
  columns: z.array(columnSchema).optional(),
  /** Where the AI column fill stream picks up after this load (#252); null unless a column fills. */
  fill_cursor: z.string().nullish(),
  /** A status this build does not know hides the pill rather than failing the list. */
  status: z.enum(LIST_STATUSES).nullable().catch(null),
  /** Rows not excluded with every required column filled (#45). */
  delivered_count: num,
  /** The latest plan's rows (approved, else proposed); null without a plan. */
  target_rows: num.nullable(),
  /** Credits charged by the turns of the list's chat. */
  credits_used: num,
  /** The chat's first message; null without one. */
  brief: z.string().nullable(),
  chat_title: z.string().nullable(),
}) satisfies z.ZodType<List>;
export type List = Omit<Schema<'ListSummary'>, 'status'> &
  Partial<Pick<Schema<'ListDetail'>, 'fill_cursor'>> & {
    columns?: ListColumn[];
    /** A status this build does not know hides the pill rather than failing the list. */
    status: ListStatus | null;
  };

/** The one-list read: the index leaves the prices out. */
const listDetailSchema = listSchema.extend({ prices: creditPricesSchema });
export type ListDetail = List & { prices: CreditPrices };

/** Kinds of evidence behind a fit score, as `REASON_CATEGORIES` in `api/app/agent/loop.py`. */
const REASON_CATEGORIES = [
  'firmographics',
  'website',
  'hiring',
  'news',
  'technology',
  'contact',
  'other',
] as const;

const cellMetaSchema = z.looseObject({
  status: z.string(),
  source: z.looseObject({ url: z.string(), title: z.string().nullish() }).nullish(),
  /** Vendor that supplied a plain (non-AI) value. */
  provider: z.string().nullish(),
});

export const rowSchema = z.looseObject({
  id: z.string(),
  seq: num,
  cells: z.record(z.string(), z.unknown()),
  // Per AI-column cell: outcome and the page the value came from.
  cell_meta: z.record(z.string(), cellMetaSchema).optional(),
  relevance_score: num.nullable(),
  /** Why the scorer gave that score (#159); null on rows scored before it. */
  score_reasons: z
    .array(
      z.looseObject({
        match: z.string(),
        category: z.enum(REASON_CATEGORIES),
        freshness: z.string().nullable(),
        key: z.string().nullable(),
      }),
    )
    .nullish(),
  excluded: z.boolean(),
  rating: z.enum(['good', 'bad']).nullish(),
  rating_note: z.string().nullish(),
  created_at: z.string().optional(),
  /** False while a required column of the list's plan is empty (#45). */
  delivered: z.boolean().optional(),
  /** Why the HubSpot read gate holds this person now (#120); `until` is null for a lasting block. */
  crm_hold: z.looseObject({ reason: z.string(), until: z.string().nullable() }).nullish(),
  /** Company rows whose chat has a Persone list: its people at this company (#229). */
  people_count: num.optional(),
  /** The first of those people, to name and link them in the company row (#344). */
  people: z
    .array(
      z.looseObject({
        row_id: z.string(),
        list_id: z.string(),
        full_name: z.string().nullable(),
        title: z.string().nullable(),
      }),
    )
    .optional(),
  /** The email and phone lookup state (#659), read through `lib/lists/lookups.ts`. */
  lookups: lookupsSchema,
}) satisfies z.ZodType<ListRow>;
declare const withheld: unique symbol;
/** The type of a cell the API does not send. Plain `never` would read as `undefined`, and a comparison of
 * `undefined` with a string is no type error; this type has no overlap with a string (TS2367). */
type Withheld = { readonly [withheld]: never };
/**
 * A row's cells by column key. The API strips the raw contact statuses (`public_cells`): the state is
 * `lookups`, so a read like `row.cells.email_status === 'failed'` is a type error.
 */
type Cells = Record<string, unknown> & { email_status?: Withheld; phone_status?: Withheld };
/** The API sends `relevance_score` as a Decimal string; `num` turns it into a number. */
export type ListRow = Omit<
  Schema<'ListRow'>,
  | 'cells'
  | 'relevance_score'
  | 'cell_meta'
  | 'created_at'
  | 'delivered'
  | 'score_reasons'
  | 'rating'
  | 'rating_note'
> &
  Partial<
    Pick<Schema<'ListRow'>, 'created_at' | 'delivered' | 'score_reasons' | 'rating' | 'rating_note'>
  > & {
    cells: Cells;
    relevance_score: number | null;
    /** Per AI-column cell: outcome and the page the value came from. */
    cell_meta?: Record<string, z.infer<typeof cellMetaSchema>>;
  };
export type RowRating = Schema<'RowRating'>['rating'];

/** What «Cerca il telefono» answers: the outcome, the credits it charged and the row after it. */
export const rowPhoneSchema = z.looseObject({
  status: rowPhoneStatusSchema,
  credits: num,
  row: rowSchema,
});

const rowsPageSchema = z.looseObject({
  rows: z.array(rowSchema),
  next_cursor: num.nullable(),
});

const jobSchema = z.looseObject({
  id: z.string(),
  chat_session_id: z.string().nullable(),
  status: z.enum(['queued', 'running', 'needs_input', 'succeeded', 'failed', 'cancelled']),
  phase: z.string().nullable(),
  progress: z.looseObject({ done: num, total: num }),
  list_id: z.string().nullable(),
  credits: z.looseObject({ reserved: num, spent: num }),
  /** The approved plan's stages when the job runs one (#49). */
  stages: z
    .array(z.looseObject({ tool: z.string(), label: z.string(), units: num, done: num }))
    .nullish(),
  error: z.string().nullable(),
  created_at: z.string(),
  finished_at: z.string().nullable(),
}) satisfies z.ZodType<Job>;
/** `GET /v1/jobs/{id}`: the nested run status, not the flat admin `Job` model. */
export type Job = Schema<'RunStatus'>;

/** What an upload would do (nothing written): `mapping` is the detected key per header,
 * `samples` the first value under each header, `rows` the file's data rows. */
const previewSchema = z.looseObject({
  headers: z.array(z.string()),
  samples: z.array(z.string()),
  rows: num,
  mapping: z.array(z.string()),
  to_import: num,
  duplicates: num,
  in_lists: num,
  excluded: num,
  incomplete: num,
  skipped: num,
  prices: creditPricesSchema,
}) satisfies z.ZodType<Schema<'CsvPreview'>>;
/** Mirrors the API's MAX_BYTES: refuse before sending. */
export const MAX_UPLOAD_BYTES = 5_000_000;

export type ExportFormat = 'csv' | 'xlsx';
export type ExportRows = 'all' | 'high_fit' | 'with_email';
export type ExportFilters = { verified_only: boolean; hide_off_target: boolean };
type Entity = 'company' | 'person';

/** Raw CSV body (not multipart) to an upload endpoint. */
const postCsv = <T>(
  path: string,
  params: Record<string, string>,
  file: File,
  schema: z.ZodType<T>,
) =>
  api(`${path}?${new URLSearchParams(params)}`, {
    method: 'POST',
    file: { body: file, type: 'text/csv' },
    camel: false,
    schema,
  });

const mappingParam = (mapping?: string[]): Record<string, string> =>
  mapping ? { mapping: JSON.stringify(mapping) } : {};

export const TERMINAL_JOB = new Set(['needs_input', 'succeeded', 'failed', 'cancelled']);

export const listsApi = {
  all: (projectId: string) =>
    api(`${API_ROUTES.lists.list}?project_id=${projectId}`, {
      camel: false,
      schema: z.looseObject({ lists: z.array(listSchema) }),
    }),
  get: (id: string) => api(API_ROUTES.lists.get(id), { camel: false, schema: listDetailSchema }),
  /** Soft delete (#341): `restore` is the undo. */
  remove: (id: string) => api(API_ROUTES.lists.get(id), { method: 'DELETE' }),
  restore: (id: string) => api(API_ROUTES.lists.restore(id), { method: 'POST' }),
  rows: (id: string, cursor = 0, limit = 100) =>
    api(`${API_ROUTES.lists.rows(id)}?cursor=${cursor}&limit=${limit}`, {
      camel: false,
      schema: rowsPageSchema,
    }),
  setExcluded: (id: string, rowId: string, excluded: boolean) =>
    api(API_ROUTES.lists.row(id, rowId), {
      method: 'PATCH',
      body: { excluded },
      camel: false,
      schema: rowSchema,
    }),
  rate: (id: string, rowId: string, rating: RowRating | null, note?: string) =>
    api(API_ROUTES.lists.rowRating(id, rowId), {
      method: 'PUT',
      body: { rating, note: note ?? null },
      camel: false,
      schema: rowSchema,
    }),
  /** Charges only when a number is found; `credits` is what it charged. */
  searchPhone: (id: string, rowId: string) =>
    api(API_ROUTES.lists.rowPhone(id, rowId), {
      method: 'POST',
      camel: false,
      schema: rowPhoneSchema,
    }),
  addAiColumn: (id: string, label: string, prompt: string) =>
    api(API_ROUTES.lists.columns(id), {
      method: 'POST',
      body: { label, prompt },
      camel: false,
      schema: columnSchema,
    }),
  estimateSnippet: (id: string, inputs: string[], rowIds: string[]) =>
    api(API_ROUTES.lists.estimateColumn(id), {
      method: 'POST',
      body: { inputs, row_ids: rowIds },
      camel: false,
      schema: snippetEstimateSchema,
    }),
  addSnippetColumn: (
    id: string,
    snippet: {
      label: string;
      prompt: string;
      inputs: string[];
      row_ids: string[];
      approved_credits: number;
    },
  ) =>
    api(API_ROUTES.lists.columns(id), {
      method: 'POST',
      body: { ...snippet, mode: 'snippet' },
      camel: false,
      schema: columnSchema,
    }),
  fillColumn: (id: string, key: string) =>
    api(API_ROUTES.lists.fillColumn(id, key), {
      method: 'POST',
      camel: false,
      schema: z.looseObject({ status: z.string() }),
    }),
  removeColumn: (id: string, key: string) =>
    api(API_ROUTES.lists.column(id, key), { method: 'DELETE' }),
  restoreColumn: (id: string, key: string) =>
    api(API_ROUTES.lists.restoreColumn(id, key), {
      method: 'POST',
      camel: false,
      schema: columnSchema,
    }),
  job: (id: string) => api(API_ROUTES.jobs.get(id), { camel: false, schema: jobSchema }),
  /** Import dialog steps 2-3: detected mapping and counts, nothing written. */
  preview: (file: File, entity: Entity, mapping?: string[]) =>
    postCsv(
      API_ROUTES.lists.uploadPreview,
      { entity, ...mappingParam(mapping) },
      file,
      previewSchema,
    ),
  /** Queues the import: the worker creates a chat holding the new list. */
  upload: (file: File, entity: Entity, projectId: string, name: string, mapping?: string[]) =>
    postCsv(
      API_ROUTES.lists.upload,
      {
        entity,
        name: name.slice(0, 120),
        project_id: projectId,
        ...mappingParam(mapping),
      },
      file,
      csvImportSchema,
    ),
  /** Authenticated download: a plain <a href> can't carry the bearer. */
  async download(
    id: string,
    name: string,
    opts: { format: ExportFormat; rows: ExportRows; columns: string[] } & ExportFilters,
  ): Promise<boolean> {
    const qs = new URLSearchParams({ rows: opts.rows });
    if (opts.verified_only) qs.set('verified_only', 'true');
    if (opts.hide_off_target) qs.set('hide_off_target', 'true');
    for (const c of opts.columns) qs.append('columns', c);
    const res = await authFetch(`${API_ROUTES.lists.export(id, opts.format)}?${qs}`).catch(
      () => null,
    );
    if (!res?.ok) return false;
    saveBlob(await res.blob(), `${name}.${opts.format}`);
    return true;
  },
};

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  Object.assign(document.createElement('a'), { href: url, download: filename }).click();
  URL.revokeObjectURL(url);
}

const ROWS_PER_PAGE = 500;

/** Every row of a list, following `next_cursor`; lists hold at most a few thousand rows. */
export async function fetchAllRows(id: string): Promise<Result<ListRow[]>> {
  const all: ListRow[] = [];
  for (let cursor: number | null = 0; cursor !== null; ) {
    const page = await listsApi.rows(id, cursor, ROWS_PER_PAGE);
    if (!page.success) return page;
    all.push(...page.data.rows);
    cursor = page.data.next_cursor;
  }
  return succeed(all);
}
