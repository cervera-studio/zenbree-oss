import { useFormatter, useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import type { RunStep } from '@/lib/automations/api';
import {
  ENROLLED_AS,
  INSTANTLY_SKIPPED,
  known,
  LEFT_OUT,
  mayHaveWritten,
  outputRows,
  reasons,
  SKIP_DECISIONS,
} from '@/lib/automations/run-view';
import { StepStatus, useCodeText, useInstantlyStepText, useWhen } from './labels';

export function RunSteps({ steps }: { steps: RunStep[] }) {
  const t = useTranslations('automations');
  return (
    <section aria-labelledby="steps-title" className="flex flex-col gap-3">
      <h2 id="steps-title" className="text-feature">
        {t('run.steps')}
      </h2>
      <ol className="flex flex-col gap-2">
        {steps.map((step) => (
          <Step key={step.position} step={step} />
        ))}
      </ol>
    </section>
  );
}

function StepError({ step, errorCode }: { step: RunStep; errorCode: string }) {
  const codeText = useCodeText();
  const instantlyText = useInstantlyStepText();
  return (
    <p className="text-negative text-small">
      {step.kind === 'add_to_instantly'
        ? instantlyText(errorCode, step.output.instantlyError)
        : codeText(errorCode)}
    </p>
  );
}

function Step({ step }: { step: RunStep }) {
  const t = useTranslations('automations');
  const format = useFormatter();
  const when = useWhen();
  // The Instantly step counts the people its batch would refuse under its own reasons.
  const leftOut: [string, number][] =
    step.kind === 'add_to_instantly'
      ? reasons(INSTANTLY_SKIPPED, step.output.skipped).map(([r, n]) => [
          t(`instantlySkipped.${r}`),
          n,
        ])
      : reasons(LEFT_OUT, step.output.leftOut).map(([r, n]) => [t(`leftOut.${r}`), n]);
  const enrolledAs = typeof step.output.status === 'string' ? step.output.status : null;
  return (
    <li className="border-line bg-ground flex flex-col gap-2.5 rounded-lg border p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-ui-strong text-ink">
          {t('step.number', { n: step.position + 1 })} · {t(`step.kind.${step.kind}`)}
        </h3>
        <StepStatus status={step.status} />
      </div>
      {step.status === 'uncertain' && (
        <p className="bg-warning-tint text-warning text-small rounded-md px-3 py-2">
          {t('step.uncertain')}
        </p>
      )}
      {step.errorCode && <StepError step={step} errorCode={step.errorCode} />}
      {step.status === 'failed' && mayHaveWritten(step) && (
        <p className="text-muted text-small">{t('step.notRetried')}</p>
      )}
      {step.status === 'skipped' && known(SKIP_DECISIONS, step.decision) && (
        <p className="text-muted text-small">{t(`skipped.${step.decision}`)}</p>
      )}
      <Facts>
        {outputRows(step).map(([key, n]) => (
          <Fact
            key={key}
            label={
              key === 'enrolled' && known(ENROLLED_AS, enrolledAs)
                ? t(`step.enrolledAs.${enrolledAs}`)
                : t(`output.${key}`)
            }
          >
            {format.number(n, 'credits')}
          </Fact>
        ))}
        <Fact label={t('step.credits')}>{format.number(step.credits, 'credits')}</Fact>
        <Fact label={t('step.attempts')}>{format.number(step.attempts, 'credits')}</Fact>
        <Fact label={t('step.started')}>{when(step.startedAt)}</Fact>
        <Fact label={t('step.finished')}>{when(step.finishedAt)}</Fact>
      </Facts>
      {leftOut.length > 0 && (
        <div className="flex flex-col gap-1">
          <h4 className="text-soft text-small font-semibold">
            {t(step.kind === 'add_to_instantly' ? 'step.leftOutInstantly' : 'step.leftOut')}
          </h4>
          <Facts>
            {leftOut.map(([label, n]) => (
              <Fact key={label} label={label}>
                {format.number(n, 'credits')}
              </Fact>
            ))}
          </Facts>
        </div>
      )}
    </li>
  );
}

export function Facts({ children }: { children: ReactNode }) {
  return <dl className="text-small grid grid-cols-2 gap-x-4 gap-y-1">{children}</dl>;
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="text-ink text-right font-mono tabular-nums">{children}</dd>
    </>
  );
}
