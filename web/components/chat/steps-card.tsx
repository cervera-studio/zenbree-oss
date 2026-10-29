'use client';

import { Check, CircleAlert, CircleDashed, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { Button } from '@/components/ui/button';
import {
  allStagesFailed,
  failedStage,
  type JobProgress,
  type JobStage,
  overallProgress,
  type StepState,
  stepState,
} from '@/lib/chat/job-progress';
import { cn } from '@/lib/utils';

function StepIcon({ state, label }: { state: StepState; label: string }) {
  if (state === 'done') {
    return <Check aria-label={label} className="text-positive size-3.5 shrink-0" />;
  }
  if (state === 'running') {
    return (
      <Loader2 aria-label={label} className="text-accent-ink size-3.5 shrink-0 animate-spin" />
    );
  }
  if (state === 'failed') {
    return <CircleAlert aria-label={label} className="text-negative size-3.75 shrink-0" />;
  }
  return <CircleDashed role="img" aria-label={label} className="text-muted size-3.5 shrink-0" />;
}

// Units of a stage that is not per row are calls: for a search tool, searches.
function unitOf(stage: JobStage): 'rows' | 'searches' | null {
  if (stage.perRow) return 'rows';
  return stage.tool.startsWith('search_') || stage.tool === 'linkedin_search' ? 'searches' : null;
}

function StepCount({ stage, state }: { stage: JobStage; state: StepState }) {
  const t = useTranslations('chat.steps');
  const unit = unitOf(stage);
  return (
    <span className="text-data-sm text-muted font-mono tabular-nums">
      {state === 'queued' ? t('queued') : t('count', { done: stage.done, units: stage.units })}
      {unit && state !== 'queued' ? ` ${t(`unit.${unit}`, { count: stage.units })}` : ''}
    </span>
  );
}

const NOUN_TOOLS = [
  'search_companies',
  'search_people',
  'linkedin_search',
  'enrich_email',
] as const;

function isNounTool(tool: string): tool is (typeof NOUN_TOOLS)[number] {
  return NOUN_TOOLS.some((name) => name === tool);
}

// Enrichment steps count people, not the email or phone they did not find.
const UNFOUND_TOOLS = ['enrich_email', 'enrich_phone'] as const;

function isUnfoundTool(tool: string): tool is (typeof UNFOUND_TOOLS)[number] {
  return UNFOUND_TOOLS.some((name) => name === tool);
}

function FailedStep({
  stage,
  retrying,
  onRetry,
  listHref,
}: {
  stage: JobStage;
  retrying: boolean;
  onRetry?: () => void;
  listHref?: string;
}) {
  const t = useTranslations('chat.steps');
  const noun = (count: number) => {
    if (unitOf(stage) === 'searches') return t('unit.searches', { count });
    return isNounTool(stage.tool)
      ? t(`noun.${stage.tool}`, { count })
      : t('noun.default', { count });
  };
  const missing = Math.max(stage.units - stage.done, 0);
  return (
    <li className="bg-negative-tint border-negative-tint flex flex-col gap-2 rounded-md border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <StepIcon state="failed" label={t('state.failed')} />
        <span className="text-ui-strong">{stage.label}</span>
        <span className="text-ui-strong text-negative">
          {retrying ? t('retrying') : t('error')}
        </span>
        <span className="grow" />
        <StepCount stage={stage} state="failed" />
      </div>
      <p role="alert" className="text-ui text-soft">
        {t(`errors.${stage.error?.code ?? 'failed'}`, { what: noun(2) })}{' '}
        {isUnfoundTool(stage.tool)
          ? t(`unfound.${stage.tool}`, { count: missing })
          : t('undelivered', { count: missing, what: noun(missing) })}
      </p>
      {!retrying && (onRetry || listHref) && (
        <div className="flex flex-wrap gap-2">
          {onRetry && (
            <Button size="sm" className="phone:h-11" onClick={onRetry}>
              {t('retry')}
            </Button>
          )}
          {listHref && (
            <Button asChild size="sm" variant="outline" className="phone:h-11">
              <Link href={listHref}>{t('openList')}</Link>
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function Step({ stage, state, bare }: { stage: JobStage; state: StepState; bare: boolean }) {
  const t = useTranslations('chat.steps');
  return (
    <li
      className={cn(
        'text-ui flex items-center gap-2',
        bare ? 'border-hair bg-raised h-8.5 rounded-md border px-3' : 'h-6',
        state === 'queued' && 'text-muted',
      )}
    >
      <StepIcon state={state} label={t(`state.${state}`)} />
      <span className={cn('grow', state !== 'queued' && 'text-ui-strong')}>{stage.label}</span>
      <StepCount stage={stage} state={state} />
    </li>
  );
}

/**
 * While a plan runs: the card, with its bar and credits. Once a step has failed and the run has
 * stopped: the steps alone, as the error screen draws them (no card, bar or total around them).
 */
export function StepsCard({
  progress,
  running,
  credits,
  onRetry,
  listHref,
}: {
  progress: JobProgress;
  running: boolean;
  credits: number;
  onRetry?: (stageIndex: number) => void;
  listHref?: string;
}) {
  const t = useTranslations('chat.steps');
  const titleId = useId();
  const { done, total } = overallProgress(progress);
  const failed = failedStage(progress);
  const settledError = failed !== null && !running;
  const allFailed = settledError && allStagesFailed(progress);
  const steps = (
    <ol className={cn('flex flex-col', settledError ? 'gap-2' : 'gap-0.5')}>
      {progress.stages.map((stage, index) => {
        const state = stepState(stage, progress.phase, running);
        const key = `${index}-${stage.tool}`;
        if (state !== 'failed')
          return <Step key={key} stage={stage} state={state} bare={settledError} />;
        return (
          <FailedStep
            key={key}
            stage={stage}
            retrying={running && progress.retryStage === index}
            onRetry={
              onRetry && failed?.index === index && !running && !allFailed
                ? () => onRetry(index)
                : undefined
            }
            listHref={running || allFailed ? undefined : listHref}
          />
        );
      })}
    </ol>
  );
  if (allFailed) {
    return (
      <section aria-label={t('title')} className="text-ui flex flex-col gap-3">
        <p role="alert" className="leading-normal">
          {t('allFailed', { credits })}
        </p>
        {onRetry && failed && (
          <div>
            <Button size="sm" className="phone:h-11" onClick={() => onRetry(failed.index)}>
              {t('retry')}
            </Button>
          </div>
        )}
        <details className="text-muted">
          <summary className="cursor-pointer">{t('details')}</summary>
          <div className="mt-2">{steps}</div>
        </details>
      </section>
    );
  }
  if (settledError) {
    return (
      <section aria-label={t('title')} className="text-ui">
        {steps}
      </section>
    );
  }
  return (
    <section
      aria-labelledby={titleId}
      className="border-line bg-ground flex flex-col gap-2 rounded-lg border p-3"
    >
      <div className="flex items-baseline gap-2">
        <h2 id={titleId} className="text-ui-strong">
          {t('title')}
        </h2>
        <span className="grow" />
        <span className="text-data-sm text-muted font-mono tabular-nums">
          {t('credits', { count: credits })}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={t('progress')}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="bg-hair h-1 overflow-hidden rounded-xs"
      >
        <div
          className="bg-accent h-1 rounded-xs"
          style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }}
        />
      </div>
      {steps}
    </section>
  );
}
