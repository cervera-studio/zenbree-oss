import { Check, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { type HomeEvent, phaseEvent, type RunRow } from '@/lib/home/live';
import type { RunningSearch } from '@/lib/home/overview';
import { cn } from '@/lib/utils';
import { HomePanel } from './home-panel';

function searchedEntity(run: RunningSearch): 'company' | 'person' {
  return run.entity ?? (run.stageTool === 'search_companies' ? 'company' : 'person');
}

interface RowProps {
  row: RunRow;
  viewerId: string | null;
  onEvent: (event: Extract<HomeEvent, { type: 'held' | 'left' }>) => void;
}

interface RunView {
  title: string;
  href: string;
  meta: string;
  pct: number;
  stepsDone: number;
  stepsTotal: number;
}

function useRunView({ row, viewerId }: Pick<RowProps, 'row' | 'viewerId'>): RunView {
  const t = useTranslations('chat.home.data.running');
  const tc = useTranslations('chat');
  const source = row.phase === 'running' ? row.run : row.done;
  const view = {
    title: source.title || tc('untitled'),
    href: `/?s=${source.sessionId}`,
  };

  if (row.phase !== 'running') {
    const { done } = row;
    const countKey = done.entity === 'company' ? 'doneCompanies' : 'donePeople';
    const meta =
      done.verifiedEmails !== null && countKey === 'donePeople'
        ? t('doneVerified', { count: done.rows, verified: done.verifiedEmails })
        : t(countKey, { count: done.rows });
    return { ...view, meta, pct: 100, stepsDone: done.stagesTotal, stepsTotal: done.stagesTotal };
  }

  const { run } = row;
  const stage =
    run.stageTool && tc.has(`tools.${run.stageTool}` as 'tools.web_search')
      ? tc(`tools.${run.stageTool}` as 'tools.web_search')
      : null;
  const by = run.startedBy
    ? run.startedBy.id === viewerId
      ? t('startedByYou')
      : t('startedBy', { name: run.startedBy.name })
    : null;
  const meta = [
    stage,
    t(searchedEntity(run) === 'company' ? 'companies' : 'people', { count: run.rows }),
    by,
  ]
    .filter(Boolean)
    .join(' · ');
  const pct =
    run.progressTotal > 0
      ? Math.min(100, Math.round((run.progressDone / run.progressTotal) * 100))
      : 0;
  return { ...view, meta, pct, stepsDone: run.stagesDone, stepsTotal: run.stagesTotal };
}

function RunningRow({ row, viewerId, onEvent }: RowProps) {
  const t = useTranslations('chat.home.data.running');
  const { title, href, meta, pct, stepsDone, stepsTotal } = useRunView({ row, viewerId });
  const done = row.phase !== 'running';

  const onRow = (kind: 'start' | 'end') => (e: React.AnimationEvent<HTMLLIElement>) => {
    const event = phaseEvent(row.jobId, kind, e.animationName, e.target === e.currentTarget);
    if (event?.type === 'held' || event?.type === 'left') onEvent(event);
  };

  return (
    <li
      onAnimationStart={done ? onRow('start') : undefined}
      onAnimationEnd={done ? onRow('end') : undefined}
      className={cn('border-hair grid border-b last:border-b-0', done && 'animate-home-leave')}
    >
      <div className="min-h-0 overflow-hidden">
        <div
          className={cn(
            'flex min-h-row items-center gap-3 px-3 py-2 phone:flex-wrap phone:gap-y-2.5',
            done && 'bg-positive-tint',
          )}
        >
          {done ? (
            <span
              aria-hidden="true"
              className="bg-positive text-ground flex size-4.5 shrink-0 items-center justify-center rounded-full"
            >
              <Check className="size-3" strokeWidth={3} />
            </span>
          ) : (
            <Loader2
              aria-hidden="true"
              className="text-accent-ink size-4.5 shrink-0 animate-spin"
            />
          )}
          <div className="flex min-w-0 flex-1 flex-col">
            <Link href={href} className="text-ui-strong truncate hover:underline">
              {title}
            </Link>
            <span className="text-small text-muted">{meta}</span>
          </div>
          <div className="w-55 shrink-0 phone:order-last phone:w-full">
            <div
              role="progressbar"
              aria-label={t('progress', { title })}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
              className="bg-hair h-1 overflow-hidden rounded-xs"
            >
              <div
                className={cn(
                  'ease-snap h-full rounded-xs transition-all duration-progress',
                  done ? 'bg-positive' : 'bg-accent',
                )}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-data-sm text-muted mt-1 block font-mono tabular-nums">
              {t('steps', { done: stepsDone, total: stepsTotal })}
            </span>
          </div>
          <Link
            href={href}
            className="text-accent-ink text-small font-bold whitespace-nowrap hover:underline phone:hidden"
          >
            {t('open')}
          </Link>
        </div>
      </div>
    </li>
  );
}

export function HomeRunning({
  runs,
  viewerId,
  onEvent,
}: {
  runs: RunRow[];
  viewerId: string | null;
  onEvent: RowProps['onEvent'];
}) {
  const t = useTranslations('chat.home.data.running');
  const format = useFormatter();
  if (runs.length === 0) return null;
  return (
    <HomePanel title={t('title')} meta={format.number(runs.length, 'credits')}>
      <ul className="flex flex-col">
        {runs.map((row) => (
          <RunningRow key={row.jobId} row={row} viewerId={viewerId} onEvent={onEvent} />
        ))}
      </ul>
    </HomePanel>
  );
}
