'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { approveRunAction, cancelRunAction } from '@/app/(app)/automations/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useErrorText } from '@/i18n/error-text';
import type { Automation, AutomationRunDetail } from '@/lib/automations/api';
import {
  approval,
  CANCELLABLE,
  cancelOutcome,
  canManage,
  type RunPlan,
  runPlan,
  type Viewer,
} from '@/lib/automations/run-view';
import type { CampaignSummary } from '@/lib/campaigns/api';
import { useAction } from '@/lib/hooks/use-action';
import { RunNow, runsByHand } from './automation-controls';

type Props = {
  run: AutomationRunDetail;
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
  campaignStatus: CampaignSummary['status'] | 'unreadable' | 'none';
  /** Whether the run's Instantly campaign sends once the approval adds people to it, read live. */
  instantlyStatus: 'sends' | 'quiet' | 'unreadable' | 'none';
};

// One campaign system for each automation, so at most one warning applies. A run that holds nobody for Instantly
// warns about the later runs only.
function approvalWarning({ campaignStatus, instantlyStatus }: Props, plan: RunPlan) {
  if (campaignStatus === 'active') return 'activeCampaign';
  if (campaignStatus === 'unreadable') return 'unknownCampaign';
  const nobody = plan.instantly?.nobody;
  if (instantlyStatus === 'sends') return nobody ? 'activeInstantlyNone' : 'activeInstantly';
  if (instantlyStatus === 'unreadable') return nobody ? 'unknownInstantlyNone' : 'unknownInstantly';
  return null;
}

function approveText(plan: RunPlan) {
  if (!plan.instantly) return 'approveText';
  return plan.instantly.nobody ? 'approveTextInstantlyNone' : 'approveTextInstantly';
}

export function RunActions(props: Props) {
  const { run, automation, viewer } = props;
  const t = useTranslations('automations.run');
  const errorText = useErrorText();
  const { pending, error, run: act } = useAction();
  const [done, setDone] = useState('');
  const [confirming, setConfirming] = useState(false);
  const offer = approval(run, automation);
  const manage = canManage(viewer, automation);
  const ended = !CANCELLABLE.includes(run.status);
  const retry = offer === 'retryHubspot' || offer === 'hubspotPending' ? offer : null;
  const plan = runPlan(run, automation);
  const warning = approvalWarning(props, plan);

  function cancel() {
    setDone('');
    return act(() => cancelRunAction(automation.id, run.id), {
      onSuccess: (after) => {
        const outcome = cancelOutcome(after);
        setDone(
          t(outcome, { hubspot: outcome === 'cancelRunning' && plan.hubspot ? 'yes' : 'no' }),
        );
      },
    });
  }

  function approve(message: 'approved' | 'retried') {
    setDone('');
    return act(() => approveRunAction(automation.id, run.id), {
      onSuccess: () => setDone(t(message)),
    });
  }

  return (
    <div className="flex flex-col gap-2">
      {offer === 'outdated' && (
        <p className="text-muted text-small">
          {manage && runsByHand(automation)
            ? `${t('outdated')} ${t('outdatedRunAgain')}`
            : t('outdated')}
        </p>
      )}
      {retry && manage && <p className="text-muted text-small">{t(`${retry}Text`)}</p>}
      {manage && (
        <div className="flex flex-wrap gap-2">
          {offer === 'approve' && (
            <Button className="phone:h-11" disabled={pending} onClick={() => setConfirming(true)}>
              {t('approve')}
            </Button>
          )}
          {retry && (
            <Button
              variant="outline"
              className="phone:h-11"
              disabled={pending}
              onClick={() => approve('retried')}
            >
              {t('retryHubspot')}
            </Button>
          )}
          {CANCELLABLE.includes(run.status) && !run.cancelRequested && (
            <Button variant="outline" className="phone:h-11" disabled={pending} onClick={cancel}>
              {t('cancel')}
            </Button>
          )}
          {ended && runsByHand(automation) && <RunNow automation={automation} label="runAgain" />}
        </div>
      )}
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        destructive={false}
        title={t('approveTitle')}
        description={t(approveText(plan), {
          hubspot: plan.hubspot ? 'yes' : 'no',
          campaign: plan.campaign ? 'yes' : 'no',
        })}
        confirmLabel={t('approve')}
        onConfirm={() => approve('approved')}
      >
        {warning && (
          <p className="bg-warning-tint text-warning text-small rounded-md px-3 py-2">
            {t(warning)}
          </p>
        )}
      </ConfirmDialog>
      {done && (
        <p role="status" className="text-soft text-small">
          {done}
        </p>
      )}
      {error && (
        <p role="alert" className="text-negative text-small">
          {errorText(error)}
        </p>
      )}
    </div>
  );
}
