'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type ComponentProps, type ReactNode, useEffect, useState } from 'react';
import { usePhone } from '@/components/app/use-phone';
import { useErrorText } from '@/i18n/error-text';
import { turnIdle, turnLive, turnOffline, turnStopping } from '@/lib/chat/chat-turn';
import { type PanelChoice, resolvePanel } from '@/lib/chat/chat-view';
import type { TopUpPack } from '@/lib/chat/plan';
import { RECONNECT_TRIES, reconnectDelay } from '@/lib/chat/reconnect';
import { sessionsApi } from '@/lib/chat/sessions';
import { useChatStreamStore } from '@/lib/chat/stream-provider';
import { DRAFT_KEY } from '@/lib/chat/stream-store-types';
import {
  type ChatActions,
  useChatActions,
  useChatEmpty,
  useChatField,
  useChatTurn,
  useChatView,
  useCredits,
} from '@/lib/chat/use-chat';
import type { HomeOverview } from '@/lib/home/overview';
import { setCurrentProjectId } from '@/lib/projects';
import { ChatFrame } from './chat-frame';
import { ChatListsPanel } from './chat-lists-panel';
import { ChatScroll, useFollowOnSend } from './chat-scroll';
import { ChatWindow } from './chat-window';
import { ComposerTextProvider } from './composer-text';
import { ConnectionBanner } from './connection-banner';
import type { HomeViewer } from './home-data/home-data';
import type { WelcomeView } from './home-welcome';
import { useCloseOnDraft } from './use-close-on-draft';

function setSessionParam(id: string | null) {
  window.history.replaceState(null, '', id ? `/?s=${id}` : '/');
}

function usePanelChoice(chatKey: string) {
  const [pick, setPick] = useState<{ key: string; choice: PanelChoice }>({
    key: chatKey,
    choice: { kind: 'auto' },
  });
  const choice: PanelChoice = pick.key === chatKey ? pick.choice : { kind: 'auto' };
  return [choice, (next: PanelChoice) => setPick({ key: chatKey, choice: next })] as const;
}

function LoadFailed({ gone }: { gone: { code: string } | null }) {
  const t = useTranslations('chat');
  const errorText = useErrorText();
  return <p className="text-muted p-8 text-center">{gone ? errorText(gone) : t('loadError')}</p>;
}

/** `/`: chat · the session's List; the sidebar lists the sessions. The chat key is `?s=` or DRAFT. */
export function ChatApp({
  initialCredits,
  topUpPack,
  todo,
  stats,
  welcome,
  overviewRequest,
  viewer,
}: {
  initialCredits: number | null;
  topUpPack: TopUpPack | null;
  todo?: ReactNode;
  stats?: ReactNode;
  welcome?: WelcomeView | null;
  overviewRequest: Promise<HomeOverview | null>;
  viewer: HomeViewer;
}) {
  const router = useRouter();
  const store = useChatStreamStore();
  const sid = useSearchParams().get('s');
  const chatKey = sid ?? DRAFT_KEY;

  const [loadFailures, setLoadFailures] = useState(0);
  // A chat deleted (or never shared) is gone for good: no retry, no "reconnecting" banner.
  const [missing, setMissing] = useState<{ sid: string; code: string } | null>(null);
  const gone = missing?.sid === sid ? missing : null;
  const loadError = loadFailures > 0 && !gone;
  const phone = usePhone();
  const [choice, choose] = usePanelChoice(chatKey);
  useCloseOnDraft(() => choose({ kind: 'closed' }));

  const sessionId = useChatField(chatKey, 'sessionId');
  const isStreaming = useChatTurn(chatKey, turnLive);
  const offline = useChatTurn(chatKey, turnOffline);
  const idle = useChatTurn(chatKey, turnIdle);
  const jobId = useChatField(chatKey, 'jobId');
  const progress = useChatField(chatKey, 'progress');
  const lists = useChatField(chatKey, 'lists');
  const activeEntity = useChatField(chatKey, 'activeEntity');
  const empty = useChatEmpty(chatKey);
  const view = useChatView(chatKey);
  const actions = useChatActions(chatKey);
  const { loadSession } = actions;
  const panelEntity = resolvePanel(choice, lists, activeEntity, view?.status ?? null, phone);
  const openable = progress?.listId ?? (lists[activeEntity ?? 'person'] ?? lists.company)?.id;
  const listHref = openable ? `/lists/${openable}` : undefined;

  useEffect(() => {
    // oxlint-disable-next-line react-you-might-not-need-an-effect/no-event-handler -- syncs a server-rendered prop into the external chat store; no event triggers it
    if (initialCredits !== null) store.seedCredits(initialCredits);
  }, [store, initialCredits]);

  // A draft got its real id from the `session` frame: put it in the URL.
  useEffect(() => {
    if (!sid && sessionId) setSessionParam(sessionId);
  }, [sid, sessionId]);

  // Opening `?s=` that the store doesn't hold yet (reload, sidebar click). While the API is down it
  // asks again on the connection backoff, so the chat opens by itself once the API is back.
  useEffect(() => {
    if (!sid || !empty || isStreaming || gone) return;
    let stale = false;
    const timer = setTimeout(
      () =>
        void sessionsApi.get(sid).then((res) => {
          if (stale) return;
          if (!res.success && res.error.status === 404)
            return setMissing({ sid, code: res.error.code });
          if (!res.success) return setLoadFailures((n) => Math.min(n + 1, RECONNECT_TRIES));
          setLoadFailures(0);
          if (res.data.projectId) void setCurrentProjectId(res.data.projectId);
          loadSession(sid, res.data.messages, {
            lists: res.data.lists,
            jobId: res.data.jobId,
            title: res.data.title,
            progress: res.data.progress,
            fillCredits: res.data.fillCredits,
          });
        }),
      loadFailures ? reconnectDelay(loadFailures - 1) : 0,
    );
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [sid, empty, isStreaming, loadSession, loadFailures, gone]);

  // End of a turn: server-rendered chrome (the sidebar's credits) catches up.
  const [wasStreaming, setWasStreaming] = useState(false);
  if (wasStreaming !== isStreaming) {
    setWasStreaming(isStreaming);
    if (!isStreaming) router.refresh();
  }

  const subscriber = (
    <MessagesSubscriber
      chatKey={chatKey}
      actions={actions}
      isStreaming={isStreaming}
      topUpPack={topUpPack}
      companyRows={lists.company?.rowCount ?? null}
      creditsUsed={view?.creditsUsed ?? 0}
      narrow={panelEntity !== null}
      brief={panelEntity ? null : view}
      progress={progress}
      jobId={jobId}
      offline={offline}
      listHref={listHref}
      isNewChat={!sid}
      todo={todo}
      stats={stats}
      welcome={welcome}
      overviewRequest={overviewRequest}
      viewer={viewer}
    />
  );
  const banner = (offline || loadError) && <ConnectionBanner />;

  // The home owns its topbar, so it sits outside the chat's page frame.
  if (!sid && empty)
    return (
      <ComposerTextProvider sessionId={sessionId}>
        <ChatScroll chatKey={chatKey} sessionId={sessionId} empty={empty}>
          <div className="relative flex h-full min-h-0 flex-col">
            {banner}
            <div className="min-h-0 flex-1">{subscriber}</div>
          </div>
        </ChatScroll>
      </ComposerTextProvider>
    );

  return (
    <ComposerTextProvider sessionId={sessionId}>
      <ChatScroll chatKey={chatKey} sessionId={sessionId} empty={empty}>
        <ChatFrame
          view={view}
          failed={Boolean(gone || loadError)}
          phone={phone}
          openEntity={panelEntity}
          onChoose={choose}
          banner={banner}
          thread={gone || loadError ? <LoadFailed gone={gone} /> : subscriber}
          panel={
            panelEntity &&
            view && (
              <FollowingListsPanel
                lists={lists}
                entity={panelEntity}
                view={view}
                activeEntity={activeEntity}
                job={jobId ? { id: jobId, progress, streaming: isStreaming } : null}
                onSelect={(entity) => choose({ kind: 'open', entity })}
                onClose={() => choose({ kind: 'closed' })}
                onMoreRows={idle ? actions.send : undefined}
                onFillEnd={actions.addFillCredits}
              />
            )
          }
        />
      </ChatScroll>
    </ComposerTextProvider>
  );
}

/** «More rows» in the panel sends a chat message: the thread beside it follows like any send. */
function FollowingListsPanel({ onMoreRows, ...rest }: ComponentProps<typeof ChatListsPanel>) {
  const follow = useFollowOnSend();
  return <ChatListsPanel {...rest} onMoreRows={onMoreRows && follow(onMoreRows)} />;
}

function MessagesSubscriber({
  chatKey,
  actions,
  ...rest
}: { chatKey: string; actions: ChatActions } & Omit<
  ComponentProps<typeof ChatWindow>,
  'messages' | 'stopping' | 'credits' | 'onSend' | 'onStop' | 'onRetry'
>) {
  const messages = useChatField(chatKey, 'messages');
  const stopping = useChatTurn(chatKey, turnStopping);
  const credits = useCredits();
  const follow = useFollowOnSend();
  return (
    <ChatWindow
      {...rest}
      messages={messages}
      stopping={stopping}
      credits={credits}
      onSend={follow(actions.send)}
      onStop={actions.stop}
      onRetry={follow(actions.retry)}
    />
  );
}
