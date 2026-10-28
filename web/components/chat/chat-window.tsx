'use client';

import { MessageScroller } from '@shadcn/react/message-scroller';
import { useTranslations } from 'next-intl';
import { type FormEvent, type ReactNode, useEffect, useRef } from 'react';
import type { ChatView } from '@/lib/chat/chat-view';
import { failedStage, type JobProgress } from '@/lib/chat/job-progress';
import type { TopUpPack } from '@/lib/chat/plan';
import type { SendOptions, UIMessage } from '@/lib/chat/stream-store-types';
import type { StageRetry } from '@/lib/chat/types';
import type { HomeOverview } from '@/lib/home/overview';
import { cn } from '@/lib/utils';
import { BriefRail } from './brief-rail';
import { ChatComposer } from './chat-composer';
import { ChatMessage, type MessageSteps } from './chat-message';
import { JumpToLatest } from './chat-scroll';
import { useComposerText } from './composer-text';
import type { HomeViewer } from './home-data/home-data';
import { HomeStart } from './home-start';
import type { WelcomeView } from './home-welcome';

export function ChatWindow({
  messages,
  isStreaming,
  credits,
  topUpPack = null,
  companyRows,
  creditsUsed,
  onSend,
  onStop,
  stopping = false,
  narrow = false,
  brief,
  progress,
  jobId,
  offline,
  onRetry,
  listHref,
  isNewChat,
  todo,
  stats,
  welcome,
  overviewRequest,
  viewer,
}: {
  messages: UIMessage[];
  isStreaming: boolean;
  credits: number | null;
  topUpPack?: TopUpPack | null;
  companyRows: number | null;
  creditsUsed: number;
  onSend: (text: string, opts?: SendOptions) => void;
  onStop: () => void;
  /** «Ferma» was pressed: the server is stopping the job. */
  stopping?: boolean;
  /** Beside the list pane: the thread is 380 px wide, so the column and its padding shrink. */
  narrow?: boolean;
  brief: Pick<ChatView, 'brief' | 'stage'> | null;
  progress: JobProgress | null;
  jobId: string | null;
  /** The stream was cut and is not rejoined yet. */
  offline: boolean;
  onRetry: (text: string, retry: StageRetry) => void;
  listHref?: string;
  /** No `?s=`: the home start screen. A session with no messages (a fresh CSV import) stays plain. */
  isNewChat: boolean;
  todo?: ReactNode;
  stats?: ReactNode;
  welcome?: WelcomeView | null;
  overviewRequest: Promise<HomeOverview | null>;
  viewer: HomeViewer;
}) {
  const t = useTranslations('chat');
  const { draft, setDraft, mode, setMode, draftParam, draftArrivals, giveBack } = useComposerText();
  const composer = useRef<HTMLTextAreaElement>(null);
  const last = messages.at(-1);
  // A refused request leaves a failed turn after the open question: that question stays open.
  const open = messages.findLast((m) => m.status !== 'failed');
  const failed = progress && !isStreaming && !offline && jobId ? failedStage(progress) : null;
  const steps: MessageSteps | undefined = progress
    ? {
        progress,
        running: isStreaming || offline,
        credits: creditsUsed,
        listHref,
        onRetry:
          failed && jobId ? (stage) => onRetry(t('steps.retry'), { jobId, stage }) : undefined,
      }
    : undefined;

  // The draft link landed: take `?draft=` out of the URL, so a reload does not fill the box again.
  useEffect(() => {
    // oxlint-disable-next-line react-you-might-not-need-an-effect/no-event-handler -- syncs the URL after a navigation from another page; no event here triggers it
    if (!draftParam) return;
    const url = new URL(window.location.href);
    url.searchParams.delete('draft');
    window.history.replaceState(null, '', url);
  }, [draftParam]);

  const placeholder = composerPlaceholder(t, { offline, isStreaming, last });

  // A card's line without its segment would start a chat with no filters: the card stays to click again.
  const send = (text: string, opts?: SendOptions) =>
    onSend(text, { ...opts, onRejected: opts?.segment === undefined ? giveBack(text) : undefined });

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!draft.trim() || isStreaming) return;
    setDraft('');
    send(draft);
  };

  const editBrief = () => {
    setDraft(t('brief.editPrefix'));
    composer.current?.focus();
  };

  if (isNewChat && messages.length === 0)
    return (
      <div className="h-full min-h-0 overflow-y-auto">
        <HomeStart
          overviewRequest={overviewRequest}
          welcome={welcome}
          viewer={viewer}
          text={draft}
          onText={setDraft}
          mode={mode}
          onMode={setMode}
          composer={composer}
          onSend={send}
          disabled={offline || isStreaming}
          todo={todo}
          stats={stats}
        />
      </div>
    );

  return (
    <div className="flex h-full min-h-0">
      <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
        {/* Under `ChatScroll`. `aria-live="off"`: the `log` role would read each streamed
            paragraph aloud. */}
        <MessageScroller.Root className="relative flex min-h-0 flex-1 flex-col">
          <MessageScroller.Viewport
            aria-label={t('transcript')}
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain data-pending-scroll:invisible"
          >
            <div className={cn('min-h-full px-6 py-5 phone:px-4', narrow && 'px-4')}>
              <MessageScroller.Content
                aria-live="off"
                className={cn(
                  'mx-auto flex min-h-full w-full flex-col gap-3.5',
                  !narrow && 'max-w-chat-column',
                )}
              >
                {messages.length === 0 ? (
                  <EmptyChat />
                ) : (
                  messages.map((m) => (
                    <MessageScroller.Item key={m.id} messageId={m.id} className="flex flex-col">
                      <ChatMessage
                        message={m}
                        streaming={isStreaming && m === last}
                        credits={credits}
                        topUpPack={topUpPack}
                        companyRows={companyRows}
                        onAnswer={!isStreaming && !offline && m === open ? onSend : undefined}
                        steps={m === last && m.role === 'assistant' ? steps : undefined}
                        onEditPlan={() => {
                          setDraft(t('plan.editPrefix'));
                          composer.current?.focus();
                        }}
                      />
                    </MessageScroller.Item>
                  ))
                )}
              </MessageScroller.Content>
            </div>
          </MessageScroller.Viewport>
          <JumpToLatest />
        </MessageScroller.Root>
        <ChatComposer
          // Each draft link mounts a new box, and `autoFocus` puts the cursor in it.
          key={draftArrivals}
          autoFocus={draftParam !== ''}
          draft={draft}
          onDraft={setDraft}
          composer={composer}
          placeholder={placeholder}
          offline={offline}
          isStreaming={isStreaming}
          stopping={stopping}
          narrow={narrow}
          onSubmit={submit}
          onStop={onStop}
        />
      </div>
      {brief?.brief && (
        <BriefRail
          brief={brief.brief}
          stage={brief.stage}
          onEdit={editBrief}
          className="hidden xl:flex"
        />
      )}
    </div>
  );
}

/** A session with no messages (a fresh CSV import): the plain heading, no home. */
function EmptyChat() {
  const t = useTranslations('chat');
  return (
    <div className="m-auto w-full max-w-2xl pb-8">
      <h1 className="text-headline">{t('emptyTitle')}</h1>
      <p className="text-lead text-muted mt-3 max-w-prose">{t('emptyBody')}</p>
    </div>
  );
}

function composerPlaceholder(
  t: ReturnType<typeof useTranslations<'chat'>>,
  { offline, isStreaming, last }: { offline: boolean; isStreaming: boolean; last?: UIMessage },
) {
  if (offline) return t('connection.composer');
  if (isStreaming) return t('composerRunning');
  if (last?.question && !last.question.plan) return t('answerPlaceholder');
  return t('placeholder');
}
