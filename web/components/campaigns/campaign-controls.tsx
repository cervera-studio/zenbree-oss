'use client';

import { Pause, Play } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { transitionCampaignAction } from '@/app/(app)/campaigns/actions';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useErrorText } from '@/i18n/error-text';
import type { Campaign } from '@/lib/campaigns/api';
import type { CampaignSenders } from '@/lib/campaigns/senders';
import { hasCopyB } from '@/lib/campaigns/steps';
import { useAction } from '@/lib/hooks/use-action';
import { SenderPicker } from './sender-picker';
import { StepCard } from './step-card';

/** «Metti in pausa» / «Riprendi» on a live campaign, with the API's reason when it refuses. */
export function PauseButton({ campaign }: { campaign: Pick<Campaign, 'id' | 'status'> }) {
  const t = useTranslations('campaigns');
  const errorText = useErrorText();
  const { pending, error, run } = useAction();
  const verb =
    campaign.status === 'active' ? 'pause' : campaign.status === 'paused' ? 'resume' : null;
  if (!verb) return null;
  return (
    <span className="relative">
      <Button
        variant={verb === 'pause' ? 'outline' : 'primary'}
        size="sm"
        className="phone:h-11"
        disabled={pending}
        onClick={() => run(() => transitionCampaignAction(campaign.id, verb))}
      >
        {verb === 'pause' ? <Pause /> : <Play />}
        {t(verb)}
      </Button>
      {error && (
        // Under the topbar button: the topbar has no room for a line of text.
        <p
          role="alert"
          className="bg-negative-tint text-negative text-small absolute top-full right-0 z-10 mt-1 w-max max-w-72 rounded-md px-2.5 py-1.5"
        >
          {errorText(error)}
        </p>
      )}
    </span>
  );
}

/** «Vedi gli step»: the saved steps, read-only once the campaign has left draft. */
export function StepsDialog({ steps }: { steps: Campaign['steps'] }) {
  const t = useTranslations('campaigns');
  const [open, setOpen] = useState(false);
  const abTest = hasCopyB(steps);
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        {t('detail.seeSteps')}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="max-h-4/5 overflow-y-auto sm:max-w-2xl"
          aria-describedby={undefined}
        >
          <DialogHeader>
            <DialogTitle>{t('steps.title')}</DialogTitle>
          </DialogHeader>
          {steps.map((step, i) => (
            <StepCard
              key={i}
              step={step}
              number={i + 1}
              editable={false}
              testing={false}
              abTest={abTest}
              onChange={() => {}}
              onRemove={() => {}}
            />
          ))}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function SendersDialog({
  campaign,
  senders,
}: {
  campaign: Pick<Campaign, 'id' | 'steps'>;
  senders: CampaignSenders | null;
}) {
  const t = useTranslations('campaigns');
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="text-accent-ink text-ui-strong hover:underline"
        onClick={() => setOpen(true)}
      >
        {t('detail.changeSenders')}
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent aria-describedby="senders-description">
          <DialogHeader>
            <DialogTitle>{t('senders.title')}</DialogTitle>
            <p id="senders-description" className="text-muted text-sm">
              {t('senders.description')}
            </p>
          </DialogHeader>
          <SenderPicker
            campaignId={campaign.id}
            pool={senders}
            keepOne={campaign.steps.some((s) => s.channel === 'email')}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
