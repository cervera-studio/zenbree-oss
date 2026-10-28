'use client';

import { Pin, Plus, Search, Trash2 } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useLocale, useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import { sessionsApi } from '@/lib/chat/sessions';
import { type NavSession, useNavState } from '@/lib/home/nav';
import { cn } from '@/lib/utils';
import { rowFocus } from './nav';
import { SessionMeta } from './sidebar-chats';

const H1 = 'text-title';
const ICON = 'text-muted size-8 phone:size-11';

/** Every chat of the active project (#434), from the sidebar's stream, so pin and delete show up
 *  live. Pinned first, then the newest. ponytail: the stream carries a project's newest 200 chats;
 *  page `GET /v1/chat/sessions` when a project outgrows that. */
export function AllChats() {
  const t = useTranslations('chats');
  const errorText = useErrorText();
  const tc = useTranslations('chat');
  const locale = useLocale();
  const { nav, failed } = useNavState();
  const [query, setQuery] = useState('');
  const [deleting, setDeleting] = useState<NavSession | null>(null);
  const [now] = useState(() => new Date());
  const sessions = nav?.sessions ?? null;
  const q = query.trim().toLocaleLowerCase(locale);
  const shown = q
    ? sessions?.filter((s) => (s.title ?? '').toLocaleLowerCase(locale).includes(q))
    : sessions;

  const pin = async (session: NavSession) => {
    const res = await sessionsApi.pin(session.id, !session.pinnedAt);
    if (!res.success) toast.error(t('pinError'));
  };
  const remove = async (session: NavSession) => {
    const res = await sessionsApi.remove(session.id);
    setDeleting(null);
    if (!res.success)
      return toast.error(
        res.error.code === 'CHAT.JOB_RUNNING'
          ? t('deleteRunning')
          : errorText(res.error, t('deleteError')),
      );
    toast.success(t('deleted'));
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-0 grow flex-col gap-1.5">
          <h1 className={H1}>{t('title')}</h1>
          <p className="text-muted text-sm">{t('subtitle')}</p>
        </div>
        <Button asChild className="h-9.5 px-4 font-semibold phone:h-11 phone:w-full">
          <Link href="/">
            <Plus /> {tc('newChat')}
          </Link>
        </Button>
      </div>
      <div className="relative">
        <Search
          className="text-muted pointer-events-none absolute top-1/2 left-3 size-3.75 -translate-y-1/2"
          aria-hidden
        />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('searchPlaceholder')}
          aria-label={t('search')}
          className="h-9 pl-8.5 text-xs-plus phone:h-12"
        />
      </div>
      {failed && !sessions ? (
        <p className="text-muted text-sm">{tc('sessionsError')}</p>
      ) : !shown ? (
        <ul aria-hidden className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <li key={i} className="bg-side h-14 rounded-lg" />
          ))}
        </ul>
      ) : shown.length === 0 ? (
        <p className="text-muted text-sm">
          {q ? t('noMatch', { query: query.trim() }) : tc('noSessions')}
        </p>
      ) : (
        <ul className="bg-ground flex flex-col rounded-xl border">
          {shown.map((session) => (
            <ChatRow
              key={session.id}
              session={session}
              now={now}
              onPin={() => pin(session)}
              onDelete={() =>
                session.status === 'running'
                  ? toast.error(t('deleteRunning'))
                  : setDeleting(session)
              }
            />
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t('deleteTitle', { title: deleting?.title || tc('untitled') })}
        description={t('deleteBody')}
        confirmLabel={t('delete')}
        onConfirm={() => deleting && remove(deleting)}
      />
    </div>
  );
}

function ChatRow({
  session,
  now,
  onPin,
  onDelete,
}: {
  session: NavSession;
  now: Date;
  onPin: () => void;
  onDelete: () => void;
}) {
  const t = useTranslations('chats');
  const tc = useTranslations('chat');
  const format = useFormatter();
  const title = session.title || tc('untitled');
  const metaId = `chat-meta-${session.id}`;
  return (
    <li className="flex items-center gap-2 border-b px-2 last:border-b-0">
      <Link
        href={`/?s=${session.id}`}
        aria-describedby={session.status ? metaId : undefined}
        className={cn(
          'hover:bg-hair flex min-w-0 flex-1 flex-col gap-0.5 rounded-md px-2 py-2.5 phone:min-h-12',
          rowFocus,
        )}
      >
        <span className="text-sm-plus flex items-center gap-1.5 truncate font-semibold">
          {session.pinnedAt && <Pin className="text-accent-ink size-3.5 shrink-0" aria-hidden />}
          <span className="truncate">{title}</span>
        </span>
        <SessionMeta session={session} metaId={metaId} />
      </Link>
      <span className="text-muted shrink-0 text-xs whitespace-nowrap phone:hidden">
        {format.relativeTime(new Date(session.updatedAt), now)}
      </span>
      <Button
        variant="ghost"
        size="icon-md"
        className={cn(ICON, session.pinnedAt && 'text-accent-ink')}
        aria-label={t('pinChat', { title })}
        aria-pressed={Boolean(session.pinnedAt)}
        onClick={onPin}
      >
        <Pin className="size-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon-md"
        className={cn(
          ICON,
          'aria-disabled:opacity-50',
          session.status !== 'running' && 'hover:text-negative',
        )}
        aria-label={t('deleteChat', { title })}
        // not `disabled`: a click must reach onDelete, which says why it waits (#494)
        aria-disabled={session.status === 'running'}
        onClick={onDelete}
      >
        <Trash2 className="size-4" />
      </Button>
    </li>
  );
}
