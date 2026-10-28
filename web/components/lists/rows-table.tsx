'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { ListColumn, ListRow, RowRating } from '@/lib/lists/api';
import { type ColumnModel, type Entity, identity } from '@/lib/lists/column-model';
import type { ColumnSpec } from '@/lib/lists/columns';
import { cn } from '@/lib/utils';
import { DeleteMenu } from './delete-menu';
import { AiCell, Cell } from './list-cells';
import { COMPOSITE_CELLS, RateToggles } from './row-cells';
import { RowPeople } from './row-people';
import type { RowSelection } from './use-row-selection';

/** The desktop table: composite columns first, then the list's own columns, then Valuta. */
export function RowsTable({
  entity,
  columns,
  model,
  rows,
  selectedId,
  onSelect,
  selection,
  onRate,
  chatHref,
  onDeleteColumn,
}: {
  entity: Entity;
  /** The list's columns, for the required ones a row misses. */
  columns: ListColumn[];
  model: ColumnModel;
  rows: ListRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** The checkboxes of the bulk actions; `onSelect` opens a row in the aside. */
  selection: RowSelection;
  onRate: (row: ListRow, rating: RowRating) => void;
  /** The list's chat, where «Chiedi in chat» leads; null without one. */
  chatHref: string | null;
  /** AI columns only, while no fill runs: the user's own columns. */
  onDeleteColumn: (spec: ColumnSpec) => void;
}) {
  const t = useTranslations('lists');
  const people = rows.some((r) => r.people_count !== undefined);
  return (
    <Table frame stickyHeader className="min-w-265">
      <TableHeader>
        <TableRow>
          <TableHead scope="col" className="w-8">
            <Checkbox
              checked={selection.all}
              onCheckedChange={selection.toggleAll}
              aria-label={t('bulk.selectAll')}
              className="align-middle"
            />
          </TableHead>
          {model.composites.map((c) => (
            <TableHead key={c.id} scope="col" className={cn(c.id === 'fit' && 'w-14')}>
              {t(c.labelKey)}
              {c.required && <span className="text-accent-ink"> *</span>}
            </TableHead>
          ))}
          {people && <TableHead scope="col">{t('column.people')}</TableHead>}
          {model.rest.map((s) => {
            const filling = s.fill === 'queued' || s.fill === 'running';
            return (
              <TableHead key={s.key} scope="col">
                <span className="inline-flex items-center gap-1.5">
                  {s.label}
                  {filling && (
                    <Loader2
                      className="size-3 animate-spin"
                      role="img"
                      aria-label={t('aiFilling')}
                    />
                  )}
                  {s.render === 'ai' && !filling && (
                    <DeleteMenu
                      label={t('columnMenu', { label: s.label })}
                      item={t('deleteColumn', { label: s.label })}
                      onDelete={() => onDeleteColumn(s)}
                    />
                  )}
                </span>
              </TableHead>
            );
          })}
          <TableHead scope="col" className="w-21">
            {t('column.rate')}
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow
            key={row.id}
            className={cn(
              (row.id === selectedId || selection.has(row.id)) &&
                'bg-accent-tint hover:bg-accent-tint',
              row.excluded && 'opacity-50',
            )}
          >
            <TableCell>
              <Checkbox
                checked={selection.has(row.id)}
                onCheckedChange={() => selection.toggle(row.id)}
                aria-label={t('bulk.selectRow', {
                  name: identity(row, entity).name || t('unnamed'),
                })}
                className="align-middle"
              />
            </TableCell>
            {model.composites.map((c) => {
              const Composite = COMPOSITE_CELLS[c.id];
              return (
                <TableCell key={c.id}>
                  <Composite
                    row={row}
                    entity={entity}
                    onOpen={() => onSelect(row.id)}
                    chatHref={chatHref}
                    columns={columns}
                  />
                </TableCell>
              );
            })}
            {people && (
              <TableCell className="whitespace-normal">
                <div className="py-1.5">
                  <RowPeople row={row} chatHref={chatHref} />
                </div>
              </TableCell>
            )}
            {model.rest.map((s) => (
              <TableCell
                key={s.key}
                className={cn(s.render === 'ai' && 'w-40 max-w-40 whitespace-normal')}
              >
                {s.render === 'ai' ? (
                  <div className="py-1.5">
                    <AiCell spec={s} row={row} wrap />
                  </div>
                ) : (
                  <Cell spec={s} row={row} chatHref={chatHref} columns={columns} />
                )}
              </TableCell>
            ))}
            <TableCell>
              <RateToggles row={row} onRate={onRate} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
