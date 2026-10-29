'use client';

import { MessageScroller, useMessageScroller } from '@shadcn/react/message-scroller';
import { ArrowDown } from 'lucide-react';
import { useReducedMotion } from 'motion/react';
import { useTranslations } from 'next-intl';
import { type ReactNode, useState } from 'react';
import { Button } from '@/components/ui/button';
import { DRAFT_KEY } from '@/lib/chat/stream-store-types';

/** The thread's scroll state. It follows the stream only while the reader is at the bottom.
 * The scroller keeps its mode until it remounts, so each thread gets its own: a chat opened again
 * from the sidebar opens at the bottom, not where the reader left the chat before. */
export function ChatScroll({
  chatKey,
  sessionId,
  empty,
  children,
}: {
  chatKey: string;
  /** The chat's session: the draft gets one on its first send, before the URL names it. */
  sessionId: string | null;
  empty: boolean;
  children: ReactNode;
}) {
  return (
    <MessageScroller.Provider
      key={useThreadKey({ chatKey, sessionId, empty })}
      autoScroll
      defaultScrollPosition="end"
    >
      {children}
    </MessageScroller.Provider>
  );
}

type Thread = { chatKey: string; sessionId: string | null; empty: boolean };

/** A new key for each thread. The draft whose session the URL now names is the same thread, so it
 * is not remounted mid-stream. A draft that empties («Nuova chat» before the first turn has a
 * session) is a new thread. */
function useThreadKey(next: Thread) {
  const [thread, setThread] = useState({ ...next, n: 0 });
  if (
    next.chatKey !== thread.chatKey ||
    next.sessionId !== thread.sessionId ||
    next.empty !== thread.empty
  ) {
    const same =
      next.chatKey === thread.chatKey
        ? !(next.empty && !thread.empty)
        : thread.chatKey === DRAFT_KEY && next.chatKey === thread.sessionId;
    setThread({ ...next, n: same ? thread.n : thread.n + 1 });
  }
  return thread.n;
}

/** Wraps a send so that it also scrolls to the end: a reader who scrolled up and sends wants to
 * see the message and its answer. Call it under `ChatScroll`. */
export function useFollowOnSend() {
  const { scrollToEnd } = useMessageScroller();
  return <A extends unknown[]>(send: (...args: A) => void) =>
    (...args: A) => {
      send(...args);
      scrollToEnd();
    };
}

/** Shows while the thread has content below the view; a click scrolls there and follows again.
 * Inactive, the primitive makes it `inert`, so it leaves the tab order too. Inside a `relative`
 * `MessageScroller.Root`, beside the viewport: the click hands focus to the thread, or a keyboard
 * user would land on the page body once the button goes inert. */
export function JumpToLatest() {
  const t = useTranslations('chat');
  return (
    <MessageScroller.Button
      behavior={useReducedMotion() ? 'auto' : 'smooth'}
      aria-label={t('jumpToLatest')}
      onClick={(e) =>
        e.currentTarget.parentElement
          ?.querySelector<HTMLElement>('[role="region"]')
          ?.focus({ preventScroll: true })
      }
      className="shadow-float absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full duration-in ease-snap data-[active=false]:scale-95 data-[active=false]:opacity-0 data-[active=false]:duration-out data-[active=false]:ease-exit"
      render={<Button variant="outline" size="icon-md" />}
    >
      <ArrowDown aria-hidden />
    </MessageScroller.Button>
  );
}
