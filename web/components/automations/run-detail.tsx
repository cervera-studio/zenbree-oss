import { useFormatter, useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import type { Automation, AutomationRunDetail, RunDelivery } from '@/lib/automations/api';
import {
  CANCELLABLE,
  hasApprovableResult,
  INSTANTLY_APPROVABLE,
  isReview,
  REFUSED,
  type RunPlan,
  reasons,
  runPlan,
  stepCredits,
} from '@/lib/automations/run-view';
import { RunStatus, useCodeText, useWhen } from './labels';
import { Fact, Facts } from './run-steps';

function reviewNoteKey(run: AutomationRunDetail, plan: RunPlan) {
  if (!plan.instantly) {
    return CANCELLABLE.includes(run.status) || hasApprovableResult(run) ? 'run.reviewText' : null;
  }
  if (!INSTANTLY_APPROVABLE.includes(run.status)) return null;
  return plan.instantly.nobody ? 'run.reviewTextInstantlyNone' : 'run.reviewTextInstantly';
}

export function RunFacts({
  run,
  automation,
}: {
  run: AutomationRunDetail;
  automation: Pick<Automation, 'approvedVersion' | 'definition'>;
}) {
  const t = useTranslations('automations');
  const when = useWhen();
  const codeText = useCodeText();
  const review = isReview(run, automation);
  const plan = runPlan(run, automation);
  const hubspot = {
    hubspot: plan.hubspot ? 'yes' : 'no',
    campaign: plan.campaign ? 'yes' : 'no',
  };
  const reviewNote = !review || run.approvedAt ? null : reviewNoteKey(run, plan);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <RunStatus status={run.status} />
        <span className="text-muted font-mono text-small">
          {t('version', { version: run.version })}
        </span>
        {review && (
          <Tag tone={run.approvedAt ? 'positive' : 'warning'}>
            {run.approvedAt ? t('approved') : t('runs.review')}
          </Tag>
        )}
      </div>
      {run.cancelRequested && run.status === 'running' && (
        <p role="status" className="text-warning text-small">
          {t('run.stopping', hubspot)}
        </p>
      )}
      {run.errorCode && (
        <p className="text-small flex flex-col">
          <span className="text-muted">{t('run.ended')}</span>
          {codeText(run.errorCode)}
        </p>
      )}
      {reviewNote && (
        <p className="bg-warning-tint text-warning text-small rounded-md px-3 py-2">
          {t(reviewNote, hubspot)}
        </p>
      )}
      <div className="max-w-md">
        <Facts>
          <Fact label={t('run.credits')}>
            {t('runs.credits', { spent: stepCredits(run), cap: run.creditCap })}
          </Fact>
          <Fact label={t('run.created')}>{when(run.createdAt)}</Fact>
          <Fact label={t('run.started')}>{when(run.startedAt)}</Fact>
          <Fact label={t('run.finished')}>{when(run.finishedAt)}</Fact>
          {run.approvedAt && <Fact label={t('approved')}>{when(run.approvedAt)}</Fact>}
        </Facts>
      </div>
    </div>
  );
}

/** Who the run took in and left out before any credit, and what its approval did. Every key is
 *  optional: a run the reaper closed has an empty summary. */
export function RunSummary({ run }: { run: AutomationRunDetail }) {
  const t = useTranslations('automations');
  const format = useFormatter();
  const codeText = useCodeText();
  const { taken, promoted, hubspotAfterApproval } = run.summary;
  const refused = reasons(REFUSED, taken?.refusedBeforeAnyCredit);
  if (!taken && promoted === undefined && !hubspotAfterApproval) return null;
  return (
    <section aria-labelledby="summary-title" className="flex max-w-md flex-col gap-2">
      <h2 id="summary-title" className="text-feature">
        {t('run.summary')}
      </h2>
      <Facts>
        {taken && <Fact label={t('run.taken')}>{format.number(taken.people, 'credits')}</Fact>}
        {promoted !== undefined && run.steps.some((s) => s.kind === 'add_to_campaign') && (
          <Fact label={t('run.promoted')}>{format.number(promoted, 'credits')}</Fact>
        )}
      </Facts>
      {refused.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="text-soft text-small font-semibold">{t('run.refused')}</h3>
          <Facts>
            {refused.map(([reason, n]) => (
              <Fact key={reason} label={t(`refused.${reason}`)}>
                {format.number(n, 'credits')}
              </Fact>
            ))}
          </Facts>
        </div>
      )}
      {hubspotAfterApproval?.errorCode && (
        <p className="text-small flex flex-col">
          <span className="text-muted">{t('run.hubspotAfterApproval')}</span>
          <span className="text-negative">{codeText(hubspotAfterApproval.errorCode)}</span>
        </p>
      )}
    </section>
  );
}

/** The signed call that started a webhook run. It names the call, never the person's email. */
export function RunCall({ delivery }: { delivery: RunDelivery }) {
  const t = useTranslations('automations.run.call');
  const when = useWhen();
  return (
    <section aria-labelledby="call-title" className="flex max-w-md flex-col gap-2">
      <h2 id="call-title" className="text-feature">
        {t('title')}
      </h2>
      <Facts>
        <Fact label={t('webhookId')}>
          <span className="break-all">{delivery.webhookId}</span>
        </Fact>
        <Fact label={t('receivedAt')}>{when(delivery.receivedAt)}</Fact>
        <Fact label={t('matchedBy')}>{t(`by.${delivery.matchedBy}`)}</Fact>
      </Facts>
      {delivery.rowId === null && <p className="text-small text-muted">{t('rowGone')}</p>}
    </section>
  );
}
