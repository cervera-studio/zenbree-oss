'use client';

import { combine } from '@atlaskit/pragmatic-drag-and-drop/combine';
import {
  draggable,
  dropTargetForElements,
} from '@atlaskit/pragmatic-drag-and-drop/element/adapter';
import {
  type ColumnDef,
  columnOrderingFeature,
  flexRender,
  tableFeatures,
  useTable,
} from '@tanstack/react-table';
import { GripVertical, Loader2, Sparkles } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { ListColumn, ListRow, RowRating } from '@/lib/lists/api';
import { type ColumnSpec, reorderColumns } from '@/lib/lists/columns';
import { cn } from '@/lib/utils';
import { Cell } from './list-cells';
import { RateToggles } from './row-cells';
import { RowInclude } from './row-include';

const features = tableFeatures({ columnOrderingFeature });

function ReorderableHead({
  id,
  scope,
  order,
  onMove,
  fill,
  onFill,
  children,
}: {
  id: string;
  scope: string;
  order: string[];
  onMove: (source: string, target: string) => void;
  /** AI columns only: last fill status, and how to fill the missing cells. */
  fill?: string;
  onFill?: () => void;
  children: string;
}) {
  const t = useTranslations('lists');
  const ref = useRef<HTMLTableCellElement>(null);
  const handle = useRef<HTMLSpanElement>(null);
  const [over, setOver] = useState(false);
  const [dragging, setDragging] = useState(false);
  const index = order.indexOf(id);
  const filling = fill === 'queued' || fill === 'running';

  useEffect(() => {
    const element = ref.current;
    const dragHandle = handle.current;
    if (!element || !dragHandle) return;
    return combine(
      draggable({
        element,
        dragHandle,
        getInitialData: () => ({ type: 'list-column', scope, id }),
        onDragStart: () => setDragging(true),
        onDrop: () => setDragging(false),
      }),
      dropTargetForElements({
        element,
        canDrop: ({ source }) =>
          source.data.type === 'list-column' &&
          source.data.scope === scope &&
          typeof source.data.id === 'string' &&
          source.data.id !== id &&
          order.includes(source.data.id),
        onDragEnter: () => setOver(true),
        onDragLeave: () => setOver(false),
        onDrop: ({ source }) => {
          setOver(false);
          if (typeof source.data.id === 'string') onMove(source.data.id, id);
        },
      }),
    );
  }, [id, scope, order, onMove]);

  return (
    <TableHead
      ref={ref}
      scope="col"
      className={cn(over && 'ring-2 ring-inset ring-accent', dragging && 'opacity-40')}
    >
      <div className="flex items-center gap-1">
        <span
          ref={handle}
          aria-hidden="true"
          className="cursor-grab p-1 text-muted active:cursor-grabbing"
        >
          <GripVertical className="size-4" />
        </span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="rounded p-1 hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent"
              aria-label={t('reorderColumn', { column: children })}
            >
              {children}
              {filling && (
                <Loader2
                  className="ml-1 inline size-3 animate-spin"
                  aria-label={t('aiFilling')}
                  role="img"
                />
              )}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem
              disabled={index === 0}
              onSelect={() => onMove(id, order[index - 1] ?? id)}
            >
              {t('moveLeft')}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={index === order.length - 1}
              onSelect={() => onMove(id, order[index + 1] ?? id)}
            >
              {t('moveRight')}
            </DropdownMenuItem>
            {onFill && (
              <DropdownMenuItem disabled={filling} onSelect={onFill}>
                <Sparkles className="size-4" /> {t('aiFill')}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </TableHead>
  );
}

function headAi(spec: ColumnSpec | undefined, onFill?: (key: string) => void) {
  if (spec?.render !== 'ai') return {};
  return { fill: spec.fill, onFill: onFill && (() => onFill(spec.key)) };
}

export function ListTable({
  specs,
  rows,
  onToggleExcluded,
  onFillColumn,
  onRate,
  chatHref,
  columns,
}: {
  specs: ColumnSpec[];
  rows: ListRow[];
  onToggleExcluded: (row: ListRow) => void;
  onFillColumn?: (key: string) => void;
  onRate?: (row: ListRow, rating: RowRating) => void;
  /** The list's chat, where «Chiedi in chat» leads; null without one. */
  chatHref: string | null;
  /** The list's columns, for the required ones a row misses. */
  columns: ListColumn[];
}) {
  const t = useTranslations('lists');
  const scope = useId();
  // ponytail: view-local order; persist per-list preferences when they must survive reopening.
  const [savedOrder, setSavedOrder] = useState<string[]>([]);
  const [announcement, setAnnouncement] = useState('');
  // Keep the user's order when streaming adds columns; new columns go at the end.
  const order = useMemo(() => {
    const keys = specs.map((spec) => spec.key);
    return [
      ...savedOrder.filter((key) => keys.includes(key)),
      ...keys.filter((key) => !savedOrder.includes(key)),
    ];
  }, [specs, savedOrder]);
  const move = useCallback(
    (source: string, target: string) => {
      const next = reorderColumns(order, source, target);
      if (next === order) return;
      setSavedOrder(next);
      setAnnouncement(
        t('columnMoved', {
          column: specs.find((spec) => spec.key === source)?.label ?? source,
          position: next.indexOf(source) + 1,
        }),
      );
    },
    [order, specs, t],
  );
  const defs = useMemo<ColumnDef<typeof features, ListRow>[]>(
    () => [
      {
        id: '__include',
        header: () => <span className="sr-only">{t('exclude')}</span>,
        cell: ({ row }) => <RowInclude row={row.original} onToggle={onToggleExcluded} />,
      },
      ...(onRate
        ? [
            {
              id: '__rating',
              header: () => t('column.rate'),
              cell: ({ row }) => <RateToggles row={row.original} onRate={onRate} />,
            } satisfies ColumnDef<typeof features, ListRow>,
          ]
        : []),
      ...specs.map(
        (spec): ColumnDef<typeof features, ListRow> => ({
          id: spec.key,
          header: spec.label,
          cell: ({ row }) => (
            <Cell spec={spec} row={row.original} chatHref={chatHref} columns={columns} />
          ),
        }),
      ),
    ],
    [specs, onToggleExcluded, onRate, chatHref, columns, t],
  );
  const table = useTable({
    features,
    data: rows,
    columns: defs,
    state: { columnOrder: ['__include', '__rating', ...order] },
    getRowId: (r) => r.id,
  });

  return (
    <>
      <p role="status" className="sr-only">
        {announcement}
      </p>
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((h) =>
                h.id.startsWith('__') ? (
                  <TableHead key={h.id} scope="col">
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </TableHead>
                ) : (
                  <ReorderableHead
                    key={h.id}
                    id={h.id}
                    scope={scope}
                    order={order}
                    onMove={move}
                    {...headAi(
                      specs.find((spec) => spec.key === h.id),
                      onFillColumn,
                    )}
                  >
                    {h.column.columnDef.header as string}
                  </ReorderableHead>
                ),
              )}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((r) => (
            <TableRow
              key={r.id}
              className={cn(
                r.original.excluded
                  ? 'opacity-40'
                  : r.original.delivered === false && 'text-muted italic',
              )}
              title={
                !r.original.excluded && r.original.delivered === false ? t('incomplete') : undefined
              }
            >
              {r.getAllCells().map((c) => (
                <TableCell key={c.id} className="max-w-64 truncate">
                  {flexRender(c.column.columnDef.cell, c.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}
