// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { sendReplyAction } from '@/app/(app)/inbox/actions';
import { TestIntl } from '@/app/auth/testing';
import type { AppErrorJSON } from '@/lib/types';
import { Answer } from './conversation-parts';
import type { ThreadData } from './types';

vi.mock('next-intl/server', async () => {
  const { createTranslator } = await import('next-intl');
  const { MESSAGES } = await import('@/messages');
  const { FORMATS } = await import('@/i18n/config');
  return {
    getTranslations: async (namespace: 'inbox') =>
      createTranslator({ locale: 'it', messages: MESSAGES.it, formats: FORMATS, namespace }),
  };
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/lib/inbox/draft', () => ({ regenerateDraft: vi.fn() }));
vi.mock('@/app/(app)/inbox/actions', () => ({ sendReplyAction: vi.fn() }));

type Status = ThreadData['replies'][number]['status'];

const answer = (status: Status, n: number): ThreadData['replies'][number] => ({
  id: `a${n}`,
  channel: 'email',
  subject: 'Re: SDR',
  body: 'Il mio testo.',
  status,
  errorCode: status === 'sent' ? null : 'PROVIDER',
  createdAt: '2026-10-18T10:00:00Z',
  deliveredAt: status === 'sent' ? '2026-10-18T10:00:00Z' : null,
});

const draftAfter = (replies: ThreadData['replies']) =>
  replies.some((r) => r.status === 'sent') ? null : 'Bozza vecchia.';

const thread = (replies: ThreadData['replies']) =>
  ({
    message: { id: 'm1', kind: 'reply', label: 'interested', senderAuthenticated: true },
    replies,
    draft: draftAfter(replies),
    replyFrom: 'ale@ritmosales.it',
    replyBlocked: null,
  }) as unknown as ThreadData;

async function show(root: Root, data: ThreadData) {
  const ui = await Answer({ thread: data });
  await act(async () => root.render(<TestIntl>{ui}</TestIntl>));
}

async function sendThenReload(outcome: Status, code: string | null) {
  vi.mocked(sendReplyAction).mockResolvedValue(
    code
      ? {
          success: false,
          error: { name: 'AppError', code, message: 'english', status: 502 } as AppErrorJSON,
        }
      : { success: true, data: answer('sent', 1) },
  );
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  const root = createRoot(container);
  await show(root, thread([]));
  const field = () => container.querySelector('textarea') as HTMLTextAreaElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(
      field(),
      'Il mio testo.',
    );
    field().dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => container.querySelector('form')?.requestSubmit());
  await show(root, thread([answer(outcome, 1)]));
  return { text: field().value, alert: container.querySelector('[role=alert]')?.textContent };
}

describe('Answer after a send', () => {
  it.each([
    ['failed', 'INBOX.SEND_FAILED', "Il provider ha rifiutato l'invio. Riprova."],
    [
      'unknown',
      'INBOX.SEND_UNKNOWN',
      "Il provider non ha confermato l'invio: controlla la posta inviata prima di riprovare.",
    ],
  ] as const)('keeps the text and the error of a %s answer', async (outcome, code, error) => {
    expect(await sendThenReload(outcome, code)).toEqual({ text: 'Il mio testo.', alert: error });
  });

  it('empties the box after a sent answer', async () => {
    expect(await sendThenReload('sent', null)).toEqual({ text: '', alert: undefined });
  });
});

describe('Answer when the conversation has no mailbox', () => {
  const blocked = (replyHold: ThreadData['replyHold']) =>
    ({
      message: { id: 'm1', kind: 'reply', label: 'interested', senderAuthenticated: true },
      replies: [],
      draft: null,
      replyFrom: null,
      replyBlocked: 'INBOX.NO_MAILBOX',
      replyHold,
    }) as unknown as ThreadData;

  it.each([
    ['needs_reconnect', 'La casella da cui è partita la conversazione va ricollegata.'],
    ['unlinked', 'Questo cliente non usa più la casella da cui è partita la conversazione.'],
    [null, 'Nessuna casella collegata da cui rispondere'],
  ] as const)('says why for %s', async (hold, text) => {
    const container = document.createElement('div');
    document.body.replaceChildren(container);
    await show(createRoot(container), blocked(hold));
    expect(container.textContent).toContain(text);
  });
});
