import type { ListRow } from '@/lib/lists/api';
import type { ColumnSpec } from '@/lib/lists/columns';

const ORIGIN_KINDS = ['database', 'web', 'maps', 'linkedin', 'other'] as const;
export type OriginKind = (typeof ORIGIN_KINDS)[number];

const WEB = new Set(['web']);
const DATABASE = new Set(['apollo', 'aiark', 'treg']);

/** A provider is a vendor name, which the customer never sees: only the kind of source. */
function originKind(provider: string): OriginKind {
  const p = provider.toLowerCase();
  if (WEB.has(p)) return 'web';
  if (p === 'google_maps') return 'maps';
  if (p === 'linkedin') return 'linkedin';
  if (DATABASE.has(p)) return 'database';
  return 'other';
}

/** The labels of the columns each kind of source filled, in `ORIGIN_KINDS` order. */
export function originGroups(row: ListRow, specs: ColumnSpec[]): [OriginKind, string[]][] {
  const groups = new Map<OriginKind, string[]>();
  for (const [key, meta] of Object.entries(row.cell_meta ?? {})) {
    if (!meta.provider) continue;
    const kind = originKind(meta.provider);
    groups.set(kind, [...(groups.get(kind) ?? []), specs.find((s) => s.key === key)?.label ?? key]);
  }
  return ORIGIN_KINDS.flatMap((k) => (groups.has(k) ? [[k, groups.get(k) ?? []]] : []));
}
