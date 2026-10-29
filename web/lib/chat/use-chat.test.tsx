// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vite-plus/test';
import { ChatStreamProvider, useChatStreamStore } from './stream-provider';
import type { ChatStreamStore } from './stream-store';
import type { UIMessage } from './stream-store-types';
import { useChatField, useChatView } from './use-chat';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const renders = { messages: 0, view: 0, lists: 0 };
let store: ChatStreamStore | null = null;

function Probes() {
  store = useChatStreamStore();
  return (
    <>
      <MessagesProbe />
      <ViewProbe />
      <ListsProbe />
    </>
  );
}

function MessagesProbe() {
  useChatField('s1', 'messages');
  renders.messages++;
  return null;
}

function ViewProbe() {
  useChatView('s1');
  renders.view++;
  return null;
}

function ListsProbe() {
  useChatField('s1', 'lists');
  renders.lists++;
  return null;
}

function message(role: UIMessage['role'], content: string): UIMessage {
  return { id: role, role, content, toolCalls: [], parts: [], createdAt: new Date(0) };
}

describe('chat read hooks', () => {
  it('a revealed text step renders the messages reader and neither the view nor the lists reader', async () => {
    const root = createRoot(document.createElement('div'));
    await act(async () =>
      root.render(
        <ChatStreamProvider userId="u1">
          <Probes />
        </ChatStreamProvider>,
      ),
    );
    if (!store) throw new Error('no store');
    const chat = store;
    await act(async () =>
      chat.setChat('s1', (prev) => ({
        ...prev,
        sessionId: 's1',
        turn: { phase: 'streaming', jobId: null },
        messages: [message('user', 'agenzie a Milano'), message('assistant', '')],
        lists: { company: { id: 'L1', entity: 'company', name: null, rowCount: 51, version: 1 } },
      })),
    );
    renders.messages = 0;
    renders.view = 0;
    renders.lists = 0;

    for (const chunk of ['Cerco ', 'agenzie ', 'a Milano.']) {
      await act(async () =>
        chat.setChat('s1', (prev) => ({
          ...prev,
          messages: prev.messages.map((m) =>
            m.role === 'assistant' ? { ...m, content: m.content + chunk } : m,
          ),
        })),
      );
    }

    expect(renders).toEqual({ messages: 3, view: 0, lists: 0 });
    expect(chat.getChat('s1').messages.at(-1)?.content).toBe('Cerco agenzie a Milano.');
  });
});
