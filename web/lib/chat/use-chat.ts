'use client';

import { useTranslations } from 'next-intl';
import { useMemo, useRef } from 'react';
import { useStore } from 'zustand';
import { useErrorText } from '@/i18n/error-text';
import type { ChatLists } from './chat-lists';
import { type ChatStoreState, chatOf } from './chat-store';
import type { ChatTurn } from './chat-turn';
import { type ChatView, deriveChatView, sameView } from './chat-view';
import type { JobProgress } from './job-progress';
import { useChatStreamStore } from './stream-provider';
import type { ChatKey, ChatState, SendOptions, UseChatLabels } from './stream-store-types';
import type { ChatMessage, StageRetry } from './types';

// Terminal codes the Zenbree loop can end a run on.
const TERMINAL_CODES = ['max_turns_reached', 'cancelled'] as const;

function useChatLabels(): UseChatLabels {
  const t = useTranslations('chat.errors');
  const errorText = useErrorText();
  return useMemo(
    () => ({
      networkError: t('networkError'),
      streamTimeout: t('streamTimeout'),
      streamInterrupted: t('streamInterrupted'),
      creditsExhausted: t('creditsExhausted'),
      creditsLow: t('creditsLow'),
      stopFailed: t('stopFailed'),
      upgradeCta: t('upgradeCta'),
      terminals: Object.fromEntries(TERMINAL_CODES.map((c) => [c, t(`terminals.${c}`)])),
      errorText,
    }),
    [t, errorText],
  );
}

function useStableSelection<T>(
  select: (s: ChatStoreState) => T,
  equal: (a: T, b: T) => boolean,
): (s: ChatStoreState) => T {
  const prev = useRef<{ value: T } | null>(null);
  return (s) => {
    const next = select(s);
    if (prev.current && equal(prev.current.value, next)) return prev.current.value;
    prev.current = { value: next };
    return next;
  };
}

export function useChatField<F extends keyof ChatState>(key: ChatKey, field: F): ChatState[F] {
  const store = useChatStreamStore();
  return useStore(store.state, (s) => chatOf(s, key)[field]);
}

/** One fact about the turn (`turnLive`, `turnOffline`…): renders only when that fact changes. */
export function useChatTurn<T>(key: ChatKey, read: (turn: ChatTurn) => T): T {
  const store = useChatStreamStore();
  return useStore(store.state, (s) => read(chatOf(s, key).turn));
}

export function useChatView(key: ChatKey): ChatView | null {
  const store = useChatStreamStore();
  return useStore(
    store.state,
    useStableSelection((s) => deriveChatView(chatOf(s, key)), sameView),
  );
}

export function useChatEmpty(key: ChatKey): boolean {
  const store = useChatStreamStore();
  return useStore(store.state, (s) => chatOf(s, key).messages.length === 0);
}

export function useCredits(): number | null {
  const store = useChatStreamStore();
  return useStore(store.state, (s) => s.global.credits);
}

export interface ChatActions {
  send: (text: string, opts?: SendOptions) => void;
  retry: (text: string, retryStage: StageRetry) => void;
  stop: () => void;
  addFillCredits: (credits: number) => void;
  loadSession: (
    id: string,
    messages: ChatMessage[],
    extra?: {
      lists?: ChatLists;
      jobId?: string | null;
      title?: string | null;
      progress?: JobProgress | null;
      fillCredits?: number;
    },
  ) => void;
}

export function useChatActions(key: ChatKey): ChatActions {
  const store = useChatStreamStore();
  const labels = useChatLabels();
  const labelsRef = useRef(labels);
  labelsRef.current = labels;
  return useMemo<ChatActions>(
    () => ({
      send: (text, opts = {}) =>
        store.send(key, { message: text, ...opts, labels: labelsRef.current }),
      retry: (text, retryStage) =>
        store.send(key, { message: text, retryStage, labels: labelsRef.current }),
      stop: () => store.stop(key, labelsRef.current),
      addFillCredits: (credits) => store.addFillCredits(key, credits),
      loadSession: (id, messages, extra) => {
        store.loadSession(key, id, messages, extra);
        // Reload mid-turn: rejoin the running job instead of showing a bare question.
        store.reattachUnanswered(key, labelsRef.current);
      },
    }),
    [store, key],
  );
}
