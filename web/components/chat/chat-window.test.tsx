// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { JobProgress } from '@/lib/chat/job-progress';
import type { UIMessage } from '@/lib/chat/stream-store-types';
import type { StageRetry } from '@/lib/chat/types';
import { ChatScroll } from './chat-scroll';
import { ChatWindow } from './chat-window';
import { ComposerTextProvider } from './composer-text';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

let params = new URLSearchParams();
vi.mock('@/app/(app)/actions', () => ({ dismissWelcomeAction: vi.fn() }));
vi.mock('next/navigation', () => ({ useSearchParams: () => params }));
vi.mock('@/components/lists/csv-import-dialog', () => ({ CsvImportDialog: () => null }));

const screen = ({
  isNewChat = false,
  sessionId = null,
  messages = [],
  progress = null,
  onRetry = () => {},
}: {
  isNewChat?: boolean;
  sessionId?: string | null;
  messages?: UIMessage[];
  progress?: JobProgress | null;
  onRetry?: (text: string, retry: StageRetry) => void;
} = {}) => (
  <TestIntl>
    <ChatScroll chatKey="test" sessionId="test" empty={false}>
      <ComposerTextProvider sessionId={sessionId}>
        <ChatWindow
          isNewChat={isNewChat}
          messages={messages}
          isStreaming={false}
          credits={null}
          companyRows={null}
          creditsUsed={0}
          onSend={() => {}}
          onStop={() => {}}
          brief={null}
          progress={progress}
          jobId={progress ? 'j1' : null}
          offline={false}
          overviewRequest={Promise.resolve(null)}
          viewer={{ firstName: null, id: null }}
          onRetry={onRetry}
        />
      </ComposerTextProvider>
    </ChatScroll>
  </TestIntl>
);

function mount() {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  return {
    root: createRoot(container),
    box: () => container.querySelector('textarea') as HTMLTextAreaElement,
  };
}

async function type(box: HTMLTextAreaElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(box, text);
    box.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('ChatWindow ?draft=', () => {
  it('prefills when the param arrives after mount, without sending', async () => {
    params = new URLSearchParams();
    const { root, box } = mount();
    await act(async () => root.render(screen()));
    expect(box().value).toBe('');

    params = new URLSearchParams({ draft: 'Trova le email mancanti' });
    await act(async () => root.render(screen()));
    expect(box().value).toBe('Trova le email mancanti');
    expect(document.activeElement).toBe(box());
  });

  it('focuses the box again for the next draft link', async () => {
    params = new URLSearchParams({ s: 'chat-a', draft: 'Prima bozza' });
    const { root, box } = mount();
    await act(async () => root.render(screen()));
    params = new URLSearchParams({ s: 'chat-a' });
    await act(async () => root.render(screen()));
    box().blur();

    params = new URLSearchParams({ s: 'chat-a', draft: 'Seconda bozza' });
    await act(async () => root.render(screen()));
    expect(box().value).toBe('Seconda bozza');
    expect(document.activeElement).toBe(box());
  });

  it('leaves the focus alone when a later chat mounts the box', async () => {
    params = new URLSearchParams({ s: 'chat-a', draft: 'Bozza' });
    const { root, box } = mount();
    await act(async () => root.render(screen()));
    params = new URLSearchParams();
    await act(async () => root.render(screen({ isNewChat: true })));
    box().blur();

    const sent: UIMessage = {
      id: 'm1',
      role: 'user',
      content: 'Ciao',
      toolCalls: [],
      parts: [],
      createdAt: new Date(0),
    };
    await act(async () => root.render(screen({ isNewChat: true, messages: [sent] })));
    expect(document.activeElement).not.toBe(box());
  });
});

describe('ChatWindow composer across chats (#570)', () => {
  it('opens «Nuova chat» with an empty composer after text typed in a chat', async () => {
    params = new URLSearchParams({ s: 'chat-a' });
    const { root, box } = mount();
    await act(async () => root.render(screen()));
    await type(box(), 'Testo scritto a mano');

    params = new URLSearchParams();
    await act(async () => root.render(screen({ isNewChat: true })));
    expect(box().value).toBe('');
  });

  it("drops a draft link's text when another chat opens", async () => {
    params = new URLSearchParams({ s: 'chat-a', draft: 'Prova bozza' });
    const { root, box } = mount();
    await act(async () => root.render(screen()));
    params = new URLSearchParams({ s: 'chat-a' });
    await act(async () => root.render(screen()));
    expect(box().value).toBe('Prova bozza');

    params = new URLSearchParams({ s: 'chat-b' });
    await act(async () => root.render(screen()));
    expect(box().value).toBe('');
  });

  it('fills the chat a draft link opens without a reload (#554)', async () => {
    params = new URLSearchParams();
    const { root, box } = mount();
    await act(async () => root.render(screen({ isNewChat: true })));
    await type(box(), 'Testo della home');

    params = new URLSearchParams({ s: 'chat-a', draft: 'Trova le email mancanti' });
    await act(async () => root.render(screen()));
    expect(box().value).toBe('Trova le email mancanti');
  });

  it('keeps the text while the same chat rerenders', async () => {
    params = new URLSearchParams({ s: 'chat-a' });
    const { root, box } = mount();
    await act(async () => root.render(screen()));
    await type(box(), 'Testo scritto a mano');

    params = new URLSearchParams({ s: 'chat-a' });
    await act(async () => root.render(screen()));
    expect(box().value).toBe('Testo scritto a mano');
  });

  it('keeps the text when a chat started on home gets its id in the URL', async () => {
    params = new URLSearchParams();
    const { root, box } = mount();
    await act(async () => root.render(screen({ isNewChat: true })));
    await type(box(), 'Testo non inviato');

    await act(async () => root.render(screen({ isNewChat: true, sessionId: 'chat-y' })));
    params = new URLSearchParams({ s: 'chat-y' });
    await act(async () => root.render(screen({ sessionId: 'chat-y' })));
    expect(box().value).toBe('Testo non inviato');
  });

  it('clears text typed on home when the sidebar opens another chat', async () => {
    params = new URLSearchParams();
    const { root, box } = mount();
    await act(async () => root.render(screen({ isNewChat: true })));
    await type(box(), 'Testo della home');

    params = new URLSearchParams({ s: 'chat-x' });
    await act(async () => root.render(screen()));
    expect(box().value).toBe('');
  });

  it('keeps the text when «Nuova chat» bounces back to a streaming chat', async () => {
    params = new URLSearchParams({ s: 'chat-y' });
    const { root, box } = mount();
    await act(async () => root.render(screen({ sessionId: 'chat-y' })));
    await type(box(), 'Testo non inviato');

    params = new URLSearchParams();
    await act(async () => root.render(screen({ sessionId: 'chat-y' })));
    params = new URLSearchParams({ s: 'chat-y' });
    await act(async () => root.render(screen({ sessionId: 'chat-y' })));
    expect(box().value).toBe('Testo non inviato');
  });
});

describe('ChatWindow «Riprova» after every search failed (demo QA chat-7)', () => {
  it('posts the localized retry label, never the stage label the model wrote', async () => {
    params = new URLSearchParams({ s: 'chat-a' });
    const answer: UIMessage = {
      id: 'm1',
      role: 'assistant',
      content: '',
      toolCalls: [],
      parts: [],
      createdAt: new Date(0),
    };
    const progress: JobProgress = {
      stages: [
        {
          tool: 'search_people',
          label: 'Find purchasing managers',
          units: 3,
          done: 0,
          error: { code: 'provider' },
        },
      ],
      phase: null,
      creditsSpent: 0,
      retryStage: null,
      listId: 'L',
    };
    const onRetry = vi.fn();
    const { root } = mount();
    await act(async () =>
      root.render(screen({ sessionId: 'chat-a', messages: [answer], progress, onRetry })),
    );
    const button = [...document.querySelectorAll('button')].find(
      (b) => b.textContent === 'Riprova',
    );
    await act(async () => button?.click());
    expect(onRetry).toHaveBeenCalledWith('Riprova', { jobId: 'j1', stage: 0 });
  });
});
