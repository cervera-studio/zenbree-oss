'use client';

import Link from 'next/link';
import { useLocale, useTranslations } from 'next-intl';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { List } from '@/lib/lists/api';
import { formatCostPerLead, listKind } from '@/lib/lists/view';
import { DeleteMenu } from './delete-menu';
import { DeliveredOf, KindPill, ListStatusPill, useCreatedText } from './list-bits';

type IndexProps = { lists: List[]; onDelete: (list: List) => void };

function ChatLink({ list }: { list: List }) {
  if (!list.chat_session_id || !list.chat_title) {
    return <span className="text-muted">—</span>;
  }
  return (
    <Link
      href={`/?s=${list.chat_session_id}`}
      className="text-small text-soft hover:text-ink block truncate"
    >
      {list.chat_title}
    </Link>
  );
}

function DeleteButton({ list, onDelete }: { list: List; onDelete: (list: List) => void }) {
  const t = useTranslations('lists');
  return (
    <DeleteMenu
      label={t('listMenu', { name: list.name })}
      item={t('delete')}
      onDelete={() => onDelete(list)}
      className="phone:size-11"
    />
  );
}

function Delivered({ list, plain = false }: { list: List; plain?: boolean }) {
  return (
    <DeliveredOf
      done={list.delivered_count}
      total={list.target_rows ?? list.row_count}
      plain={plain}
    />
  );
}

/** «6 liste»: the load-all list has no pager, so the count stands where the pager was. */
export function IndexCount({ count }: { count: number }) {
  const t = useTranslations('lists');
  return <p className="text-data-sm text-muted font-mono">{t('indexCount', { n: count })}</p>;
}

export function IndexTable({ lists, onDelete }: IndexProps) {
  const t = useTranslations('lists');
  const locale = useLocale();
  const created = useCreatedText();
  return (
    <div className="phone:hidden">
      <Table frame>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('indexName')}</TableHead>
            <TableHead scope="col">{t('indexType')}</TableHead>
            <TableHead scope="col">{t('indexState')}</TableHead>
            <TableHead scope="col">{t('indexDelivered')}</TableHead>
            <TableHead scope="col" num>
              {t('indexCost')}
            </TableHead>
            <TableHead scope="col">{t('indexOrigin')}</TableHead>
            <TableHead scope="col">{t('created')}</TableHead>
            <TableHead scope="col" className="w-12">
              <span className="sr-only">{t('actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lists.map((l) => (
            <TableRow key={l.id}>
              <TableCell className="max-w-85">
                <Link href={`/lists/${l.id}`} className="text-ink hover:text-accent-ink block">
                  <span className="text-ui-strong block truncate hover:underline">{l.name}</span>
                </Link>
                {l.brief && <span className="text-small text-muted block truncate">{l.brief}</span>}
              </TableCell>
              <TableCell>{t(`kind.${listKind(l)}`)}</TableCell>
              <TableCell>
                <ListStatusPill status={l.status} />
              </TableCell>
              <TableCell className="text-data text-ink font-mono">
                <Delivered list={l} plain />
              </TableCell>
              <TableCell num>
                {formatCostPerLead(l.credits_used, l.delivered_count, locale)}
              </TableCell>
              <TableCell className="max-w-56">
                <ChatLink list={l} />
              </TableCell>
              <TableCell className="text-small">{created(l.created_at)}</TableCell>
              <TableCell>
                <DeleteButton list={l} onDelete={onDelete} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function IndexCards({ lists, onDelete }: IndexProps) {
  const t = useTranslations('lists');
  const locale = useLocale();
  const created = useCreatedText();
  return (
    <ul className="flex flex-col gap-2 desktop:hidden">
      {lists.map((l) => (
        <li
          key={l.id}
          className="bg-ground border-line flex flex-col gap-2 rounded-xl border p-3.5"
        >
          <div className="flex items-start gap-2">
            <div className="flex min-w-0 grow flex-col gap-0.5">
              <Link href={`/lists/${l.id}`} className="text-ui-strong">
                {l.name}
              </Link>
              {l.brief && <span className="text-muted text-small line-clamp-2">{l.brief}</span>}
            </div>
            <DeleteButton list={l} onDelete={onDelete} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <KindPill list={l} />
            <ListStatusPill status={l.status} />
          </div>
          <div className="text-muted text-small flex flex-wrap items-center gap-x-3 gap-y-1">
            <Delivered list={l} />
            <span>
              {t('cardCost', {
                cost: formatCostPerLead(l.credits_used, l.delivered_count, locale),
              })}
            </span>
            <span>{created(l.created_at)}</span>
          </div>
          <ChatLink list={l} />
        </li>
      ))}
    </ul>
  );
}
