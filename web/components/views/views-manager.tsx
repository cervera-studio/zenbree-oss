'use client';

import { Check, Pencil, X } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState, useTransition } from 'react';
import { createViewAction, deleteViewAction, renameViewAction } from '@/app/(app)/views/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useErrorText } from '@/i18n/error-text';
import type { SavedView } from '@/lib/views/api';
import { viewHref } from '@/lib/views/href';

export function ViewsManager({ views }: { views: SavedView[] }) {
  const t = useTranslations('lists.views');
  const tl = useTranslations('lists');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [error, setError] = useState('');
  const [pending, startTransition] = useTransition();

  function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing?.name.trim()) return;
    const { id, name } = editing;
    setError('');
    startTransition(async () => {
      const result = await renameViewAction(id, name);
      if (!result.success) return setError(errorText(result.error, t('actionError')));
      setEditing(null);
      toast.success(t('renamed'));
    });
  }

  // Reversible: gone at once, «Annulla» saves the same view again.
  function remove(view: SavedView) {
    setError('');
    startTransition(async () => {
      const result = await deleteViewAction(view.id);
      if (!result.success) return setError(errorText(result.error, t('actionError')));
      toast.success(t('removed', { name: view.name }), {
        action: {
          label: tc('undo'),
          onClick: () => undo(view),
        },
      });
    });
  }

  async function undo(view: SavedView) {
    const result = await createViewAction(view);
    if (!result.success) toast.error(errorText(result.error, t('actionError')));
  }

  if (views.length === 0) return <p className="text-muted text-ui">{t('empty')}</p>;
  return (
    <div className="flex flex-col gap-3">
      {error && (
        <p role="alert" className="text-negative text-ui-strong">
          {error}
        </p>
      )}
      <StackedTable frame>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('nameCol')}</TableHead>
            <TableHead scope="col">{t('listCol')}</TableHead>
            <TableHead scope="col">{t('filtersCol')}</TableHead>
            <TableHead scope="col" className="w-24">
              <span className="sr-only">{t('actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {views.map((view) => (
            <TableRow key={view.id}>
              <TableCell className="text-ui-strong text-ink">
                <CellLabel>{t('nameCol')}</CellLabel>
                {editing?.id === view.id ? (
                  <form className="flex items-center gap-1" onSubmit={rename}>
                    <Input
                      aria-label={t('renameLabel')}
                      className="h-8"
                      value={editing.name}
                      maxLength={100}
                      autoFocus
                      onChange={(event) => setEditing({ id: view.id, name: event.target.value })}
                      onKeyDown={(event) => event.key === 'Escape' && setEditing(null)}
                    />
                    <Button
                      type="submit"
                      variant="ghost"
                      size="icon-sm"
                      disabled={pending || !editing.name.trim()}
                      aria-label={t('renameSave')}
                    >
                      <Check aria-hidden="true" />
                    </Button>
                  </form>
                ) : (
                  <Link href={viewHref(view)} aria-label={t('open', { name: view.name })}>
                    {view.name}
                  </Link>
                )}
              </TableCell>
              <TableCell className="text-muted">
                <CellLabel>{t('listCol')}</CellLabel>
                {view.listName}
              </TableCell>
              <TableCell className="text-muted">
                <CellLabel>{t('filtersCol')}</CellLabel>
                {[
                  tl(`tab.${view.tab}`),
                  view.query && t('filterQuery', { query: view.query }),
                  view.minFit !== null && t('filterFit', { min: view.minFit }),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </TableCell>
              <TableCell>
                <div className="flex gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="text-muted"
                    disabled={pending}
                    aria-label={t('rename', { name: view.name })}
                    onClick={() => setEditing({ id: view.id, name: view.name })}
                  >
                    <Pencil aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="text-muted"
                    disabled={pending}
                    aria-label={t('remove', { name: view.name })}
                    onClick={() => remove(view)}
                  >
                    <X aria-hidden="true" />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </StackedTable>
    </div>
  );
}
