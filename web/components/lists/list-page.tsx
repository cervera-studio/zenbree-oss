'use client';

import { Download, Send } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AppPage } from '@/components/app/page';
import { usePhone } from '@/components/app/use-phone';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import {
  fetchAllRows,
  type List,
  type ListColumn,
  type ListRow,
  listsApi,
  type RowRating,
} from '@/lib/lists/api';
import { buildColumnModel, entityOf } from '@/lib/lists/column-model';
import { type ColumnSpec, toColumnSpecs } from '@/lib/lists/columns';
import { listChatHref, type RowTab, shownTabs, tabCounts, visibleRows } from '@/lib/lists/view';
import { useCurrentProjectId } from '@/lib/projects';
import { type Result, succeed } from '@/lib/types';
import { AddToCampaignDialog } from './add-to-campaign-dialog';
import { AiColumnDialog } from './ai-column-dialog';
import { BulkBar } from './bulk-bar';
import { ExportDialog } from './export-dialog';
import { InstantlyDialog } from './instantly-dialog';
import { FillFailures, NoRows, settledEmpty } from './list-bits';
import { ListPageHeader, ListTopbarActions } from './list-page-header';
import { LoadFailed } from './loading';
import { PhoneRows } from './phone-rows';
import { RowAside } from './row-aside';
import { FitChip, RowSearch, RowTabs } from './row-toolbar';
import { RowsTable } from './rows-table';
import { SaveViewDialog } from './save-view-dialog';
import { DetailSkeleton } from './skeletons';
import { type MutationKind, type RowsLoader, type SaveChange, useListData } from './use-list-data';
import { useMarkOffTarget } from './use-mark-off-target';
import { useRowSelection } from './use-row-selection';

const NO_COLUMNS: ListColumn[] = [];

const loadAllRows: RowsLoader = async (id) => {
  const all = await fetchAllRows(id);
  return all.success ? succeed({ rows: all.data, next_cursor: null }) : all;
};

export function ListPage({
  listId,
  balance,
  initialRowId = null,
  initialTab = 'all',
  initialQuery = '',
  initialMinFit = null,
}: {
  listId: string;
  balance: number | null;
  /** The row a link from a company's people opens. */
  initialRowId?: string | null;
  /** A saved view's filters (#827), read from the link. */
  initialTab?: RowTab;
  initialQuery?: string;
  initialMinFit?: number | null;
}) {
  const t = useTranslations('lists');
  const errorText = useErrorText();
  const tc = useTranslations('common');
  const phone = usePhone();
  const projectId = useCurrentProjectId();
  // ponytail: this page shows `searching` (an email or phone lookup a running chat turn has open) when it loads,
  // not live: it follows its stream only while an AI column fills. The reading is right, only late. The chat
  // panel refetches on the claim's `list_updated` frame.
  // ponytail: every row is loaded up front (lists cap at a few thousand), so counts, tabs and
  // search are exact and client-side. Page the table and ask the API to filter once lists outgrow that.
  const { list, setList, rows, error, timedOut, refetch, retry, patchRow, save } = useListData(
    listId,
    loadAllRows,
  );
  const [siblings, setSiblings] = useState<List[]>([]);
  const [tab, setTab] = useState<RowTab>(initialTab);
  const [query, setQuery] = useState(initialQuery);
  const [minFit, setMinFit] = useState<number | null>(initialMinFit);
  const [selectedId, setSelectedId] = useState<string | null>(initialRowId);
  const [adding, setAdding] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [picking, setPicking] = useState(false);
  const [sendingOut, setSendingOut] = useState(false);

  const chatSessionId = list?.chat_session_id;
  useEffect(() => {
    if (!projectId || !chatSessionId) return;
    let stale = false;
    void listsApi.all(projectId).then((res) => {
      if (stale || !res.success) return;
      setSiblings(
        res.data.lists.filter((l) => l.chat_session_id === chatSessionId && l.id !== listId),
      );
    });
    return () => {
      stale = true;
    };
  }, [projectId, chatSessionId, listId]);

  const changeRow = useCallback(
    (row: ListRow) => {
      patchRow(row.id, row);
      refetch(); // a first number adds the Telefono column
    },
    [patchRow, refetch],
  );

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
      // Including waits for the server: whether the row lands in Consegnate or Da completare is its call.
      void saveRow(
        row,
        'exclude',
        {
          optimistic: excluded ? { excluded, delivered: false } : undefined,
          revert: { excluded: row.excluded, delivered: row.delivered },
        },
        () => listsApi.setExcluded(listId, row.id, excluded),
      );
    },
    [listId, saveRow],
  );

  const rate = useCallback(
    (row: ListRow, rating: RowRating) => {
      const next = row.rating === rating ? null : rating;
      void saveRow(
        row,
        'rate',
        { optimistic: { rating: next }, revert: { rating: row.rating ?? null } },
        () => listsApi.rate(listId, row.id, next),
      );
    },
    [listId, saveRow],
  );

  const retryFill = useCallback(
    async (key: string) => {
      const res = await listsApi.fillColumn(listId, key);
      if (!res.success) return toast.error(errorText(res.error, t('aiFillError')));
      refetch();
    },
    [listId, refetch, t, errorText],
  );

  // Reversible: gone at once, «Annulla» brings it back with its cells (a refetch).
  const deleteColumn = useCallback(
    async (spec: ColumnSpec) => {
      setList((l) => l && { ...l, columns: l.columns?.filter((c) => c.key !== spec.key) });
      const res = await listsApi.removeColumn(listId, spec.key);
      if (!res.success) {
        refetch();
        return toast.error(t('deleteError'));
      }
      toast.success(t('columnDeleted', { label: spec.label }), {
        action: {
          label: tc('undo'),
          onClick: async () => {
            const undone = await listsApi.restoreColumn(listId, spec.key);
            if (!undone.success) return toast.error(t('restoreError'));
            refetch();
          },
        },
      });
    },
    [listId, refetch, setList, t, tc],
  );

  const closeAside = useCallback(() => {
    if (!selectedId) return;
    setSelectedId(null);
    requestAnimationFrame(() =>
      document.querySelector<HTMLElement>(`[data-row-open="${selectedId}"]`)?.focus(),
    );
  }, [selectedId]);
  const entity = entityOf(list?.entity ?? '');
  const columns = list?.columns ?? NO_COLUMNS;
  const model = useMemo(() => buildColumnModel(columns, entity, t), [columns, entity, t]);
  const specs = useMemo(() => toColumnSpecs(columns, t), [columns, t]);
  const counts = useMemo(() => tabCounts(rows), [rows]);
  // The tab bar drops «Escluse» or «Senza contatto» once empty: fall back to «Tutte» rather than show nothing.
  const activeTab = shownTabs(counts).includes(tab) ? tab : 'all';
  const shown = useMemo(
    () => visibleRows(rows, activeTab, query, minFit),
    [rows, activeTab, query, minFit],
  );
  const selected = rows.find((r) => r.id === selectedId);
  const selection = useRowSelection(shown);
  const marking = useMarkOffTarget({ listId, patchRow, refetch, onMarked: selection.clear });

  if (error === 'LIST.NOT_FOUND') notFound();
  const crumbs = listCrumbs(list, t('title'), t('openChat'));
  if (error || (timedOut && !list) || !list) {
    return (
      <div className="flex min-h-full flex-col">
        <AppPage crumbs={crumbs} crumbsLabel={t('breadcrumb')} busy={!error && !timedOut}>
          {error || timedOut ? <LoadFailed onRetry={retry} code={error} /> : <DetailSkeleton />}
        </AppPage>
      </div>
    );
  }

  const chatHref = listChatHref(list);
  const empty = settledEmpty(list, rows);
  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 grow flex-col">
        <AppPage
          crumbs={crumbs}
          crumbsLabel={t('breadcrumb')}
          topbarActions={
            !phone && (
              <ListTopbarActions
                list={list}
                empty={empty}
                balance={balance}
                onAddColumn={() => setAdding(true)}
                onExport={() => setExporting(true)}
              />
            )
          }
          className="flex-1 phone:gap-3 phone:pb-0"
        >
          <ListPageHeader
            list={list}
            siblings={siblings}
            rows={rows}
            delivered={counts.delivered}
          />
          <FillFailures columns={columns} onRetry={retryFill} />
          {rows.length === 0 ? (
            <NoRows empty={empty} chatHref={chatHref} />
          ) : (
            <>
              <div className="flex flex-col gap-4 phone:gap-3">
                <RowTabs counts={counts} value={activeTab} onChange={setTab} pill={phone} />
                <div className="flex flex-wrap items-center gap-2 phone:flex-col phone:items-stretch">
                  <RowSearch value={query} onChange={setQuery} className="w-70 phone:w-full" />
                  <FitChip minFit={minFit} onChange={setMinFit} />
                  <SaveViewDialog listId={listId} tab={activeTab} query={query} minFit={minFit} />
                </div>
              </div>
              {shown.length === 0 ? (
                <p className="text-muted text-sm">{t('noMatches')}</p>
              ) : phone ? (
                <PhoneRows
                  listId={listId}
                  chatHref={chatHref}
                  columns={columns}
                  onRowChange={changeRow}
                  entity={entity}
                  rows={shown}
                  specs={specs}
                  selection={selection}
                  onRate={rate}
                  onToggleExcluded={toggleExcluded}
                  phoneCredits={list.prices.phone}
                />
              ) : (
                <RowsTable
                  entity={entity}
                  columns={columns}
                  model={model}
                  rows={shown}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  selection={selection}
                  onRate={rate}
                  chatHref={chatHref}
                  onDeleteColumn={deleteColumn}
                />
              )}
              <div className="flex flex-col gap-1 phone:hidden">
                <p className="text-data-sm text-muted font-mono">
                  {t('rowsCount', { n: shown.length })}
                </p>
                <p className="text-muted text-small">{t('requiredFootnote')}</p>
              </div>
            </>
          )}
          {selection.checked.length > 0 ? (
            <BulkBar
              count={selection.checked.length}
              people={entity === 'person'}
              busy={marking.busy}
              phone={phone}
              onAdd={() => setPicking(true)}
              onInstantly={() => setSendingOut(true)}
              onMark={() => void marking.mark(selection.checked)}
              onClear={selection.clear}
            />
          ) : (
            phone && !empty && <PhoneBar list={list} onExport={() => setExporting(true)} />
          )}
        </AppPage>
      </div>
      {!phone && selected && (
        <RowAside
          key={selected.id}
          listId={listId}
          entity={entity}
          specs={specs}
          row={selected}
          onClose={closeAside}
          onToggleExcluded={toggleExcluded}
          onRowChange={changeRow}
          onRate={rate}
          phoneCredits={list.prices.phone}
        />
      )}
      <AiColumnDialog
        listId={listId}
        delivered={counts.delivered}
        balance={balance}
        prices={list.prices}
        open={adding}
        onOpenChange={setAdding}
        onAdded={refetch}
      />
      {picking && (
        <AddToCampaignDialog
          listId={listId}
          rowIds={selection.checked.map((r) => r.id)}
          onClose={() => setPicking(false)}
          onAdded={(result, failed) => {
            if (!failed) selection.clear();
            if (result.skipped.not_found) refetch();
          }}
          onShowTab={setTab}
        />
      )}
      {sendingOut && (
        <InstantlyDialog
          listId={listId}
          rowIds={selection.checked.map((r) => r.id)}
          columns={columns}
          onClose={() => setSendingOut(false)}
        />
      )}
      {exporting && <ExportDialog list={list} rows={rows} onClose={() => setExporting(false)} />}
    </div>
  );
}

function listCrumbs(list: List | null, root: string, chatFallback: string) {
  const crumbs: { label: string; href?: string }[] = [{ label: root, href: '/lists' }];
  if (!list) return crumbs;
  const chatHref = listChatHref(list);
  if (chatHref) crumbs.push({ label: list.chat_title || chatFallback, href: chatHref });
  crumbs.push({ label: list.name });
  return crumbs;
}

function PhoneBar({ list, onExport }: { list: List; onExport: () => void }) {
  const t = useTranslations('lists');
  return (
    <div
      data-phone-bar
      className="bg-ground sticky bottom-0 -mx-4 mt-auto flex gap-2 border-t px-4 pt-3 pb-5"
    >
      <Button variant="outline" className="h-12.5 rounded-xl px-4" onClick={onExport}>
        <Download /> {t('export')}
      </Button>
      {entityOf(list.entity) === 'person' && (
        <Button asChild className="h-12.5 grow rounded-xl">
          <Link href={`/campaigns?list=${encodeURIComponent(list.id)}`}>
            <Send /> {t('startCampaign')}
          </Link>
        </Button>
      )}
    </div>
  );
}
