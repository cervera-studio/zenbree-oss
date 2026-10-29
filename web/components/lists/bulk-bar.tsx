'use client';

import { X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';

/** The actions on the checked rows: floating under the table on desktop, in place of the phone bar on a phone. */
export function BulkBar({
  count,
  people,
  busy,
  phone,
  onAdd,
  onInstantly,
  onMark,
  onClear,
}: {
  count: number;
  /** A person list: only people join a campaign. */
  people: boolean;
  /** A bulk request runs: the actions wait for it. */
  busy: boolean;
  phone: boolean;
  onAdd: () => void;
  onInstantly: () => void;
  onMark: () => void;
  onClear: () => void;
}) {
  const t = useTranslations('lists.bulk');
  const label = <span className="text-data font-mono">{t('selected', { n: count })}</span>;
  const clear = (
    <Button
      variant="ghost"
      size={phone ? 'icon-lg' : 'icon-xs'}
      aria-label={t('clear')}
      onClick={onClear}
      className="text-muted"
    >
      <X />
    </Button>
  );

  if (phone) {
    return (
      <div
        data-phone-bar
        role="toolbar"
        aria-label={t('barLabel')}
        className="bg-ground sticky bottom-0 -mx-4 mt-auto flex flex-wrap items-center gap-2 border-t px-4 pt-3 pb-5"
      >
        <span className="grow">{label}</span>
        {clear}
        {people && (
          <Button className="h-12.5 grow rounded-xl" disabled={busy} onClick={onAdd}>
            {t('addToCampaign')}
          </Button>
        )}
        {people && (
          <Button
            variant="outline"
            className="h-12.5 grow rounded-xl"
            disabled={busy}
            onClick={onInstantly}
          >
            {t('sendToInstantly')}
          </Button>
        )}
        <Button
          variant="outline"
          className="h-12.5 grow rounded-xl"
          disabled={busy}
          onClick={onMark}
        >
          {t('markOffTarget')}
        </Button>
      </div>
    );
  }

  return (
    <div
      role="toolbar"
      aria-label={t('barLabel')}
      className="bg-ground shadow-float sticky bottom-6 z-10 inline-flex h-9 items-center gap-1 self-center rounded-lg border pr-1.5 pl-3"
    >
      <span className="pr-2">{label}</span>
      {people && (
        <Button size="xs" disabled={busy} onClick={onAdd}>
          {t('addToCampaign')}
        </Button>
      )}
      {people && (
        <Button
          variant="ghost"
          size="xs"
          disabled={busy}
          onClick={onInstantly}
          className="text-ink"
        >
          {t('sendToInstantly')}
        </Button>
      )}
      <Button variant="ghost" size="xs" disabled={busy} onClick={onMark} className="text-ink">
        {t('markOffTarget')}
      </Button>
      {clear}
    </div>
  );
}
