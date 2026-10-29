import { createStore, type StoreApi } from 'zustand/vanilla';
import { IDLE } from './chat-turn';
import { type ChatKey, type ChatState, DRAFT_KEY, type GlobalState } from './stream-store-types';

export interface ChatStoreState {
  chats: Readonly<Record<ChatKey, ChatState>>;
  aliases: Readonly<Record<ChatKey, ChatKey>>;
  global: GlobalState;
}

export type ChatStoreReader = Pick<
  StoreApi<ChatStoreState>,
  'getState' | 'getInitialState' | 'subscribe'
>;

export const EMPTY_CHAT: ChatState = Object.freeze({
  messages: [],
  sessionId: null,
  turn: IDLE,
  jobId: null,
  lists: {},
  activeEntity: null,
  title: null,
  progress: null,
  fillCredits: 0,
});

export function createChatStore(): StoreApi<ChatStoreState> {
  return createStore<ChatStoreState>(() => ({
    chats: { [DRAFT_KEY]: EMPTY_CHAT },
    aliases: {},
    global: { credits: null, maxCredits: null },
  }));
}

export function canonicalKey(s: ChatStoreState, key: ChatKey): ChatKey {
  return s.aliases[key] ?? key;
}

export function chatOf(s: ChatStoreState, key: ChatKey): ChatState {
  return s.chats[canonicalKey(s, key)] ?? EMPTY_CHAT;
}

export function withChat(
  s: ChatStoreState,
  key: ChatKey,
  updater: (prev: ChatState) => ChatState,
): ChatStoreState {
  const k = canonicalKey(s, key);
  const prev = s.chats[k] ?? EMPTY_CHAT;
  const next = updater(prev);
  return next === prev ? s : { ...s, chats: { ...s.chats, [k]: next } };
}

export function withGlobal(
  s: ChatStoreState,
  updater: (prev: GlobalState) => GlobalState,
): ChatStoreState {
  const next = updater(s.global);
  return next === s.global ? s : { ...s, global: next };
}

export function migrated(s: ChatStoreState, oldKey: ChatKey, newKey: ChatKey): ChatStoreState {
  const { [oldKey]: entry, ...rest } = s.chats;
  if (oldKey === newKey || !entry) return s;
  return {
    ...s,
    chats: { ...rest, [newKey]: entry },
    aliases: { ...s.aliases, [oldKey]: newKey },
  };
}

export function draftReleased(s: ChatStoreState, sessionKey: ChatKey): ChatStoreState {
  if (sessionKey === DRAFT_KEY || s.aliases[DRAFT_KEY] !== sessionKey) return s;
  const { [DRAFT_KEY]: _, ...aliases } = s.aliases;
  return { ...s, aliases, chats: { ...s.chats, [DRAFT_KEY]: EMPTY_CHAT } };
}
