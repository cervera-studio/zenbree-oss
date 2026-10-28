'use client';

import { Bookmark } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState, useTransition } from 'react';
import { createViewAction } from '@/app/(app)/views/actions';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import type { RowTab } from '@/lib/lists/view';

/** «Salva vista»: names the list's current tab, search and fit floor as a view of the project (#827). */
export function SaveViewDialog({
  listId,
  tab,
  query,
  minFit,
}: {
  listId: string;
  tab: RowTab;
  query: string;
  minFit: number | null;
}) {
  const t = useTranslations('lists.views');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [pending, startTransition] = useTransition();

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    startTransition(async () => {
      const result = await createViewAction({ listId, name, tab, query: query.trim(), minFit });
      if (!result.success) return setError(errorText(result.error, t('actionError')));
      setOpen(false);
      setName('');
      toast.success(t('saved'));
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" className="phone:h-11" onClick={() => setOpen(true)}>
        <Bookmark aria-hidden="true" /> {t('save')}
      </Button>
      <DialogContent>
        <form className="grid gap-4" onSubmit={save}>
          <DialogHeader>
            <DialogTitle>{t('saveTitle')}</DialogTitle>
            <DialogDescription>{t('saveBody')}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <label className="text-small" htmlFor="saved-view-name">
              {t('nameLabel')}
            </label>
            <Input
              id="saved-view-name"
              value={name}
              maxLength={100}
              required
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="text-negative text-ui-strong">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              {tc('cancel')}
            </Button>
            <Button type="submit" disabled={pending || !name.trim()}>
              {tc('save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
