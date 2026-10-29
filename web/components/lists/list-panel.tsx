'use client';

import { Download, ListPlus, Send, Sparkles } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type ReactNode, useCallback, useContext, useMemo, useState } from 'react';
import { usePhone } from '@/components/app/use-phone';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/sonner';
import { Textarea } from '@/components/ui/textarea';
import { useErrorText } from '@/i18n/error-text';
import type { ChatStatus } from '@/lib/chat/chat-view';
import type { LiveJob } from '@/lib/chat/job-progress';
import { type List, type ListRow, listsApi, type RowRating } from '@/lib/lists/api';
import { entityOf, panelColumnSpecs } from '@/lib/lists/column-model';
import { listChatHref } from '@/lib/lists/view';
import { type Result, succeed } from '@/lib/types';
import { cn } from '@/lib/utils';
import { AiColumnDialog } from './ai-column-dialog';
import { ExportDialog } from './export-dialog';
import { JobProgress } from './job-progress';
import { FillFailures, NoRows, settledEmpty } from './list-bits';
import { ListCards } from './list-cards';
import { filterRows, ROW_FILTERS, type RowFilter } from './list-filter';
import { ListTable } from './list-table';
import { LoadFailed, TableSkeleton } from './loading';
import { PanelSettled } from './panel-settled';
import { type MutationKind, type RowsLoader, type SaveChange, useListData } from './use-list-data';

const PAGE = 100;

const loadPage: RowsLoader = (id, cursor) => listsApi.rows(id, cursor, PAGE);

/**
 * One List: header, job progress, rows table. `version` changes on every
 * `list_updated` SSE frame and triggers a refetch of the first page; AI column
 * fills arrive on their own stream (#252).
 * `onMoreRows` (chat only) asks the agent for the next page of the list's search.
 */
export function ListPanel({
  listId,
  version = 0,
  job,
  chatStatus,
  onMoreRows,
  onFillEnd,
  meta,
  balance,
}: {
  listId: string;
  version?: number;
  /** The chat turn's job, live from the chat store (#265). */
  job?: LiveJob | null;
  chatStatus: ChatStatus | null;
  onMoreRows?: (message: string) => void;
  /** A fill of this list ended with what it charged (the chat header counts it, #239). */
  onFillEnd?: (credits: number) => void;
  meta: (list: List) => ReactNode;
  balance: number | null;
}) {
  const t = useTranslations('lists');
  const errorText = useErrorText();
  const { list, rows, cursor, error, timedOut, refetch, retry, loadMore, save } = useListData(
    listId,
    loadPage,
    { reloadKey: version, onFillEnd },
  );
  const [adding, setAdding] = useState(false);
  // Row rated bad: the optional note saying what is wrong with it.
  const [noteRow, setNoteRow] = useState<ListRow | null>(null);
  const [note, setNote] = useState('');
  const [exporting, setExporting] = useState(false);
  // Phone (#171): cards instead of the table, with filters over the loaded rows.
  const phone = usePhone();
  const settled = useContext(PanelSettled);
  const [query, setQuery] = useState('');
  const [show, setShow] = useState<RowFilter>('all');
  const specs = useMemo(
    () => (list ? panelColumnSpecs(list.columns ?? [], entityOf(list.entity), t) : []),
    [list, t],
  );

  const fillColumn = useCallback(
    async (key: string) => {
      const res = await listsApi.fillColumn(listId, key);
      if (!res.success) return toast.error(errorText(res.error, t('aiFillError')));
      refetch();
    },
    [listId, refetch, t, errorText],
  );

  const showMore = async () => {
    const res = await loadMore();
    if (res && !res.success) toast.error(t('error'));
  };

  const saveRow = useCallback(
    async (
      row: ListRow,
      kind: MutationKind,
      change: SaveChange,
      request: () => Promise<Result<Partial<ListRow>>>,
    ) => {
      const res = await save(row, kind, change, request);
      if (res && !res.success) toast.error(t('updateError'));
    },
    [save, t],
  );

  const toggleExcluded = useCallback(
    (row: ListRow) => {
      const excluded = !row.excluded;
      void saveRow(
        row,
        'exclude',
        { optimistic: { excluded }, revert: { excluded: row.excluded } },
        async () => {
          const res = await listsApi.setExcluded(listId, row.id, excluded);
          // Only the fields this save owns: a fill may have written the row's cells meanwhile.
          return res.success
            ? succeed({ excluded: res.data.excluded, delivered: res.data.delivered })
            : res;
        },
      );
    },
    [listId, saveRow],
  );

  const saveRating = useCallback(
    (row: ListRow, rating: RowRating | null, ratingNote?: string) =>
      saveRow(row, 'rate', { revert: {} }, () => listsApi.rate(listId, row.id, rating, ratingNote)),
    [listId, saveRow],
  );

  const rate = useCallback(
    (row: ListRow, rating: RowRating) => {
      // Same thumb again clears it; a bad rating offers a note.
      const next = row.rating === rating ? null : rating;
      void saveRating(row, next);
      if (next === 'bad') {
        setNote('');
        setNoteRow(row);
      }
    },
    [saveRating],
  );

  if (error || (timedOut && !list)) return <LoadFailed onRetry={retry} code={error} />;
  if (!list) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <div className="bg-hair h-4 w-48 animate-pulse rounded" />
        <div className="bg-hair h-3 w-32 animate-pulse rounded" />
        <TableSkeleton columns={5} rows={8} />
      </div>
    );
  }

  const chatHref = listChatHref(list);
  // The panel refetches the list on its first new row only, so a turn that runs again still reads `empty`.
  const empty = settledEmpty(list, rows) && chatStatus !== 'running';

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="bg-ground/70 flex flex-col gap-3 border-b px-4 py-4">
        <div className="flex items-center gap-2 phone:flex-wrap">
          <div className="min-w-0 flex-1 phone:basis-full">
            <p className="text-muted truncate text-xs-plus">{meta(list)}</p>
          </div>
          {onMoreRows && list.can_expand && (
            <Button
              size="sm"
              variant="outline"
              className={PHONE_BUTTON}
              onClick={() => onMoreRows(t('moreRowsMessage', { id: list.id }))}
            >
              <ListPlus /> {t('moreRows')}
            </Button>
          )}
          {!empty && (
            <>
              <Button
                size="sm"
                variant="outline"
                className={PHONE_BUTTON}
                onClick={() => setAdding(true)}
              >
                <Sparkles /> {t('aiAdd')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className={PHONE_BUTTON}
                onClick={() => setExporting(true)}
              >
                <Download /> {t('export')}
              </Button>
            </>
          )}
        </div>
        {job && <JobProgress job={job} chatStatus={chatStatus} />}
        <FillFailures columns={list.columns ?? []} />
        {phone && rows.length > 0 && (
          <search aria-label={t('filters')} className="flex flex-col gap-2">
            <Input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('filterSearch')}
              aria-label={t('filterSearch')}
              className="h-11"
            />
            <div className="flex gap-2">
              {ROW_FILTERS.map((f) => (
                <Button
                  key={f}
                  size="sm"
                  variant="outline"
                  aria-pressed={show === f}
                  onClick={() => setShow(f)}
                  className={cn(
                    'h-11 rounded-full',
                    show === f && 'border-accent/40 bg-accent-tint text-accent-ink',
                  )}
                >
                  {t(`filter.${f}`)}
                </Button>
              ))}
            </div>
          </search>
        )}
      </div>
      {exporting && <ExportDialog list={list} onClose={() => setExporting(false)} />}
      <AiColumnDialog
        listId={listId}
        delivered={list.delivered_count}
        balance={balance}
        prices={list.prices}
        open={adding}
        onOpenChange={setAdding}
        onAdded={refetch}
      />
      <Dialog open={noteRow !== null} onOpenChange={(open) => !open && setNoteRow(null)}>
        <DialogContent>
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (noteRow) void saveRating(noteRow, 'bad', note.trim());
              setNoteRow(null);
            }}
          >
            <DialogHeader>
              <DialogTitle>{t('rateBadTitle')}</DialogTitle>
            </DialogHeader>
            <div className="grid gap-2">
              <Label htmlFor="row-rating-note">{t('rateNote')}</Label>
              <Textarea
                id="row-rating-note"
                autoFocus
                value={note}
                maxLength={500}
                placeholder={t('rateNotePlaceholder')}
                onChange={(e) => setNote(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={!note.trim()}>
                {t('rateNoteSave')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <div className="min-h-0 flex-1 overflow-auto">
        {!settled ? (
          <TableSkeleton columns={5} rows={8} />
        ) : rows.length === 0 ? (
          <NoRows empty={empty} className="p-4" />
        ) : (
          <>
            {phone ? (
              <PhoneRows
                specs={specs}
                rows={filterRows(rows, specs, query, show)}
                onToggleExcluded={toggleExcluded}
                onRate={rate}
                chatHref={chatHref}
                columns={list.columns ?? []}
              />
            ) : (
              <ListTable
                key={list.id}
                specs={specs}
                rows={rows}
                onToggleExcluded={toggleExcluded}
                onFillColumn={fillColumn}
                onRate={rate}
                chatHref={chatHref}
                columns={list.columns ?? []}
              />
            )}
            {cursor !== null && (
              <div className="p-3 text-center">
                <Button size="sm" variant="ghost" className={PHONE_BUTTON} onClick={showMore}>
                  {t('loadMore')}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
      {list.entity === 'person' && !empty && (
        // Phone only: a people list's next step, always in reach (#171).
        <div className="shrink-0 border-t p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] desktop:hidden">
          <Button asChild className="h-11 w-full">
            <Link href={`/campaigns?list=${encodeURIComponent(list.id)}`}>
              <Send /> {t('startCampaign')}
            </Link>
          </Button>
        </div>
      )}
    </div>
  );
}

const PHONE_BUTTON = 'phone:h-11';

function PhoneRows(props: Parameters<typeof ListCards>[0]) {
  const t = useTranslations('lists');
  if (props.rows.length === 0) return <p className="text-muted p-4 text-sm">{t('noMatches')}</p>;
  return <ListCards {...props} />;
}
