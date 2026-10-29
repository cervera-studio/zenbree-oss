'use client';

import { createContext, type PropsWithChildren, useContext, useRef } from 'react';
import { sessionsApi } from './sessions';
import { ChatStreamStore } from './stream-store';

async function recoverSession(sessionId: string) {
  const res = await sessionsApi.get(sessionId);
  return res.success ? res.data : null;
}

async function cancelJob(jobId: string) {
  const res = await sessionsApi.cancelJob(jobId);
  return res.success ? res.data.status : null;
}

const newStore = () => new ChatStreamStore({ recoverSession, cancelJob });

const Ctx = createContext<ChatStreamStore | null>(null);

/**
 * One store per signed-in user for the life of the tab, so a remount of the
 * provider doesn't orphan a live stream. Keyed by user so another account on
 * the same tab never sees the previous one's chats. Server renders keep a
 * store per request.
 */
let clientStore: { userId: string; store: ChatStreamStore } | null = null;

export function ChatStreamProvider({ userId, children }: PropsWithChildren<{ userId: string }>) {
  const serverStore = useRef<ChatStreamStore | null>(null);
  let store: ChatStreamStore;
  if (typeof window === 'undefined') {
    serverStore.current ??= newStore();
    store = serverStore.current;
  } else {
    if (clientStore?.userId !== userId) {
      clientStore?.store.dispose();
      clientStore = { userId, store: newStore() };
    }
    store = clientStore.store;
  }
  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}

export function useChatStreamStore(): ChatStreamStore {
  const store = useContext(Ctx);
  if (!store) throw new Error('useChatStreamStore outside ChatStreamProvider');
  return store;
}
