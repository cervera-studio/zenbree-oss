import { Check, Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { type PhaseStatus, type StudyCommand, type StudyPhase, studyPhases } from './wizard';

export function Analysing({ study, onCancel }: { study: StudyCommand; onCancel: () => void }) {
  const t = useTranslations('onboarding.company');
  const panel = useRef<HTMLElement>(null);
  // It replaces the form, taking the submit button and its focus with it.
  useEffect(() => panel.current?.focus(), []);
  return (
    <section
      ref={panel}
      tabIndex={-1}
      aria-label={t('busyLabel')}
      className="flex flex-col gap-4 rounded-lg border border-line bg-ground p-5 outline-none"
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="max-w-full truncate rounded-sm bg-raised px-2.5 py-1 font-mono text-data tabular-nums">
          {study.input.website ?? t('fromDescription')}
        </span>
        <span className="flex items-center gap-2 text-ui text-soft">
          <Loader2 className="size-4 animate-spin text-accent-ink" aria-hidden="true" />
          {t('studying')}
        </span>
      </div>
      <ol aria-live="polite" className="flex flex-col gap-3 text-ui text-ink">
        {studyPhases(study).map((phase) => (
          <PhaseRow key={phase.id} phase={phase} />
        ))}
      </ol>
      <div className="flex justify-end">
        <Button type="button" variant="outline" onClick={onCancel}>
          {study.input.website ? t('changeSite') : t('editDescription')}
        </Button>
      </div>
    </section>
  );
}

function PhaseRow({ phase }: { phase: StudyPhase }) {
  const t = useTranslations('onboarding.company');
  const result =
    phase.id === 'site' && phase.pages > 0
      ? t('pagesRead', { n: phase.pages })
      : phase.id === 'offer'
        ? phase.summary
        : null;
  return (
    <li className="flex items-center gap-3">
      <PhaseIcon status={phase.status} />
      <span className="sr-only">{t(`phaseStatus.${phase.status}`)}</span>
      <span className={cn('shrink-0', phase.status === 'pending' && 'text-soft')}>
        {t(`phases.${phase.id}`)}
      </span>
      {result && (
        <span title={result} className="ml-auto min-w-0 truncate text-small text-muted">
          {result}
        </span>
      )}
    </li>
  );
}

function PhaseIcon({ status }: { status: PhaseStatus }) {
  if (status === 'done') {
    return <Check aria-hidden="true" className="size-4 shrink-0 text-positive" />;
  }
  if (status === 'running') {
    return <Loader2 aria-hidden="true" className="size-4 shrink-0 animate-spin text-accent-ink" />;
  }
  return (
    <span aria-hidden="true" className="flex size-4 shrink-0 items-center justify-center">
      <span className="size-1.5 rounded-full bg-accent-ink/60" />
    </span>
  );
}
