'use client';

import { Pause, Play } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { runNowAction, setEnabledAction } from '@/app/(app)/automations/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useErrorText } from '@/i18n/error-text';
import type { Automation, AutomationRun } from '@/lib/automations/api';
import { canManage, nextIsReview, type Viewer } from '@/lib/automations/run-view';
import { useAction } from '@/lib/hooks/use-action';

type Props = {
  automation: Pick<
    Automation,
    | 'id'
    | 'name'
    | 'enabled'
    | 'creditCap'
    | 'version'
    | 'approvedVersion'
    | 'userId'
    | 'triggerKind'
    | 'definition'
  >;
  viewer: Viewer;
};

/** A webhook automation runs on its signed calls only, a signal automation on its signals: the API refuses Run now. */
export const runsByHand = (a: Pick<Automation, 'enabled' | 'triggerKind'>) =>
  a.enabled && a.triggerKind !== 'webhook' && a.triggerKind !== 'signal';

export function AutomationControls({ automation, viewer }: Props) {
  if (!canManage(viewer, automation)) return null;
  return (
    <div className="flex flex-wrap items-start gap-2">
      <PauseToggle automation={automation} />
      {runsByHand(automation) && <RunNow automation={automation} label="runNow" />}
    </div>
  );
}

function PauseToggle({ automation }: Pick<Props, 'automation'>) {
  const t = useTranslations('automations.controls');
  const errorText = useErrorText();
  const { pending, error, run } = useAction();
  const enabled = automation.enabled;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant={enabled ? 'outline' : 'primary'}
        className="phone:h-11"
        disabled={pending}
        onClick={() =>
          run(() => setEnabledAction(automation.id, !enabled), {
            successToast: t(enabled ? 'paused' : 'resumed'),
          })
        }
      >
        {enabled ? <Pause aria-hidden /> : <Play aria-hidden />}
        {t(enabled ? 'pause' : 'resume')}
      </Button>
      {error && (
        <p role="alert" className="text-negative text-small max-w-72">
          {errorText(error)}
        </p>
      )}
    </div>
  );
}

const has = (
  a: Pick<Automation, 'definition'>,
  type: Automation['definition']['steps'][number]['type'],
) => a.definition.steps.some((s) => s.type === type);

export function RunNow({
  automation,
  label,
}: Pick<Props, 'automation'> & { label: 'runNow' | 'runAgain' }) {
  const t = useTranslations('automations');
  const errorText = useErrorText();
  const { error, run } = useAction();
  const [open, setOpen] = useState(false);
  const [started, setStarted] = useState<{ run: AutomationRun; alreadyGoing: boolean } | null>(
    null,
  );
  const cap = { cap: automation.creditCap };
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant={label === 'runNow' ? 'primary' : 'outline'}
        className="phone:h-11"
        onClick={() => {
          setStarted(null);
          setOpen(true);
        }}
      >
        <Play aria-hidden />
        {t(`controls.${label}`)}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        destructive={false}
        title={t('confirmRun.title', { name: automation.name })}
        description={t('confirmRun.cap', cap)}
        consequences={
          nextIsReview(automation)
            ? [
                has(automation, 'add_to_instantly')
                  ? t('confirmRun.reviewInstantly', {
                      version: automation.version,
                      hubspot: has(automation, 'write_hubspot') ? 'yes' : 'no',
                    })
                  : t('confirmRun.review', {
                      version: automation.version,
                      hubspot: has(automation, 'write_hubspot') ? 'yes' : 'no',
                      campaign: has(automation, 'add_to_campaign') ? 'yes' : 'no',
                    }),
              ]
            : []
        }
        confirmLabel={t('controls.runNow')}
        onConfirm={() => run(() => runNowAction(automation.id), { onSuccess: setStarted })}
      />
      {started && (
        <p role="status" className="text-small text-soft max-w-72 text-right">
          {t(started.alreadyGoing ? 'confirmRun.alreadyGoing' : 'confirmRun.queued')}{' '}
          <Link
            href={`/automations/${automation.id}/runs/${started.run.id}`}
            className="text-accent-ink hover:underline"
          >
            {t('run.open')}
          </Link>
        </p>
      )}
      {error && (
        <p role="alert" className="text-negative text-small max-w-72">
          {errorText(error)}
        </p>
      )}
    </div>
  );
}
