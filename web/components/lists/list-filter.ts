import type { ListRow } from '@/lib/lists/api';
import { type ColumnSpec, cellValue, displayText, scoreTone } from '@/lib/lists/columns';

export const ROW_FILTERS = ['all', 'high_fit', 'excluded'] as const;
export type RowFilter = (typeof ROW_FILTERS)[number];

/** The phone list's filters (#171), over the rows already loaded: a text match plus a chip. */
export function filterRows(
  rows: ListRow[],
  specs: ColumnSpec[],
  query: string,
  show: RowFilter,
): ListRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter((row) => {
    if (show === 'excluded' && !row.excluded) return false;
    // Excluded rows never reach a campaign, so they are not a fit worth showing.
    if (show === 'high_fit' && (row.excluded || scoreTone(row.relevance_score) !== 'high'))
      return false;
    return !q || specs.some((s) => displayText(cellValue(row, s.key)).toLowerCase().includes(q));
  });
}

/** A row card's title (first non-empty text-like cell) and up to two more values under it. */
export function cardSummary(row: ListRow, specs: ColumnSpec[]) {
  const values = specs
    .filter((s) => s.render !== 'score' && s.render !== 'ai')
    .map((s) => displayText(cellValue(row, s.key)))
    .filter(Boolean);
  return { title: values[0] ?? '—', subtitle: values.slice(1, 3).join(' · ') };
}
