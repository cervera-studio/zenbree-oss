import { describe, expect, it } from 'vite-plus/test';
import {
  type ChatStoreState,
  chatOf,
  createChatStore,
  draftReleased,
  migrated,
  withChat,
} from './chat-store';
import { DRAFT_KEY } from './stream-store-types';

function draftWith(sessionId: string): ChatStoreState {
  const s = createChatStore().getState();
  return withChat(s, DRAFT_KEY, (prev) => ({
    ...prev,
    sessionId,
    turn: { phase: 'streaming', jobId: null },
  }));
}

describe('chat store transitions', () => {
  it('serves the migrated chat, same object, under both the draft key and the session id', () => {
    const before = draftWith('s1');
    const draft = chatOf(before, DRAFT_KEY);
    const after = migrated(before, DRAFT_KEY, 's1');

    expect(chatOf(after, 's1')).toBe(draft);
    expect(chatOf(after, DRAFT_KEY)).toBe(draft);
    expect(after.chats[DRAFT_KEY]).toBeUndefined();
  });

  it('writes through the alias to the session chat', () => {
    const s = migrated(draftWith('s1'), DRAFT_KEY, 's1');
    const next = withChat(s, DRAFT_KEY, (prev) => ({ ...prev, title: 'Agenzie' }));

    expect(next.chats.s1?.title).toBe('Agenzie');
    expect(next.chats[DRAFT_KEY]).toBeUndefined();
  });

  it('moves nothing when no chat is stored under the old key', () => {
    const s = createChatStore().getState();
    expect(migrated(s, 'gone', 's1')).toBe(s);
  });

  it('keeps the alias when the stream that ends is not the one the draft points at', () => {
    const s = migrated(draftWith('s1'), DRAFT_KEY, 's1');
    expect(draftReleased(s, 's2')).toBe(s);
  });
});
