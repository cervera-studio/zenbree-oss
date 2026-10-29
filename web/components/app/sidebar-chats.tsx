'use client';

import { MessageSquare, Plus } from 'lucide-react';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type MouseEvent, useId } from 'react';
import { ProjectSwitcher } from '@/components/projects/project-switcher';
import { useChatStreamStore } from '@/lib/chat/stream-provider';
import { type NavSession, useNavState } from '@/lib/home/nav';
import { cn } from '@/lib/utils';
import { rowFocus } from './nav';
import { groupLabel } from './side-rail';

/** The chat session open on home (`/?s=`), or null on the empty home and on other pages. */
export function useOpenSessionId() {
  const onHome = usePathname() === '/';
  const sid = useSearchParams().get('s');
  return { onHome, sid: onHome ? sid : null };
}

/** Home already renders the chat: swap the session in place, no server round trip. */
function useOpenChat() {
  const { onHome } = useOpenSessionId();
  return (e: MouseEvent, id: string | null) => {
    // A modified click (new tab, new window) is the browser's, not ours.
    if (!onHome || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    window.history.replaceState(null, '', id ? `/?s=${id}` : '/');
  };
}

function useResetDraft() {
  const store = useChatStreamStore();
  return () => store.startNewChat();
}

/** The design's workspace row: the client's name over the rail. Switching clients starts a new
 *  chat, since the open one belongs to the one left. */
export function WorkspaceSwitcher({ userId, phone = false }: { userId: string; phone?: boolean }) {
  const { sid } = useOpenSessionId();
  const resetDraft = useResetDraft();
  return (
    <ProjectSwitcher
      userId={userId}
      phone={phone}
      onSwitch={() => {
        resetDraft();
        if (sid) window.history.replaceState(null, '', '/');
      }}
    />
  );
}

/** `phone`: 48 px target for the drawer. An action, not a place: never the current page, since
 *  Panoramica is the entry for `/`. */
export function NewChatLink({ phone = false }: { phone?: boolean }) {
  const t = useTranslations('chat');
  const resetDraft = useResetDraft();
  const openChat = useOpenChat();
  return (
    <Link
      href="/"
      onClick={(e) => {
        resetDraft();
        openChat(e, null);
      }}
      className={cn(
        'text-ui-strong flex shrink-0 items-center justify-center gap-2 rounded-md border transition-colors',
        rowFocus,
        phone ? 'h-12' : 'h-control',
        'bg-ground text-ink hover:bg-hair',
      )}
    >
      <Plus className="size-4" aria-hidden />
      {t('newChat')}
    </Link>
  );
}

const DOT: Record<NonNullable<NavSession['status']>, string> = {
  asking: 'bg-warning',
  plan: 'bg-accent',
  running: 'bg-accent',
  done: 'bg-positive',
  error: 'bg-negative',
};

/** The line under a chat's title (#342): what the chat waits on, or what it found. The link's
 *  description, not part of its name, which stays the title. */
export function SessionMeta({ session, metaId }: { session: NavSession; metaId: string }) {
  const t = useTranslations('chat.sessionMeta');
  const { status, progressDone, progressTotal, listCount, people, companies } = session;
  if (!status) return null;
  const progress = progressTotal > 0 ? { done: progressDone, total: progressTotal } : null;
  const text =
    status === 'running' || status === 'error'
      ? progress
        ? t(`${status}Progress`, progress)
        : t(status)
      : status === 'done'
        ? listCount > 0 && people + companies > 0
          ? t('found', { lists: listCount, people, companies })
          : t('done')
        : t(status);
  return (
    <span
      id={metaId}
      aria-hidden
      className={cn(
        'flex items-center gap-1.5 text-2xs font-normal',
        status === 'asking' ? 'text-warning' : status === 'error' ? 'text-negative' : 'text-muted',
      )}
    >
      <span className={cn('size-1.5 shrink-0 rounded-full', DOT[status])} aria-hidden />
      <span className="truncate">{text}</span>
    </span>
  );
}

/** The sidebar shows the newest few (#434); «Chat» has the rest. */
const RECENT = 8;

export function RecentChats({ phone = false }: { phone?: boolean }) {
  const t = useTranslations('chat');
  const tn = useTranslations('nav');
  const { sid: activeId } = useOpenSessionId();
  const openChat = useOpenChat();
  const { nav, failed } = useNavState();
  const sessions = nav?.sessions ?? null;
  const headingId = useId();

  return (
    <section
      aria-labelledby={headingId}
      className={cn('flex flex-col gap-0.5', phone ? 'shrink-0' : 'min-h-0 flex-1')}
    >
      <h2 id={headingId} className={cn(groupLabel, 'flex items-center gap-1.5')}>
        <MessageSquare className="size-3" aria-hidden />
        {tn('chats')}
      </h2>
      <div className={cn(!phone && 'min-h-0 flex-1 overflow-y-auto')}>
        {failed && !sessions ? (
          <p className="text-muted px-2.5 py-2 text-xs">{t('sessionsError')}</p>
        ) : sessions?.length === 0 ? (
          <p className="text-muted px-2.5 py-2 text-xs leading-relaxed">{t('noSessions')}</p>
        ) : (
          sessions?.slice(0, RECENT).map((session) => {
            const current = session.id === activeId;
            return (
              <Link
                key={session.id}
                href={`/?s=${session.id}`}
                onClick={(e) => openChat(e, session.id)}
                aria-current={current ? 'page' : undefined}
                aria-describedby={session.status ? `${headingId}-${session.id}` : undefined}
                className={cn(
                  'text-xs-plus flex flex-col gap-0.5 rounded-md px-2.5 transition-colors',
                  rowFocus,
                  phone ? 'min-h-12 justify-center py-2.5' : 'py-1.5',
                  current
                    ? 'bg-accent-tint text-accent-ink font-semibold'
                    : 'text-ink hover:bg-hair font-medium',
                )}
              >
                <span className="truncate">{session.title || t('untitled')}</span>
                <SessionMeta session={session} metaId={`${headingId}-${session.id}`} />
              </Link>
            );
          })
        )}
      </div>
    </section>
  );
}
