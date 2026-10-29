import { z } from 'zod';
import { api } from '@/lib/api/browser';
import { API_ROUTES } from '@/lib/config/api-routes';
import { type AppErrorJSON, type Result, succeed } from '@/lib/types';
import type { RowRating } from './api';
import type { RowTab } from './view';

/** The most row ids one bulk request takes, and the most people one campaign takes: the API's `MAX_TARGETS`. */
export const BULK_MAX = 1000;

const SKIP_REASONS = [
  'excluded',
  'duplicate',
  'no_contact',
  'in_other_campaign',
  'held_by_instantly',
  'stopped',
  'not_delivered',
  'not_found',
  'over_limit',
] as const;
/** Why add-to-campaign left a row out; `other` holds a reason the API added after this web. */
export type SkipReason = (typeof SKIP_REASONS)[number] | 'other';
export type AddResult = { added: number; skipped: Partial<Record<SkipReason, number>> };
export type MarkResult = { marked: number; not_found: number };
export type RateResult = { rated: number; not_found: number };

/** What a bulk action did before it stopped. `error` is the request that failed; `unsent` are the ids of that
 * request and of every one after it, which the API never changed. */
export type Chunked<T> = { result: T; error: AppErrorJSON | null; unsent: string[] };

type Action = 'skipAction.excluded' | 'skipAction.in_other_campaign' | 'skipAction.not_delivered';
type NextStep = { href: string; action: Action } | { tab: RowTab; action: Action } | null;

/** Where the user goes to bring a skipped person in, in the order the result lists them. The text of each reason
 * is `lists.bulk.skip.<reason>`; `action` labels its link or tab button. */
export const SKIP_NEXT: Record<SkipReason, NextStep> = {
  excluded: { href: '/settings/exclusions', action: 'skipAction.excluded' },
  duplicate: null,
  // «Senza contatto» is the company tab (no people found), so a person list has no tab for it.
  no_contact: null,
  in_other_campaign: { href: '/campaigns', action: 'skipAction.in_other_campaign' },
  held_by_instantly: null,
  stopped: null,
  not_delivered: { tab: 'pending', action: 'skipAction.not_delivered' },
  not_found: null,
  over_limit: null,
  other: null,
};

const isKnown = (reason: string): reason is SkipReason =>
  (SKIP_REASONS as readonly string[]).includes(reason);

const markedSchema = z.object({ marked: z.number(), not_found: z.number() });
const ratedSchema = z.object({ rated: z.number(), not_found: z.number() });
const addedSchema = z.object({ added: z.number(), skipped: z.record(z.string(), z.number()) });
const campaignsSchema = z.looseObject({
  campaigns: z.array(
    z.looseObject({
      id: z.string(),
      name: z.string(),
      status: z.enum(['draft', 'active', 'paused', 'stopped', 'completed']),
    }),
  ),
});
export type DraftCampaign = { id: string; name: string };

export const bulkApi = {
  markOffTarget: (listId: string, rowIds: string[]) =>
    api(API_ROUTES.lists.markOffTarget(listId), {
      method: 'POST',
      body: { row_ids: rowIds },
      camel: false,
      schema: markedSchema,
    }),
  rate: (listId: string, rowIds: string[], rating: RowRating | null) =>
    api(API_ROUTES.lists.rateRows(listId), {
      method: 'POST',
      body: { row_ids: rowIds, rating },
      camel: false,
      schema: ratedSchema,
    }),
  addToCampaign: (listId: string, campaignId: string, rowIds: string[]) =>
    api(API_ROUTES.lists.addToCampaign(listId), {
      method: 'POST',
      body: { campaign_id: campaignId, row_ids: rowIds },
      camel: false,
      schema: addedSchema,
    }),
  /** The active project's draft campaigns: the only ones rows can join. */
  draftCampaigns: async (): Promise<Result<DraftCampaign[]>> => {
    const res = await api(`${API_ROUTES.campaigns.list}?status=draft`, {
      camel: false,
      schema: campaignsSchema,
    });
    if (!res.success) return res;
    return succeed(res.data.campaigns.map(({ id, name }) => ({ id, name })));
  },
};

function chunk<T>(items: readonly T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
  return chunks;
}

/** Sends `ids` in requests of `BULK_MAX`, one after another, and stops at the first that fails. */
async function inChunks<T, R>(
  ids: readonly string[],
  zero: T,
  send: (ids: string[]) => Promise<Result<R>>,
  add: (sum: T, part: R) => T,
): Promise<Chunked<T>> {
  let result = zero;
  const chunks = chunk(ids, BULK_MAX);
  for (const [i, part] of chunks.entries()) {
    const res = await send(part);
    if (!res.success) return { result, error: res.error, unsent: chunks.slice(i).flat() };
    result = add(result, res.data);
  }
  return { result, error: null, unsent: [] };
}

export const markOffTarget = (listId: string, ids: readonly string[]) =>
  inChunks<MarkResult, MarkResult>(
    ids,
    { marked: 0, not_found: 0 },
    (part) => bulkApi.markOffTarget(listId, part),
    (sum, part) => ({
      marked: sum.marked + part.marked,
      not_found: sum.not_found + part.not_found,
    }),
  );

/** One rating for every row; a null also clears the note, `good` and `bad` keep it. */
export const rateRows = (listId: string, ids: readonly string[], rating: RowRating | null) =>
  inChunks<RateResult, RateResult>(
    ids,
    { rated: 0, not_found: 0 },
    (part) => bulkApi.rate(listId, part, rating),
    (sum, part) => ({ rated: sum.rated + part.rated, not_found: sum.not_found + part.not_found }),
  );

function addSkipped(sum: AddResult, part: { added: number; skipped: Record<string, number> }) {
  const skipped = { ...sum.skipped };
  for (const [reason, n] of Object.entries(part.skipped)) {
    const key = isKnown(reason) ? reason : 'other';
    skipped[key] = (skipped[key] ?? 0) + n;
  }
  return { added: sum.added + part.added, skipped };
}

export const addToCampaign = (listId: string, campaignId: string, ids: readonly string[]) =>
  inChunks(
    ids,
    { added: 0, skipped: {} } as AddResult,
    (part) => bulkApi.addToCampaign(listId, campaignId, part),
    addSkipped,
  );
