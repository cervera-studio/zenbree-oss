import { CircleAlert, Plus } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { Button } from '@/components/ui/button';
import { useErrorText } from '@/i18n/error-text';
import type { blockers } from '@/lib/campaigns/builder';
import { CHANNELS, type Channel, MAX_STEPS, type Step } from '@/lib/campaigns/steps';
import type { AppErrorJSON } from '@/lib/types';
import { Banner } from './banner';

/** Why «Attiva» is disabled, in the design's negative banner; the first reason is in bold. */
export function BuilderBlockers({ blocked }: { blocked: ReturnType<typeof blockers> }) {
  const tb = useTranslations('campaignBuilder');
  if (blocked.length === 0) return null;
  return (
    <Banner tone="negative" icon={CircleAlert} role="alert">
      <p className="flex flex-wrap gap-x-2.5 gap-y-0.5">
        {blocked.map((b, i) => (
          <span key={b} className={i === 0 ? 'text-ui-strong' : 'text-ui'}>
            {tb(`blockers.${b}`)}
            {b === 'noMailbox' && (
              <>
                {' '}
                <Link
                  href="/settings/integrations"
                  className="text-accent-ink text-ui-strong hover:underline"
                >
                  {tb('blockers.noMailboxLink')}
                </Link>
              </>
            )}
          </span>
        ))}
      </p>
    </Banner>
  );
}

export function AddStepButtons({
  steps,
  onAdd,
}: {
  steps: Step[];
  onAdd: (channel: Channel) => void;
}) {
  const tb = useTranslations('campaignBuilder');
  const hintId = useId();
  const disabled = steps.length >= MAX_STEPS;
  const needsInvite = !disabled && !steps.some((s) => s.channel === 'linkedin_connect');
  return (
    <div className="flex flex-col gap-2 pl-14 phone:pl-12">
      <div className="flex flex-wrap gap-2">
        {CHANNELS.map((channel) => {
          const blocked = needsInvite && channel === 'linkedin_message';
          return (
            <Button
              key={channel}
              variant="outline"
              disabled={disabled || blocked}
              aria-describedby={blocked ? hintId : undefined}
              onClick={() => onAdd(channel)}
            >
              <Plus aria-hidden="true" />
              <span className="sr-only">{tb('addStep')} </span>
              {tb(`add.${channel}`)}
            </Button>
          );
        })}
      </div>
      {needsInvite && (
        <p id={hintId} className="text-muted text-ui">
          {tb('needsInvite')}
        </p>
      )}
    </div>
  );
}

export function BuilderError({
  error,
  chips,
}: {
  error: AppErrorJSON;
  chips: { token: string; label: string }[];
}) {
  const errorText = useErrorText();
  const t = useTranslations('campaignBuilder.fields');
  const { waiting, missing } = error.code === 'CAMPAIGN.NO_TARGETS' ? (error.data ?? {}) : {};
  const label = (token: string) => chips.find((c) => c.token === token)?.label ?? `{{${token}}}`;
  return (
    <p role="alert" className="text-negative text-sm">
      {errorText(error)}
      {typeof waiting === 'number' &&
        Array.isArray(missing) &&
        ` ${t('nobodyReady', { count: waiting, fields: missing.map(label).join(', ') })}`}
    </p>
  );
}
