import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { Segment } from '@/lib/segments/api';

export function SegmentsIndex({ segments }: { segments: Segment[] }) {
  const t = useTranslations('segments');
  return (
    <StackedTable frame>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t('columns.name')}</TableHead>
          <TableHead scope="col">{t('columns.entity')}</TableHead>
          <TableHead scope="col">{t('columns.members')}</TableHead>
          <TableHead scope="col">{t('columns.state')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {segments.map((s) => (
          <TableRow key={s.id}>
            <TableCell className="text-ui-strong text-ink phone:col-span-2">
              <CellLabel>{t('columns.name')}</CellLabel>
              <Link
                href={`/segments/${s.id}`}
                className="hover:underline phone:flex phone:min-h-11 phone:items-center"
              >
                {s.name}
              </Link>
            </TableCell>
            <TableCell className="text-soft">
              <CellLabel>{t('columns.entity')}</CellLabel>
              {t(`editor.entities.${s.entity}`)}
            </TableCell>
            <TableCell className="text-ink font-mono tabular-nums">
              <CellLabel visible>{t('columns.members')}</CellLabel>
              {s.published_at ? t('memberCount', { n: s.members }) : '—'}
            </TableCell>
            <TableCell>
              <CellLabel>{t('columns.state')}</CellLabel>
              <span className="inline-flex flex-wrap items-center gap-1.5">
                <Tag tone={s.published_at ? 'positive' : 'neutral'}>
                  {s.published_at ? t('state.published') : t('state.draft')}
                </Tag>
                {s.problems.length > 0 && <Tag tone="warning">{t('state.frozen')}</Tag>}
              </span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </StackedTable>
  );
}
