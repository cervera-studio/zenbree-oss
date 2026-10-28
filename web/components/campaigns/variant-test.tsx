'use client';

import { Pause, Play } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { allocationAction } from '@/app/(app)/campaigns/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Label } from '@/components/ui/label';
import { Panel } from '@/components/ui/panel';
import { Select } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { useErrorText } from '@/i18n/error-text';
import type { Campaign } from '@/lib/campaigns/api';
import { hasCopyB, type Step, toApiSteps } from '@/lib/campaigns/steps';
import { useAction } from '@/lib/hooks/use-action';

// The API's scale (`variant_routes.SharePercent`): 10..90 in steps of 10.
const SHARES = [10, 20, 30, 40, 50, 60, 70, 80, 90] as const;

/** The draft's A/B test (#934): the switch, and B's share of the people who join. */
export function VariantTest({
  campaign,
  on,
  steps,
  onSwitch,
}: {
  campaign: Pick<Campaign, 'id' | 'bPercent'>;
  on: boolean;
  steps: Step[];
  onSwitch: (on: boolean) => void;
}) {
  const t = useTranslations('campaignBuilder.variants');
  const errorText = useErrorText();
  const id = useId();
  const [confirmOff, setConfirmOff] = useState(false);
  const [share, setShare] = useState(campaign.bPercent);
  const { pending, error, run } = useAction();

  function pick(next: number) {
    setShare(next);
    run(() => allocationAction(campaign.id, { bPercent: next }), {
      onError: () => setShare(campaign.bPercent),
    });
  }

  return (
    <Panel title={t('title')}>
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2.5">
          <Label htmlFor={`${id}-switch`} className="text-ui-strong text-ink min-w-0 flex-1">
            {t('switch')}
          </Label>
          <Switch
            id={`${id}-switch`}
            checked={on}
            // Turning it off deletes copy B on save: ask first, unless there is none.
            onCheckedChange={(next) =>
              next || !hasCopyB(toApiSteps(steps)) ? onSwitch(next) : setConfirmOff(true)
            }
          />
        </div>
        {on && (
          <>
            <p className="text-small text-muted">{t('help')}</p>
            <div className="space-y-1">
              <Label htmlFor={`${id}-share`} className="text-ui-strong text-ink">
                {t('share')}
              </Label>
              <Select
                id={`${id}-share`}
                value={share}
                disabled={pending}
                onChange={(e) => pick(Number(e.target.value))}
              >
                {SHARES.map((n) => (
                  <option key={n} value={n}>
                    {t('shareOption', { percent: n })}
                  </option>
                ))}
              </Select>
            </div>
            <p className="text-small text-muted">{t('saved')}</p>
          </>
        )}
        {error && (
          <p role="alert" className="text-negative text-small">
            {errorText(error)}
          </p>
        )}
      </div>
      <ConfirmDialog
        open={confirmOff}
        onOpenChange={setConfirmOff}
        title={t('offTitle')}
        description={t('offBody')}
        confirmLabel={t('offConfirm')}
        destructive
        onConfirm={() => {
          setConfirmOff(false);
          onSwitch(false);
        }}
      />
    </Panel>
  );
}

/** A live campaign's A/B test: its share, and a pause that sends copy A to the people who join next. */
export function LiveVariantTest({
  campaign,
}: {
  campaign: Pick<Campaign, 'id' | 'status' | 'steps' | 'bPercent' | 'allocationPausedAt'>;
}) {
  const t = useTranslations('campaigns.variants');
  const errorText = useErrorText();
  const { pending, error, run } = useAction();
  const live = campaign.status === 'active' || campaign.status === 'paused';
  if (!live || !hasCopyB(campaign.steps)) return null;
  const paused = campaign.allocationPausedAt !== null;
  return (
    <Panel
      title={t('title')}
      action={
        <Button
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => run(() => allocationAction(campaign.id, { paused: !paused }))}
        >
          {paused ? <Play /> : <Pause />}
          {t(paused ? 'resume' : 'pause')}
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        <p className="text-ui text-ink">
          {paused ? t('paused') : t('share', { percent: campaign.bPercent })}
        </p>
        <p className="text-small text-muted">{t('kept')}</p>
        {error && (
          <p role="alert" className="text-negative text-small">
            {errorText(error)}
          </p>
        )}
      </div>
    </Panel>
  );
}
