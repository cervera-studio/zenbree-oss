import { ROW_TABS, type RowTab } from '@/lib/lists/view';

/** The tab a link names, `all` for anything else: a hand-typed `?tab=` never breaks the page. */
export const toRowTab = (value: string | undefined): RowTab =>
  ROW_TABS.find((t) => t === value) ?? 'all';

export function toMinFit(value: string | string[] | undefined): number | null {
  const first = [value].flat()[0];
  if (first === undefined || !/^\d{1,3}$/.test(first)) return null;
  const min = Number(first);
  return min <= 100 ? min : null;
}

/** The list with a view's filters applied: what the list page reads back as `tab`, `q` and `fit`. */
export function viewHref(view: {
  listId: string;
  tab: string;
  query: string;
  minFit: number | null;
}) {
  const params = new URLSearchParams();
  if (view.tab !== 'all') params.set('tab', view.tab);
  if (view.query) params.set('q', view.query);
  if (view.minFit !== null) params.set('fit', String(view.minFit));
  const search = params.size ? `?${params}` : '';
  return `/lists/${encodeURIComponent(view.listId)}${search}`;
}
