'use client';

import { Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState } from 'react';
import { createCampaignAction } from '@/app/(app)/campaigns/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useAction } from '@/lib/hooks/use-action';

export function NewCampaignForm({
  lists,
  defaultListId,
}: {
  lists: { id: string; name: string }[];
  defaultListId?: string;
}) {
  const t = useTranslations('campaigns.new');
  const [name, setName] = useState('');
  const [listId, setListId] = useState(() => initialListId(lists, defaultListId));
  const { pending, error, run } = useAction();

  if (lists.length === 0)
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="text-muted text-sm">{t('noLists')}</p>
        <Button asChild className="phone:h-11">
          <Link href="/">{t('newChat')}</Link>
        </Button>
      </div>
    );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!listId) return;
    run(() => createCampaignAction(name, listId));
  }

  return (
    <>
      <form className="flex flex-col gap-3 sm:flex-row" onSubmit={submit}>
        <label className="sr-only" htmlFor="campaign-name">
          {t('name')}
        </label>
        <Input
          id="campaign-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('name')}
          maxLength={200}
          required
          autoFocus={Boolean(defaultListId)}
          className="phone:h-11"
        />
        <label className="sr-only" htmlFor="campaign-list">
          {t('list')}
        </label>
        <Select
          touch
          id="campaign-list"
          value={listId}
          onChange={(e) => setListId(e.target.value)}
          aria-describedby={listId ? undefined : 'campaign-list-missing'}
        >
          {!listId && (
            <option value="" disabled>
              {t('list')}
            </option>
          )}
          {lists.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
        <Button type="submit" className="phone:h-11" disabled={pending || !name.trim() || !listId}>
          {pending && <Loader2 className="size-4 animate-spin" />}
          {t('create')}
        </Button>
        {error && (
          <p role="alert" className="text-negative text-sm">
            {t('error')}
          </p>
        )}
      </form>
      {!listId && (
        <p id="campaign-list-missing" className="text-muted text-sm">
          {t('listMissing')}
        </p>
      )}
    </>
  );
}

function initialListId(lists: { id: string }[], requested: string | undefined): string {
  if (!requested) return lists[0]?.id ?? '';
  return lists.some((l) => l.id === requested) ? requested : '';
}
