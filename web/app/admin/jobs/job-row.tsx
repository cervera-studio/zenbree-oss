import { ArrowUpRight } from 'lucide-react';
import Link from 'next/link';
import { getFormatter, getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import { Badge, DOT } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Meter } from '@/components/ui/meter';
import { TableCell } from '@/components/ui/table';
import type { Job } from '@/lib/admin/api';
import { formatDuration, formatLastSeen } from '@/lib/admin/format';
import { cn } from '@/lib/utils';
import { JobDisclosure } from './job-disclosure';
import { COLUMNS, JOB_STATUS, stageText, stageTone } from './job-status';

// Over a minute to the first row misses the target the subtitle names.
const SLOW_FIRST_ROW_MS = 60_000;

export async function JobRow({ job: j, open }: { job: Job; open: boolean }) {
  const [t, tAdmin, format, locale, timeZone] = await Promise.all([
    getTranslations('admin.jobs'),
    getTranslations('admin'),
    getFormatter(),
    getLocale(),
    getTimeZone(),
  ]);
  const status = JOB_STATUS[j.status];
  const n = (v: number) => format.number(v, 'credits');
  const now = new Date();
  const nowIso = now.toISOString();
  const firstRowMs =
    j.startedAt && j.firstRowAt ? Date.parse(j.firstRowAt) - Date.parse(j.startedAt) : null;
  const flower = process.env.NEXT_PUBLIC_FLOWER_URL;
  const undelivered = j.result?.undelivered ?? 0;

  const cells = (
    <>
      <TableCell className="text-data-sm text-muted font-mono" title={j.id}>
        {j.id.slice(0, 8)}
      </TableCell>
      <TableCell className="text-ui-strong text-ink max-w-48 truncate">
        {j.email ?? j.userId?.slice(0, 8) ?? tAdmin('formerMember')}
      </TableCell>
      <TableCell>
        <Badge tone={status.tone} dot>
          {t(`statuses.${status.label}`)}
        </Badge>
      </TableCell>
      <TableCell className="text-data-sm text-muted max-w-32 truncate font-mono">
        {j.phase ?? '—'}
      </TableCell>
      <TableCell>
        <span className="flex items-center gap-2">
          <Meter
            value={j.progressTotal ? j.progressDone / j.progressTotal : 0}
            tone={status.meter}
          />
          <span className="text-data-sm font-mono tabular-nums">
            {n(j.progressDone)}/{n(j.progressTotal)}
          </span>
        </span>
      </TableCell>
      <TableCell num>
        {n(j.creditsReserved)} / {n(j.creditsSpent)}
      </TableCell>
      <TableCell className="text-small">
        {formatLastSeen(j.createdAt, { locale, timeZone }, now)}
      </TableCell>
      <TableCell
        num
        className={cn(firstRowMs !== null && firstRowMs > SLOW_FIRST_ROW_MS && 'text-warning')}
      >
        {formatDuration(j.startedAt, j.firstRowAt)}
      </TableCell>
      <TableCell num>{formatDuration(j.startedAt, j.finishedAt ?? nowIso)}</TableCell>
    </>
  );

  return (
    <JobDisclosure
      id={j.id}
      defaultOpen={open}
      label={`${t('details')} ${j.id.slice(0, 8)}`}
      cells={cells}
      colSpan={COLUMNS.length + 1}
    >
      <div className="flex gap-8 px-1.5 py-3 phone:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <p className="text-label text-muted uppercase">{t('brief')}</p>
          <p className="text-ui-strong text-ink">{j.brief || '—'}</p>
          {j.error && (
            <div className="bg-negative-tint text-negative flex flex-col gap-0.5 rounded-md px-3 py-2">
              <p className="text-label uppercase">{t('error')}</p>
              <p className="text-data-sm font-mono break-all">{j.error}</p>
            </div>
          )}
          {undelivered > 0 && (
            <p className="bg-warning-tint text-warning text-small rounded-md px-3 py-2">
              {t('refundNote', { rows: undelivered, credits: j.result?.refunded ?? 0 })}
            </p>
          )}
        </div>
        <div className="flex w-82.5 shrink-0 flex-col gap-2 phone:w-full">
          <p className="text-label text-muted uppercase">{t('stages')}</p>
          {j.stages?.length ? (
            <ul className="flex flex-col">
              {j.stages.map((s, i) => (
                <li key={`${s.tool}-${i}`} className="flex h-5.5 items-center gap-2">
                  <span
                    aria-hidden
                    className={cn('size-1.75 shrink-0 rounded-full', DOT[stageTone(s, j.status)])}
                  />
                  <span className="text-data-sm flex-1 truncate font-mono">
                    {s.label || s.tool}
                  </span>
                  <span className="text-data-sm text-muted font-mono tabular-nums">
                    {stageText(s, n, (from) =>
                      formatDuration(from, s.finishedAt ?? j.finishedAt ?? nowIso),
                    )}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-small text-muted">{t('noStages')}</p>
          )}
          <div className="flex flex-wrap gap-2">
            {j.userId && (
              <Button asChild variant="outline" size="sm">
                <Link href={`/admin/users/${j.userId}`}>{t('openUser')}</Link>
              </Button>
            )}
            {j.chatSessionId && (
              <Button asChild variant="outline" size="sm">
                <Link href={`/admin/chat-sessions/${j.chatSessionId}`}>{t('openChat')}</Link>
              </Button>
            )}
            {flower && (
              <Button asChild variant="outline" size="sm">
                <a href={flower} target="_blank" rel="noreferrer">
                  {t('openFlower')}
                  <ArrowUpRight aria-hidden />
                </a>
              </Button>
            )}
          </div>
        </div>
      </div>
    </JobDisclosure>
  );
}
