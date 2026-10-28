import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';

/** What a finished tool call found: one union by `kind`, from `ToolCallRecord.summary` in the API. */
export type ToolSummary = NonNullable<Camel<Schema<'ToolCallRecord'>>['summary']>;
type Kind = ToolSummary['kind'];
type Searched = Extract<ToolSummary, { tally: unknown }>;
type TallyKey = keyof Searched['tally'];

export type SummaryState = 'done' | 'none' | 'retry' | 'error';

/**
 * How each kind of summary reads. A kind the API adds fails `pnpm typecheck` here, for the chip and the
 * admin transcript at once. A batch that only failed is `retry` although the tool result is ok, and
 * `no_row_match` is `error` although the model saw an answer.
 */
const KIND_STATE: Record<Kind, SummaryState> = {
  count: 'done',
  all_found: 'done',
  partial: 'done',
  legacy: 'done',
  none_found: 'none',
  nothing_to_search: 'none',
  all_failed: 'retry',
  no_row_match: 'error',
};

type Contact = Searched['contact'];
type TallyPart = 'found' | 'invalid' | 'notFound' | 'failed' | 'noSource' | 'noInput';
/** A batch that only failed says so; beside what it found, the failed rows are the ones to retry. */
type PartName = TallyPart | 'failedAll';

/** The message of each bucket of the tally. The order of the keys is the order on the screen. */
const TALLY_PART: Record<TallyKey, TallyPart> = {
  found: 'found',
  invalid: 'invalid',
  notFound: 'notFound',
  failed: 'failed',
  noSource: 'noSource',
  noInput: 'noInput',
};

/** A key of the `toolSummary` messages that takes the count `n`. */
type CountKey =
  | `count.${Extract<ToolSummary, { noun: unknown }>['noun']}`
  | `${Contact}.${PartName | 'unmatched' | 'refunded'}`;
type PlainKey = 'nothingToSearch' | 'noRowMatch';
export type SummaryPart = { key: CountKey; n: number } | { key: PlainKey };

export interface SummaryView {
  state: SummaryState;
  parts: SummaryPart[];
}

function partName(s: Searched, k: TallyKey): PartName {
  return k === 'failed' && s.kind === 'all_failed' ? 'failedAll' : TALLY_PART[k];
}

function searchedParts(s: Searched): SummaryPart[] {
  const counted = (Object.keys(TALLY_PART) as TallyKey[])
    // «0 email» is an answer only when the batch searched and found none.
    .filter((k) => s.tally[k] > 0 || (k === 'found' && s.kind === 'none_found'))
    .map((k) => ({ key: `${s.contact}.${partName(s, k)}` as const, n: s.tally[k] }));
  return [
    ...counted,
    ...(s.unmatched > 0 ? [{ key: `${s.contact}.unmatched` as const, n: s.unmatched }] : []),
    // The turn took these rows back as undelivered (#659): set when the turn ends.
    ...(s.refunded > 0 ? [{ key: `${s.contact}.refunded` as const, n: s.refunded }] : []),
  ];
}

function partsOf(s: ToolSummary): SummaryPart[] {
  if ('tally' in s) return searchedParts(s);
  switch (s.kind) {
    case 'count':
      return [{ key: `count.${s.noun}`, n: s.count }];
    case 'legacy':
      // The old rows' remainder is not «not found»: only the found count and the rows to retry were stored.
      return [
        { key: `${s.contact}.found`, n: s.found },
        ...((s.failed ?? 0) > 0 ? [{ key: `${s.contact}.failed` as const, n: s.failed ?? 0 }] : []),
        ...((s.refunded ?? 0) > 0
          ? [{ key: `${s.contact}.refunded` as const, n: s.refunded ?? 0 }]
          : []),
      ];
    case 'nothing_to_search':
      return [
        { key: 'nothingToSearch' },
        ...(s.unmatched > 0 ? [{ key: `${s.contact}.unmatched` as const, n: s.unmatched }] : []),
      ];
    case 'no_row_match':
      return [{ key: 'noRowMatch' }];
  }
}

export function summaryView(s: ToolSummary): SummaryView {
  return { state: KIND_STATE[s.kind], parts: partsOf(s) };
}

const count = z.number();
const contact = z.enum(['email', 'phone']);

/** Reads a summary from the wire (live frame, stored message or admin transcript): it never throws. */
export const toolSummarySchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('count'), noun: z.enum(['company', 'person']), count }),
  z.object({
    kind: z.enum(['all_found', 'partial', 'none_found', 'all_failed']),
    contact,
    searched: count,
    tally: z.object({
      found: count,
      invalid: count,
      notFound: count,
      failed: count,
      noSource: count,
      noInput: count,
    }),
    unmatched: count.default(0),
    refunded: count.default(0),
  }),
  z.object({ kind: z.literal('nothing_to_search'), contact, unmatched: count.default(0) }),
  z.object({ kind: z.literal('no_row_match'), contact }),
  z.object({
    kind: z.literal('legacy'),
    contact,
    found: count,
    failed: count.default(0),
    refunded: count.default(0),
  }),
]) satisfies z.ZodType<ToolSummary>;

export function parseToolSummary(raw: unknown): ToolSummary | undefined {
  const parsed = toolSummarySchema.safeParse(raw);
  return parsed.success ? parsed.data : undefined;
}
