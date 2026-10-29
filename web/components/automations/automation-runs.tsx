import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { Automation, AutomationRun } from '@/lib/automations/api';
import { isReview, spentIsFinal } from '@/lib/automations/run-view';
import { RunStatus, useWhen } from './labels';

export function AutomationRuns({
  automation,
  runs,
}: {
  automation: Pick<Automation, 'id' | 'approvedVersion'>;
  runs: AutomationRun[];
}) {
  const t = useTranslations('automations');
  const when = useWhen();
  return (
    <section aria-labelledby="runs-title" className="flex flex-col gap-3">
      <h2 id="runs-title" className="text-feature">
        {t('runs.title')}
      </h2>
      {runs.length === 0 ? (
        <p className="text-muted text-ui">{t('runs.empty')}</p>
      ) : (
        <StackedTable frame>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t('runs.columns.run')}</TableHead>
              <TableHead scope="col">{t('runs.columns.status')}</TableHead>
              <TableHead scope="col">{t('runs.columns.trigger')}</TableHead>
              <TableHead scope="col">{t('runs.columns.version')}</TableHead>
              <TableHead scope="col" num>
                {t('runs.columns.credits')}
              </TableHead>
              <TableHead scope="col">{t('runs.columns.started')}</TableHead>
              <TableHead scope="col">{t('runs.columns.finished')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell className="text-ui-strong text-ink phone:col-span-2">
                  <CellLabel>{t('runs.columns.run')}</CellLabel>
                  <Link
                    href={`/automations/${automation.id}/runs/${run.id}`}
                    className="hover:underline phone:flex phone:min-h-11 phone:items-center"
                  >
                    {t('runs.open', { date: when(run.createdAt) })}
                  </Link>
                </TableCell>
                <TableCell>
                  <CellLabel>{t('runs.columns.status')}</CellLabel>
                  <span className="inline-flex flex-wrap items-center gap-x-2">
                    <RunStatus status={run.status} />
                    {run.cancelRequested && run.status === 'running' && (
                      <span className="text-warning text-small">{t('runs.stopping')}</span>
                    )}
                  </span>
                </TableCell>
                <TableCell className="text-muted">
                  <CellLabel visible>{t('runs.columns.trigger')}</CellLabel>
                  {t(`runs.trigger.${run.triggerKind}`)}
                </TableCell>
                <TableCell>
                  <CellLabel visible>{t('runs.columns.version')}</CellLabel>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="text-muted font-mono">
                      {t('version', { version: run.version })}
                    </span>
                    {isReview(run, automation) && (
                      <Tag tone={run.approvedAt ? 'positive' : 'warning'}>
                        {run.approvedAt ? t('approved') : t('runs.review')}
                      </Tag>
                    )}
                  </span>
                </TableCell>
                <TableCell num className="text-muted">
                  <CellLabel visible>{t('runs.columns.credits')}</CellLabel>
                  {spentIsFinal(run)
                    ? t('runs.credits', { spent: run.creditsSpent, cap: run.creditCap })
                    : t('runs.creditsOnRun')}
                </TableCell>
                <TableCell className="text-muted">
                  <CellLabel visible>{t('runs.columns.started')}</CellLabel>
                  {when(run.startedAt)}
                </TableCell>
                <TableCell className="text-muted">
                  <CellLabel visible>{t('runs.columns.finished')}</CellLabel>
                  {when(run.finishedAt)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </StackedTable>
      )}
    </section>
  );
}
