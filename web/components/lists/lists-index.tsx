'use client';

import { List as ListIcon, Plus, Upload } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { type ReactNode, useEffect, useState } from 'react';
import { AppPage } from '@/components/app/page';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { toast } from '@/components/ui/sonner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useErrorText } from '@/i18n/error-text';
import { type List, listsApi } from '@/lib/lists/api';
import { type ListKind, listKind } from '@/lib/lists/view';
import { useCurrentProjectId } from '@/lib/projects';
import { CsvImportDialog } from './csv-import-dialog';
import { IndexCards, IndexCount, IndexTable } from './lists-index-views';
import { LoadFailed, useLoadTimeout } from './loading';
import { IndexSkeleton } from './skeletons';

const KINDS: readonly ListKind[] = ['person', 'company', 'enrich'];

export function ListsIndex() {
  const t = useTranslations('lists');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  const [lists, setLists] = useState<List[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const projectId = useCurrentProjectId();
  const [attempt, setAttempt] = useState(0);
  const timedOut = useLoadTimeout(!lists && !error, attempt);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` is the retry trigger
  useEffect(() => {
    if (!projectId) return;
    let stale = false;
    void listsApi.all(projectId).then((res) => {
      if (stale) return;
      if (res.success) setLists(res.data.lists);
      else setError(res.error.code);
    });
    return () => {
      stale = true;
    };
  }, [projectId, attempt]);

  const retry = () => {
    setError(null);
    setAttempt((n) => n + 1);
  };

  const putBack = (list: List) =>
    setLists((ls) =>
      ls ? [...ls, list].sort((a, b) => b.created_at.localeCompare(a.created_at)) : ls,
    );

  // Reversible: gone at once, «Annulla» restores it with its rows and columns.
  const remove = async (list: List) => {
    setLists((ls) => ls?.filter((l) => l.id !== list.id) ?? ls);
    const res = await listsApi.remove(list.id);
    if (!res.success) {
      putBack(list);
      return toast.error(errorText(res.error, t('deleteError')));
    }
    toast.success(t('deleted'), {
      action: {
        label: tc('undo'),
        onClick: async () => {
          const undone = await listsApi.restore(list.id);
          if (undone.success) putBack(list);
          else toast.error(t('restoreError'));
        },
      },
    });
  };

  const crumbs = [{ label: t('title') }];
  const shell = (children: ReactNode, actions?: ReactNode, meta?: string) => (
    <div className="flex min-h-full flex-col">
      <AppPage
        crumbs={crumbs}
        inProject
        crumbsLabel={t('breadcrumb')}
        topbarActions={actions}
        title={t('title')}
        meta={meta}
        className="flex-1"
        busy={!lists && !error}
      >
        {children}
      </AppPage>
    </div>
  );

  if (lists?.length === 0) {
    return shell(
      <div className="flex flex-1 items-center">
        <EmptyState
          variant="lists"
          icon={ListIcon}
          title={t('emptyTitle')}
          text={t('emptyBody')}
          note={t('csvLimits')}
          action={
            <div className="flex gap-2">
              <Button asChild>
                <Link href="/">
                  <Plus /> {t('newChat')}
                </Link>
              </Button>
              <UploadButton />
            </div>
          }
        />
      </div>,
      undefined,
      t('subtitleShort'),
    );
  }

  return shell(
    error || (timedOut && !lists) ? (
      <LoadFailed onRetry={retry} code={error} />
    ) : lists ? (
      <IndexBody lists={lists} onDelete={remove} />
    ) : (
      <IndexSkeleton />
    ),
    lists && (
      <div className="flex gap-2">
        <UploadButton />
        <Button asChild>
          <Link href="/">
            <Plus /> {t('newList')}
          </Link>
        </Button>
      </div>
    ),
    lists ? t('subtitle') : undefined,
  );
}

/** The type tabs and the lists they filter. A type with no list has no tab. */
function IndexBody({ lists, onDelete }: { lists: List[]; onDelete: (list: List) => void }) {
  const t = useTranslations('lists');
  const format = useFormatter();
  const [tab, setTab] = useState<'all' | ListKind>('all');
  const count = (kind: ListKind) => lists.filter((l) => listKind(l) === kind).length;
  const present = KINDS.filter((k) => count(k) > 0);
  const active = tab === 'all' || present.includes(tab) ? tab : 'all';
  const shown = active === 'all' ? lists : lists.filter((l) => listKind(l) === active);
  return (
    <>
      <Tabs value={active} onValueChange={(v) => setTab(v as 'all' | ListKind)}>
        <TabsList aria-label={t('indexTabsLabel')}>
          <TabsTrigger value="all" count={format.number(lists.length, 'credits')}>
            {t('tab.all')}
          </TabsTrigger>
          {present.map((k) => (
            <TabsTrigger key={k} value={k} count={format.number(count(k), 'credits')}>
              {t(`kind.${k}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={active} className="flex flex-col gap-4">
          <IndexTable lists={shown} onDelete={onDelete} />
          <IndexCards lists={shown} onDelete={onDelete} />
          <IndexCount count={shown.length} />
        </TabsContent>
      </Tabs>
    </>
  );
}

function UploadButton() {
  const t = useTranslations('lists');
  const projectId = useCurrentProjectId();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="outline" disabled={!projectId} onClick={() => setOpen(true)}>
        <Upload /> {t('upload')}
      </Button>
      <CsvImportDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
