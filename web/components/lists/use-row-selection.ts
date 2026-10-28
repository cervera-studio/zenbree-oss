import { useState } from 'react';
import type { ListRow } from '@/lib/lists/api';

export type RowSelection = {
  /** The shown rows that are checked: what the bulk bar counts and acts on. */
  checked: ListRow[];
  has: (id: string) => boolean;
  toggle: (id: string) => void;
  /** The header box over the shown rows. */
  all: boolean | 'indeterminate';
  toggleAll: () => void;
  clear: () => void;
};

/** Checked rows by id. A row a filter hides keeps its check but leaves `checked`, so it is neither counted nor
 * acted on, and shows checked again once the filter clears; a row a refetch drops leaves the same way. */
export function useRowSelection(shown: ListRow[]): RowSelection {
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const checked = shown.filter((r) => selected.has(r.id));
  const every = shown.length > 0 && checked.length === shown.length;

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const r of shown) {
        if (every) next.delete(r.id);
        else next.add(r.id);
      }
      return next;
    });

  return {
    checked,
    has: (id) => selected.has(id),
    toggle,
    all: every ? true : checked.length > 0 ? 'indeterminate' : false,
    toggleAll,
    clear: () => setSelected(new Set()),
  };
}
