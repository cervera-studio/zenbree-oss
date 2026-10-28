'use client';

import { Download, MessageSquare, Plus, Send, Sparkles, Users } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useLocale, useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { KpiStrip, type Stat } from '@/components/ui/kpi';
import type { List, ListDetail, ListRow } from '@/lib/lists/api';
import { entityOf } from '@/lib/lists/column-model';
import {
  companyContacts,
  formatCostPerLead,
  listChatHref,
  listKind,
  verifiedEmails,
} from '@/lib/lists/view';
import { ChipFace, chipClass, ListStatusPill } from './list-bits';
import { draftHref, MoreRowsDialog } from './more-rows-dialog';

/** The actions in the topbar. The phone has `PhoneBar` at the foot of the page instead. */
export function ListTopbarActions({
  list,
  empty,
  balance,
  onAddColumn,
  onExport,
}: {
  list: ListDetail;
  /** The search found no one: only «Altre righe» still has something to act on. */
  empty: boolean;
  balance: number | null;
  onAddColumn: () => void;
  onExport: () => void;
}) {
  const t = useTranslations('lists');
  const isPerson = entityOf(list.entity) === 'person';
  const chatHref = listChatHref(list);
  const [moreOpen, setMoreOpen] = useState(false);
  return (
    <div className="flex items-center gap-2">
      {!empty && (
        <Button variant="outline" onClick={onAddColumn}>
          <Sparkles /> {t('aiAdd')}
        </Button>
      )}
      {list.can_expand && chatHref && (
        <Button variant="outline" onClick={() => setMoreOpen(true)}>
          <Plus /> {t('moreRows')}
        </Button>
      )}
      {!empty && (
        <Button variant="outline" onClick={onExport}>
          <Download /> {t('export')}
        </Button>
      )}
      {empty ? null : isPerson ? (
        <Button asChild>
          <Link href={`/campaigns?list=${encodeURIComponent(list.id)}`}>
            <Send /> {t('startCampaign')}
          </Link>
        </Button>
      ) : (
        chatHref && (
          <Button asChild>
            <Link href={draftHref(chatHref, t('findPeopleMessage', { id: list.id }))}>
              <Users /> {t('findPeople')}
            </Link>
          </Button>
        )
      )}
      {chatHref && (
        <MoreRowsDialog
          list={list}
          chatHref={chatHref}
          balance={balance}
          open={moreOpen}
          onOpenChange={setMoreOpen}
        />
      )}
    </div>
  );
}

function useStats(list: ListDetail, rows: ListRow[], delivered: number): Stat[] {
  const t = useTranslations('lists');
  const locale = useLocale();
  const format = useFormatter();
  const credits = list.credits_used;
  const credit = { label: t('kpiCredits'), value: format.number(credits, 'credits') };
  if (entityOf(list.entity) === 'person') {
    const cost = formatCostPerLead(credits, delivered, locale);
    return [
      {
        label: t('kpiDelivered'),
        value: t('kpiOf', { done: delivered, total: list.target_rows ?? list.row_count }),
      },
      { label: t('kpiVerified'), value: format.number(verifiedEmails(rows), 'credits') },
      credit,
      ...(cost === '—' ? [] : [{ label: t('kpiCost'), value: cost }]),
    ];
  }
  const contacts = companyContacts(rows);
  return [
    {
      label: t('kpiCompanies'),
      value: format.number(rows.filter((r) => !r.excluded).length, 'credits'),
    },
    ...(contacts
      ? [
          {
            label: t('kpiWithContact'),
            value: t('kpiOf', { done: contacts.done, total: contacts.total }),
          },
        ]
      : []),
    credit,
  ];
}

export function ListPageHeader({
  list,
  siblings,
  rows,
  delivered,
}: {
  list: ListDetail;
  /** Other lists born from the same chat. */
  siblings: List[];
  rows: ListRow[];
  delivered: number;
}) {
  const t = useTranslations('lists');
  const chatHref = listChatHref(list);
  const stats = useStats(list, rows, delivered);
  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-title phone:text-lede">{list.name}</h1>
          <ListStatusPill status={list.status} />
        </div>
        {list.brief && <p className="text-muted text-small line-clamp-2 max-w-3xl">{list.brief}</p>}
        <p className="text-muted text-small desktop:hidden">
          {[
            t(`kind.${listKind(list)}`),
            t('phoneMeta', { delivered, total: list.target_rows ?? list.row_count }),
            t('phoneCredits', { credits: list.credits_used }),
          ].join(' · ')}
        </p>
        {(chatHref || siblings.length > 0) && (
          <div className="text-muted text-small flex flex-wrap items-center gap-x-4 gap-y-2 phone:hidden">
            {chatHref && (
              <Link href={chatHref} className="inline-flex items-center gap-1.5">
                <MessageSquare className="size-3.5" aria-hidden="true" />
                {t('provenance')}
                <span className="text-accent-ink text-ui-strong">
                  {list.chat_title ? `«${list.chat_title}»` : t('openChat')}
                </span>
              </Link>
            )}
            {siblings.length > 0 && (
              <span className="inline-flex flex-wrap items-center gap-1.5">
                {t('sameChat')}
                {[list, ...siblings].sort(byKind).map((l) => (
                  <SiblingChip key={l.id} list={l} current={l.id === list.id} />
                ))}
              </span>
            )}
          </div>
        )}
      </div>
      <div className="phone:hidden">
        <KpiStrip stats={stats} />
      </div>
    </div>
  );
}

/** Persone before Aziende, whatever the order the chat made them in. */
const KIND_ORDER = ['person', 'company', 'enrich'] as const;
const byKind = (a: List, b: List) =>
  KIND_ORDER.indexOf(listKind(a)) - KIND_ORDER.indexOf(listKind(b));

function SiblingChip({ list, current }: { list: List; current: boolean }) {
  const face = <ChipFace kind={listKind(list)} count={list.row_count} quiet={!current} />;
  return current ? (
    <span aria-current="page" className={chipClass('current')}>
      {face}
    </span>
  ) : (
    <Link href={`/lists/${list.id}`} title={list.name} className={chipClass('plain')}>
      {face}
    </Link>
  );
}
