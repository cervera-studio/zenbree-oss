'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import type { ChatStatus } from '@/lib/chat/chat-view';
import { visiblePhase } from '@/lib/chat/hidden-tools';
import { type LiveJob, liveJob } from '@/lib/chat/job-progress';
import { type Job, listsApi, TERMINAL_JOB } from '@/lib/lists/api';
import { cn } from '@/lib/utils';

const CHAT_FAILED: Partial<Record<ChatStatus, 'stepError' | 'allFailed'>> = {
  step_error: 'stepError',
  all_failed: 'allFailed',
};

/**
 * The chat turn's job: one `GET /v1/jobs/{id}` when the turn starts and one when it ends, live
 * stages in between from the chat store, which follows the job's SSE stream (#265). The API marks a job
 * `succeeded` even when one of its steps failed, so a finished job reads the chat's status.
 */
// ponytail: a turn the user stopped (`stop` only detaches the stream) or never re-attached to shows
// the snapshot read at that moment until the chat reloads, like the steps card.
export function JobProgress({
  job: live,
  chatStatus,
}: {
  job: LiveJob;
  chatStatus: ChatStatus | null;
}) {
  const t = useTranslations('lists.job');
  const tChat = useTranslations('chat.status');
  const [snapshot, setSnapshot] = useState<Job | null>(null);
  const { id, streaming } = live;

  // biome-ignore lint/correctness/useExhaustiveDependencies: `streaming` flipping is the refetch trigger (turn start/end)
  useEffect(() => {
    let stop = false;
    void listsApi.job(id).then((res) => {
      if (!stop && res.success) setSnapshot(res.data);
    });
    return () => {
      stop = true;
    };
  }, [id, streaming]);

  if (!snapshot || snapshot.id !== id) return null;
  const job = liveJob(snapshot, live);
  if (!job) return null;
  const { done, total } = job.progress;
  const pct = total ? Math.min(100, Math.round(((done ?? 0) / total) * 100)) : null;
  const running = !TERMINAL_JOB.has(job.status);
  const phase = visiblePhase(job.phase);
  // the first unfinished stage is the one running (#49)
  const current = running ? (job.stages?.findIndex((s) => s.done < s.units) ?? -1) : -1;
  const failed = running || !chatStatus ? undefined : CHAT_FAILED[chatStatus];
  const status = failed
    ? tChat(failed)
    : t.has(job.status as 'running')
      ? t(job.status as 'running')
      : job.status;

  return (
    <div className="flex flex-col gap-1 text-xs">
      <div className="text-muted flex flex-wrap gap-x-3">
        <span className={job.status === 'failed' || failed ? 'text-negative' : 'text-ink'}>
          {status}
          {running && phase ? ` · ${phase}` : ''}
        </span>
        {total ? (
          <span>{t('progress', { done: done ?? 0, total })}</span>
        ) : done ? (
          <span>{t('steps', { done })}</span>
        ) : null}
        {job.credits.spent ? <span>{t('credits', { spent: job.credits.spent })}</span> : null}
      </div>
      {job.stages && job.stages.length > 0 && (
        <ol className="flex flex-col gap-0.5" aria-label={t('stages')}>
          {job.stages.map((s, i) => {
            const finished = s.done >= s.units;
            return (
              <li
                key={`${i}-${s.tool}`}
                aria-current={i === current ? 'step' : undefined}
                className={cn(
                  'flex items-center gap-2',
                  i === current ? 'text-ink font-medium' : 'text-muted',
                )}
              >
                <span aria-hidden className={cn('w-3', finished && 'text-accent')}>
                  {finished ? '✓' : i === current ? '●' : '○'}
                </span>
                <span>{s.label}</span>
                <span className="tabular-nums">
                  {t('progress', { done: Math.min(s.done, s.units), total: s.units })}
                </span>
              </li>
            );
          })}
        </ol>
      )}
      {running && (
        <div className="bg-hair h-1 overflow-hidden rounded-full">
          <div
            className={`bg-accent h-full transition-all ${pct === null ? 'w-1/3 animate-pulse' : ''}`}
            style={pct === null ? undefined : { width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
