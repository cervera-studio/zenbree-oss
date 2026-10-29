import { useFormatter, useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { Tag } from '@/components/ui/badge';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { ReportRow } from '@/lib/reports/api';
import { VariantSplit } from './variant-split';

type Lineage = ReportRow['lineage'][number];

const COLUMNS = [
  'contacted',
  'sent',
  'replied',
  'positive',
  'bounced',
  'meetings',
  'deals',
] as const;

export function CampaignReportTable({ rows }: { rows: ReportRow[] }) {
  const t = useTranslations('reports');
  const format = useFormatter();
  const n = (value: number) => format.number(value, 'credits');
  return (
    <StackedTable frame>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t('columns.campaign')}</TableHead>
          {COLUMNS.map((column) => (
            <TableHead key={column} scope="col" num>
              {t(`columns.${column}`)}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const { counts } = row;
          const sent =
            counts.sent.email + counts.sent.linkedinConnect + counts.sent.linkedinMessage;
          return (
            <TableRow key={`${row.sender}:${row.id}`}>
              <TableCell className="align-top phone:col-span-2">
                <CellLabel>{t('columns.campaign')}</CellLabel>
                <span className="inline-flex flex-wrap items-center gap-x-2">
                  <span className="text-ui-strong text-ink">{row.name}</span>
                  <Tag tone="neutral">{t(`sender.${row.sender}`)}</Tag>
                </span>
                {row.lineage.length > 0 && (
                  <details className="text-muted">
                    <summary className="cursor-pointer">{t('lineage.summary')}</summary>
                    <ul className="flex flex-col gap-1 pb-2">
                      {row.lineage.map((line) => (
                        <LineageItem key={lineageKey(line)} line={line} sender={row.sender} />
                      ))}
                    </ul>
                  </details>
                )}
                {row.variants.length > 0 && <VariantSplit variants={row.variants} />}
              </TableCell>
              <MeasureCell label={t('columns.contacted')}>{n(counts.contacted)}</MeasureCell>
              <MeasureCell label={t('columns.sent')}>
                {n(sent)}
                {counts.sent.linkedinConnect + counts.sent.linkedinMessage > 0 && (
                  <span className="text-muted block">
                    {t('channels', {
                      email: counts.sent.email,
                      invites: counts.sent.linkedinConnect,
                      messages: counts.sent.linkedinMessage,
                    })}
                  </span>
                )}
              </MeasureCell>
              <MeasureCell label={t('columns.replied')}>
                {t('replied', { replied: counts.humanReplies, contacted: counts.contacted })}
              </MeasureCell>
              <MeasureCell label={t('columns.positive')}>
                {t('positive', { positive: counts.positiveReplies, replied: counts.humanReplies })}
              </MeasureCell>
              {(['bounced', 'meetings', 'deals'] as const).map((column) => (
                <MeasureCell key={column} label={t(`columns.${column}`)}>
                  <Count value={counts[column]} />
                </MeasureCell>
              ))}
            </TableRow>
          );
        })}
      </TableBody>
    </StackedTable>
  );
}

/** On the phone a measure takes the card's full width, its name on the left and its value on the
 * right: in the card's two columns a long value squeezed the names until they broke mid-word. */
function MeasureCell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <TableCell
      num
      className="phone:col-span-2 phone:flex phone:items-baseline phone:justify-between phone:gap-3"
    >
      <span className="shrink-0 whitespace-nowrap">
        <CellLabel visible>{label}</CellLabel>
      </span>
      <span className="min-w-0 text-right">{children}</span>
    </TableCell>
  );
}

/** A count the report does not measure yet is null: it shows as unknown, never as 0. */
function Count({ value }: { value: number | null }) {
  const t = useTranslations('reports');
  const format = useFormatter();
  if (value !== null) return format.number(value, 'credits');
  return (
    <span className="text-muted">
      {t('unknown')}
      <sup aria-hidden>*</sup>
    </span>
  );
}

function LineageItem({ line, sender }: { line: Lineage; sender: ReportRow['sender'] }) {
  const t = useTranslations('reports.lineage');
  // Only a native row knows the person's source. A null one there is a person deleted since, or a
  // sender who matched no person (a colleague who answered for them).
  const source = sender === 'native' ? t(`source.${line.source ?? 'unknown'}`) : null;
  const signal = line.signal
    ? t('signal', { watchlist: line.signal.watchlistName, type: line.signal.type })
    : null;
  return (
    <li>
      <span className="text-soft">
        {[line.list?.name ?? t('noList'), source, signal].filter(Boolean).join(' · ')}
      </span>
      <span className="block">
        {t('counts', {
          contacted: line.contacted,
          replied: line.humanReplies,
          positive: line.positiveReplies,
        })}
      </span>
    </li>
  );
}

function lineageKey(line: Lineage) {
  return [line.list?.id, line.source, line.signal?.watchlistId, line.signal?.type].join(':');
}
