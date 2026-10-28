import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { Automation } from '@/lib/automations/api';
import { nextIsReview } from '@/lib/automations/run-view';
import { RunStatus, StateLabel, useTriggerText, useWhen } from './labels';

export function AutomationsIndex({ automations }: { automations: Automation[] }) {
  const t = useTranslations('automations');
  const trigger = useTriggerText();
  const when = useWhen();
  return (
    <StackedTable frame>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t('columns.name')}</TableHead>
          <TableHead scope="col">{t('columns.trigger')}</TableHead>
          <TableHead scope="col">{t('columns.state')}</TableHead>
          <TableHead scope="col">{t('columns.version')}</TableHead>
          <TableHead scope="col">{t('columns.lastRun')}</TableHead>
          <TableHead scope="col">{t('columns.nextRun')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {automations.map((a) => (
          <TableRow key={a.id}>
            <TableCell className="text-ui-strong text-ink phone:col-span-2">
              <CellLabel>{t('columns.name')}</CellLabel>
              <Link
                href={`/automations/${a.id}`}
                className="hover:underline phone:flex phone:min-h-11 phone:items-center"
              >
                {a.name}
              </Link>
            </TableCell>
            <TableCell className="text-muted">
              <CellLabel>{t('columns.trigger')}</CellLabel>
              {trigger(a)}
            </TableCell>
            <TableCell>
              <CellLabel>{t('columns.state')}</CellLabel>
              <StateLabel enabled={a.enabled} />
            </TableCell>
            <TableCell className="phone:col-span-2">
              <CellLabel>{t('columns.version')}</CellLabel>
              <span className="inline-flex items-center gap-1.5 phone:flex-wrap">
                <span className="text-muted font-mono">{t('version', { version: a.version })}</span>
                <Tag tone={nextIsReview(a) ? 'warning' : 'positive'}>
                  {nextIsReview(a) ? t('needsReview') : t('approved')}
                </Tag>
              </span>
            </TableCell>
            <TableCell className="phone:col-span-2">
              <CellLabel visible>{t('columns.lastRun')}</CellLabel>
              {a.lastRun ? (
                <span className="inline-flex flex-wrap items-center gap-x-2">
                  <RunStatus status={a.lastRun.status} />
                  <span className="text-muted text-small">{when(a.lastRun.createdAt)}</span>
                </span>
              ) : (
                <span className="text-muted">{t('notRun')}</span>
              )}
            </TableCell>
            <TableCell className="text-muted phone:col-span-2">
              <CellLabel visible>{t('columns.nextRun')}</CellLabel>
              {a.triggerKind === 'manual' ? t('byHand') : a.enabled ? when(a.nextRunAt) : '—'}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </StackedTable>
  );
}
