'use client';

import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { updateCampaignAction } from '@/app/(app)/campaigns/actions';
import { Switch } from '@/components/ui/switch';
import { useErrorText } from '@/i18n/error-text';
import type { Campaign } from '@/lib/campaigns/api';
import { useAction } from '@/lib/hooks/use-action';

export function ParallelCampaigns({
  campaign,
}: {
  campaign: Pick<Campaign, 'id' | 'status' | 'blockActiveDuplicates'>;
}) {
  const t = useTranslations('campaignBuilder.parallel');
  const errorText = useErrorText();
  const id = useId();
  const { pending, error, run } = useAction();
  const shared = !campaign.blockActiveDuplicates;
  const ended = campaign.status === 'stopped' || campaign.status === 'completed';
  return (
    <div className="border-line bg-ground flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex items-center gap-2.5">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <p id={id} className="text-ui-strong text-ink">
            {t('title')}
          </p>
          <p id={`${id}-body`} className="text-small text-muted">
            {t('body')}
          </p>
        </div>
        <Switch
          checked={shared}
          disabled={ended || pending}
          aria-labelledby={id}
          aria-describedby={`${id}-body`}
          onCheckedChange={(on) =>
            run(() => updateCampaignAction(campaign.id, { blockActiveDuplicates: !on }))
          }
        />
      </div>
      {error && (
        <p role="alert" className="text-small text-negative">
          {errorText(error)}
        </p>
      )}
    </div>
  );
}
