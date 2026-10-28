// @vitest-environment happy-dom
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vite-plus/test';
import { DRAFT_KEY } from '@/lib/chat/stream-store-types';
import { ChatScroll } from './chat-scroll';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

let mounts = 0;
function Thread() {
  useState(() => ++mounts);
  return null;
}

type ThreadState = { chatKey: string; sessionId?: string | null; empty?: boolean };

/** The mount count after each thread state in turn: a new mount is a fresh scroller. */
function mountsAfter(threads: ThreadState[]) {
  mounts = 0;
  const root = createRoot(document.createElement('div'));
  const counts = threads.map(({ chatKey, sessionId = chatKey, empty = false }) => {
    act(() =>
      root.render(
        <ChatScroll chatKey={chatKey} sessionId={sessionId} empty={empty}>
          <Thread />
        </ChatScroll>,
      ),
    );
    return mounts;
  });
  act(() => root.unmount());
  return counts;
}

const home = { chatKey: DRAFT_KEY, sessionId: null, empty: true };
const sent = { chatKey: DRAFT_KEY, sessionId: null };

describe('ChatScroll', () => {
  it('keeps the thread from the first send on home until the URL names its session', () => {
    expect(
      mountsAfter([home, sent, { chatKey: DRAFT_KEY, sessionId: 'a' }, { chatKey: 'a' }]),
    ).toEqual([1, 1, 1, 1]);
  });

  it('starts a fresh thread when a draft with no session opens another chat', () => {
    expect(mountsAfter([home, sent, { chatKey: 'x' }])).toEqual([1, 1, 2]);
  });

  it('starts a fresh thread when the draft empties («Nuova chat» before a session)', () => {
    expect(mountsAfter([home, sent, home, sent])).toEqual([1, 1, 2, 2]);
  });

  it('starts a fresh thread for another chat, and for «Nuova chat»', () => {
    expect(mountsAfter([{ chatKey: 'a' }, { chatKey: 'b' }, home, { chatKey: 'a' }])).toEqual([
      1, 2, 3, 4,
    ]);
  });

  it('keeps the thread while the same chat rerenders or loads its messages', () => {
    expect(
      mountsAfter([{ chatKey: 'a', empty: true }, { chatKey: 'a' }, { chatKey: 'a' }]),
    ).toEqual([1, 1, 1]);
  });
});
