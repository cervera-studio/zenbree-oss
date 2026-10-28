import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import type { ListRow, RowRating } from '@/lib/lists/api';
import { markOffTarget, rateRows } from '@/lib/lists/bulk';

type Before = { id: string; rating: RowRating | null };

/** «Segna fuori target» on many rows at once, shown at once and undone from its toast. */
export function useMarkOffTarget({
  listId,
  patchRow,
  refetch,
  onMarked,
}: {
  listId: string;
  patchRow: (id: string, patch: Partial<ListRow>) => void;
  refetch: () => void;
  onMarked: () => void;
}) {
  const t = useTranslations('lists');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  const [busy, setBusy] = useState(false);

  // One bulk request per earlier rating: a request per row ran out of auth tokens after ~90 rows.
  const undo = async (before: Before[]) => {
    const groups = await Promise.all(
      (['good', null] as const).map(async (rating) => {
        const ids = before.filter((b) => b.rating === rating).map((b) => b.id);
        return { rating, ids, done: await rateRows(listId, ids, rating) };
      }),
    );
    // One patch per response re-rendered the whole table each time: 1,100 rows took minutes.
    for (const { rating, ids, done } of groups) {
      const unsent = new Set(done.unsent);
      const patch = rating === null ? { rating, rating_note: null } : { rating };
      for (const id of ids) if (!unsent.has(id)) patchRow(id, patch);
    }
    if (groups.every((g) => g.done.error === null)) return;
    toast.error(t('bulk.undoError'));
    refetch();
  };

  const mark = async (rows: ListRow[]) => {
    const before: Before[] = rows.map((r) => ({ id: r.id, rating: r.rating ?? null }));
    setBusy(true);
    for (const r of rows) patchRow(r.id, { rating: 'bad' });
    const done = await markOffTarget(
      listId,
      rows.map((r) => r.id),
    );
    setBusy(false);
    if (done.error) {
      // The requests before the failed one did mark their rows: only the rest go back.
      const unsent = new Set(done.unsent);
      for (const b of before) {
        if (unsent.has(b.id)) patchRow(b.id, { rating: b.rating });
      }
      const sent = before.filter((b) => !unsent.has(b.id));
      toast.error(
        errorText(done.error, t('updateError')),
        done.result.marked > 0
          ? {
              description: t('bulk.marked', { n: done.result.marked }),
              action: { label: tc('undo'), onClick: () => void undo(sent) },
            }
          : undefined,
      );
      return;
    }
    onMarked();
    if (done.result.not_found > 0) refetch();
    toast.success(t('bulk.marked', { n: done.result.marked }), {
      description: t('bulk.markedHint', { filter: t('exportFilters.hide_off_target') }),
      action: { label: tc('undo'), onClick: () => void undo(before) },
    });
  };

  return { busy, mark };
}
