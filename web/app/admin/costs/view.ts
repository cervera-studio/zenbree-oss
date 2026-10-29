export type View = { from?: string; to?: string; table: boolean; sort: 'cost' | 'margin' };

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const date = (v: string | string[] | undefined) =>
  typeof v === 'string' && DATE.test(v) ? v : undefined;

export function parseView(sp: Record<string, string | string[] | undefined>): View {
  return {
    from: date(sp.from),
    to: date(sp.to),
    table: sp.view === 'table',
    sort: sp.sort === 'margin' ? 'margin' : 'cost',
  };
}

export function costsHref(v: View, patch: Partial<View>): string {
  const { from, to, table, sort } = { ...v, ...patch };
  const q = new URLSearchParams({
    ...(from && { from }),
    ...(to && { to }),
    ...(table && { view: 'table' }),
    ...(sort === 'margin' && { sort }),
  }).toString();
  return q ? `/admin/costs?${q}` : '/admin/costs';
}
