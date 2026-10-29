'use client';

import { useTranslations } from 'next-intl';
import type { WatchlistChoices } from '@/lib/automations/editor-api';
import { type SignalFields as Fields, MAX_SIGNAL_SCORE } from '@/lib/automations/signal-draft';
import { ChoiceField, NumberField } from './fields';

export function SignalFields({
  fields,
  watchlists,
  watchlistDeleted,
  set,
}: {
  fields: Fields;
  watchlists: WatchlistChoices;
  watchlistDeleted: boolean;
  set: (patch: Partial<Fields>) => void;
}) {
  const t = useTranslations('automationEditor.fields.signal');
  if (watchlists.state === 'unreadable') {
    return <p className="text-ui text-ink">{t('unreadable')}</p>;
  }
  // No page makes a watchlist yet (#649), so there is nothing to link to.
  if (watchlists.items.length === 0) {
    return <p className="text-ui text-ink">{t('noWatchlist')}</p>;
  }
  const picked = watchlists.items.find((w) => w.id === fields.watchlistId);
  const choices = watchlists.items.map(({ id, name, enabled }) => ({
    id,
    name: enabled ? name : t('paused', { name }),
  }));
  return (
    <div className="flex flex-col gap-5">
      <ChoiceField
        label={t('watchlist')}
        hint={
          watchlistDeleted && !fields.watchlistId
            ? t('deleted')
            : picked
              ? t('watchlistHintList', { list: picked.listName })
              : t('watchlistHint')
        }
        value={fields.watchlistId}
        choices={choices}
        onChange={(watchlistId) => set({ watchlistId })}
      />
      <NumberField
        label={t('minScore')}
        hint={t('minScoreHint')}
        value={fields.minScore}
        min={0}
        max={MAX_SIGNAL_SCORE}
        step="any"
        onChange={(minScore) => set({ minScore })}
      />
    </div>
  );
}
