import type { Tone } from '@/components/ui/badge';
import type {
  CreditPrices,
  ExportFilters,
  ExportRows,
  List,
  ListColumn,
  ListRow,
  ListStatus,
} from './api';
import { displayText, scoreTone } from './columns';
import { isVerified } from './lookups';

export const LIST_STATUS: Record<ListStatus, { labelKey: `status.${ListStatus}`; tone: Tone }> = {
  ready: { labelKey: 'status.ready', tone: 'positive' },
  running: { labelKey: 'status.running', tone: 'accent' },
  needs_input: { labelKey: 'status.needs_input', tone: 'warning' },
  empty: { labelKey: 'status.empty', tone: 'warning' },
  plan_pending: { labelKey: 'status.plan_pending', tone: 'accent' },
};

export const isHttp = (url: string) => /^https?:\/\//i.test(url);
export const host = (url: string) => url.replace(/^https?:\/\/(www\.)?/, '');

export type ListKind = 'enrich' | 'person' | 'company';

/** The list's chat, where its drafts lead; null for a list no chat made. */
export const listChatHref = (list: Pick<List, 'chat_session_id'>) =>
  list.chat_session_id ? `/?s=${list.chat_session_id}` : null;

export function listKind(list: Pick<List, 'mode' | 'entity'>): ListKind {
  if (list.mode === 'enrich') return 'enrich';
  return list.entity === 'person' ? 'person' : 'company';
}

/** Credits per delivered row, one decimal in the locale; «<0,1» when it rounds to 0; «—» when no credits were used or a figure is missing. */
export function formatCostPerLead(
  credits: number | null | undefined,
  delivered: number | null | undefined,
  locale: string,
): string {
  if (!credits || !delivered) return '—';
  const format = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
  const cost = credits / delivered;
  return cost < 0.05 ? `<${format.format(0.1)}` : format.format(cost);
}

export type CreatedLabel =
  | { kind: 'today'; date: Date }
  | { kind: 'yesterday' }
  | { kind: 'date'; date: Date };

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
const DAY_MS = 86_400_000;

export function createdLabel(iso: string, now: Date): CreatedLabel {
  const date = new Date(iso);
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
  if (days === 0) return { kind: 'today', date };
  if (days === 1) return { kind: 'yesterday' };
  return { kind: 'date', date };
}

/** The labels of the required columns `row` misses, other than its email: what a turn must also fill for the row to
 * keep an email it buys (#45, #531). A required cell cleared by hand does not count, as in the API's
 * `UNFINISHED_SQL`. The API sends no raw status in `cells`, so `email_status` (the email's own: the search the draft
 * asks for writes it again) never counts. `phone_status` counts for `lookups.phone` `not_searched` or `refunded`, the
 * states the API's gap rule counts. A failed search after a refund still reads `refunded`, so the draft can name
 * the column once more than the API needs: a search it does not need costs nothing, a missing one repeats the refund. */
export function missingRequired(
  row: Pick<ListRow, 'cells' | 'cell_meta' | 'lookups'>,
  columns: ListColumn[],
): string[] {
  const phoneStatusEmpty = ['not_searched', 'refunded'].includes(row.lookups.phone.state);
  return columns
    .filter(
      (c) =>
        c.config?.required &&
        c.key !== 'email' &&
        c.key !== 'email_status' &&
        (c.key === 'phone_status' ? phoneStatusEmpty : !displayText(row.cells[c.key])) &&
        row.cell_meta?.[c.key]?.status !== 'manual',
    )
    .map((c) => c.label);
}

/** Non-excluded rows whose email the waterfall verified: the people header's «Email verificate». */
export const verifiedEmails = (rows: ListRow[]) =>
  rows.filter((r) => !r.excluded && isVerified(r.lookups.email)).length;

/** A company list's «Con almeno un contatto N di M» over the rows not excluded; null without a Persone list. */
export function companyContacts(rows: ListRow[]): { done: number; total: number } | null {
  const kept = rows.filter((r) => !r.excluded);
  if (!kept.some((r) => r.people_count !== undefined)) return null;
  return { done: kept.filter((r) => (r.people_count ?? 0) > 0).length, total: kept.length };
}

/** The most `rows` more rows can cost with this list's columns; pay-if-found, so often less. */
export function moreRowsCredits(prices: CreditPrices, columns: ListColumn[], rows: number): number {
  const has = (key: string) => columns.some((c) => c.key === key);
  const perRow =
    prices.search +
    (has('email') ? prices.email : 0) +
    (has('phone') ? prices.phone : 0) +
    prices.ai_cell * columns.filter((c) => c.kind === 'ai').length;
  return perRow * rows;
}

/** What a new AI column's fill can cost: a credit per filled cell of the delivered rows (#150), up to
 *  what one fill run researches. */
export const aiColumnCredits = (prices: CreditPrices, delivered: number) =>
  Math.min(delivered, prices.ai_cells_per_run) * prices.ai_cell;

export const ROW_TABS = ['all', 'delivered', 'pending', 'no_contact', 'excluded'] as const;
export type RowTab = (typeof ROW_TABS)[number];

/** Tabs that only show while they hold a row. */
const HIDDEN_WHEN_EMPTY: ReadonlySet<RowTab> = new Set(['no_contact', 'excluded']);

export const shownTabs = (counts: Record<RowTab, number>) =>
  ROW_TABS.filter((tab) => !HIDDEN_WHEN_EMPTY.has(tab) || counts[tab] > 0);

/** Not excluded and holding every required column; the API's `delivered` once it sends it. */
const isDelivered = (r: ListRow) => !r.excluded && (r.delivered ?? true);

const TAB_MATCH: Record<RowTab, (r: ListRow) => boolean> = {
  all: () => true,
  delivered: isDelivered,
  pending: (r) => !r.excluded && !isDelivered(r),
  // `people_count` is only sent once the chat has a Persone list: before that no company lacks one.
  no_contact: (r) => !r.excluded && r.people_count === 0,
  excluded: (r) => r.excluded,
};

export function tabCounts(rows: ListRow[]): Record<RowTab, number> {
  return {
    all: rows.length,
    delivered: rows.filter(TAB_MATCH.delivered).length,
    pending: rows.filter(TAB_MATCH.pending).length,
    no_contact: rows.filter(TAB_MATCH.no_contact).length,
    excluded: rows.filter(TAB_MATCH.excluded).length,
  };
}

const SEARCH_KEYS = ['full_name', 'name', 'company_name', 'company_domain', 'domain', 'email'];

/** The «Fit oltre 80» chip's floor. */
export const FIT_MIN = 80;

/** The rows of one tab that match a search over name, company, domain and email, and with `minFit` the fit floor:
 * the API's `min_fit` rule, where an unscored row is out. A saved view can hold any floor from 0 to 100. */
export function visibleRows(
  rows: ListRow[],
  tab: RowTab,
  query: string,
  minFit: number | null = null,
): ListRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter(
    (r) =>
      TAB_MATCH[tab](r) &&
      (minFit === null || (r.relevance_score !== null && r.relevance_score >= minFit)) &&
      (!q || SEARCH_KEYS.some((k) => displayText(r.cells[k]).toLowerCase().includes(q))),
  );
}

/** The API's export row filters, applied to delivered rows. */
const EXPORT_ROW_FILTERS: Record<ExportRows, (r: ListRow) => boolean> = {
  all: () => true,
  high_fit: (r) => scoreTone(r.relevance_score) === 'high',
  with_email: (r) => displayText(r.cells.email) !== '',
};

/** How many delivered rows each export row filter would hold, once the optional filters apply. */
export function exportRowCounts(
  rows: ListRow[],
  { verified_only, hide_off_target }: ExportFilters = {
    verified_only: false,
    hide_off_target: false,
  },
): Record<ExportRows, number> {
  const delivered = rows.filter(
    (r) =>
      isDelivered(r) &&
      (!verified_only || isVerified(r.lookups.email)) &&
      (!hide_off_target || r.rating !== 'bad'),
  );
  const count = (filter: ExportRows) => delivered.filter(EXPORT_ROW_FILTERS[filter]).length;
  return { all: count('all'), high_fit: count('high_fit'), with_email: count('with_email') };
}
