'use client';

import { ArrowLeft, ArrowRight, X } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { ListPanel } from '@/components/lists/list-panel';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { type ChatLists, type ListEntity, parseEntity } from '@/lib/chat/chat-lists';
import { type ChatView, type ListMeta, listMeta, TAB_ORDER } from '@/lib/chat/chat-view';
import type { LiveJob } from '@/lib/chat/job-progress';
import { useCredits } from '@/lib/chat/use-chat';
import type { List } from '@/lib/lists/api';

function MetaText({ meta }: { meta: ListMeta }) {
  const t = useTranslations('chat.panelMeta');
  switch (meta.kind) {
    case 'draft':
      return t('draft');
    case 'base':
      return t('base');
    case 'running':
      return meta.remaining === null
        ? t('runningOpen', { delivered: meta.delivered })
        : t('running', { delivered: meta.delivered, remaining: meta.remaining });
    case 'settled':
      return t('settled', { delivered: meta.delivered, total: meta.total });
  }
}

export function ChatListsPanel({
  lists,
  entity,
  view,
  activeEntity,
  job,
  onSelect,
  onClose,
  onMoreRows,
  onFillEnd,
}: {
  lists: ChatLists;
  entity: ListEntity;
  view: Pick<ChatView, 'status' | 'draftListId'>;
  activeEntity: ListEntity | null;
  job: LiveJob | null;
  onSelect: (entity: ListEntity) => void;
  onClose: () => void;
  onMoreRows?: (message: string) => void;
  onFillEnd?: (credits: number) => void;
}) {
  const t = useTranslations('chat');
  const tKind = useTranslations('lists.kind');
  const credits = useCredits();
  const selected = lists[entity];
  const metaOf = (tab: ListEntity) => (list: List) => (
    <MetaText meta={listMeta(list, tab, { ...view, lists, activeEntity })} />
  );
  return (
    <Tabs
      value={entity}
      onValueChange={(value) => {
        const next = parseEntity(value);
        if (next) onSelect(next);
      }}
      className="h-full min-h-0 gap-0"
    >
      <div className="border-line flex h-12 shrink-0 items-center gap-3 border-b px-4">
        <Button variant="ghost" size="sm" className="-ml-2 px-2 desktop:hidden" onClick={onClose}>
          <ArrowLeft aria-hidden="true" />
          {t('backToChat')}
        </Button>
        <TabsList aria-label={t('listsGroup')} className="border-b-0 phone:hidden">
          {TAB_ORDER.flatMap((e) => {
            const list = lists[e];
            return list
              ? [
                  <TabsTrigger key={e} value={e} count={list.rowCount ?? '—'}>
                    {tKind(e)}
                  </TabsTrigger>,
                ]
              : [];
          })}
        </TabsList>
        <span className="text-ui-strong desktop:hidden">{tKind(entity)}</span>
        <span className="grow" />
        {selected && (
          <Button
            asChild
            size="sm"
            className="phone:border-line phone:bg-ground phone:text-ink phone:hover:bg-hair phone:size-8 phone:has-[>svg]:px-0"
          >
            <Link href={`/lists/${selected.id}`}>
              {/* Icon only on phone: back button, title and a text button do not fit in 390 px. */}
              <span className="phone:sr-only">{t('openFullList')}</span>
              <ArrowRight aria-hidden="true" />
            </Link>
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('hideList')}
          className="phone:hidden"
          onClick={onClose}
        >
          <X />
        </Button>
      </div>
      {TAB_ORDER.flatMap((e) => {
        const list = lists[e];
        return list
          ? [
              <TabsContent key={e} value={e} className="min-h-0">
                <ListPanel
                  key={list.id}
                  listId={list.id}
                  version={list.version}
                  job={job}
                  chatStatus={view.status}
                  onMoreRows={onMoreRows}
                  onFillEnd={onFillEnd}
                  meta={metaOf(e)}
                  balance={credits}
                />
              </TabsContent>,
            ]
          : [];
      })}
    </Tabs>
  );
}
