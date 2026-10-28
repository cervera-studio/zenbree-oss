// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { sendReplyAction } from '@/app/(app)/inbox/actions';
import { render } from '@/app/auth/testing';
import type { AppErrorJSON, Result } from '@/lib/types';
import { ReplyBox } from './reply-box';

let answer: (r: Result<{ draft: string }>) => void = () => {};
const regenerateDraft = vi.fn(
  (_id: string) =>
    new Promise<Result<{ draft: string }>>((resolve) => {
      answer = resolve;
    }),
);
vi.mock('@/lib/inbox/draft', () => ({ regenerateDraft: (id: string) => regenerateDraft(id) }));
const refresh = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));
vi.mock('@/app/(app)/inbox/actions', () => ({ sendReplyAction: vi.fn() }));
beforeEach(() => {
  regenerateDraft.mockClear();
  refresh.mockClear();
});

const failed = (code: string): Result<never> => ({
  success: false,
  error: { name: 'AppError', code, message: 'english', status: 502 } as AppErrorJSON,
});

async function box(draft: string | null = 'Bozza vecchia.') {
  const c = await render(
    <ReplyBox id="m1" draft={draft} from="ale@ritmosales.it" canDraft={true} />,
  );
  const field = c.querySelector('textarea') as HTMLTextAreaElement;
  const button = (text: string) =>
    [...c.querySelectorAll('button')].find((b) => b.textContent?.includes(text));
  const type = (value: string) =>
    act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      set?.call(field, value);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
  const click = (text: string) => act(async () => button(text)?.click());
  const label = () => c.querySelector('label')?.textContent;
  return { c, field, button, type, click, label };
}

describe('ReplyBox «Rigenera»', () => {
  it('puts the new draft in, with the replaced text one swap away', async () => {
    const b = await box();
    await b.type('Il mio testo.');
    await b.click('Rigenera');
    expect(b.field.readOnly).toBe(true);
    expect(b.button('Scrivo…')?.disabled).toBe(true);
    expect(b.button('Invia risposta')?.disabled).toBe(true);
    await act(async () => answer({ success: true, data: { draft: 'Bozza nuova.' } }));
    expect(b.field.value).toBe('Bozza nuova.');
    expect(b.field.readOnly).toBe(false);
    expect(refresh).toHaveBeenCalledOnce();
    await b.click('Ripristina il testo precedente');
    expect(b.field.value).toBe('Il mio testo.');
    await b.click('Ripristina il testo precedente');
    expect(b.field.value).toBe('Bozza nuova.');
  });

  it('starts one call for a double click', async () => {
    const b = await box();
    await act(async () => {
      b.button('Rigenera')?.click();
      b.button('Rigenera')?.click();
    });
    expect(regenerateDraft).toHaveBeenCalledExactlyOnceWith('m1');
  });

  it('keeps the text and shows the error text when the call fails', async () => {
    const b = await box();
    await b.type('Il mio testo.');
    await b.click('Rigenera');
    await act(async () => answer(failed('INBOX.LLM_FAILED')));
    expect(b.field.value).toBe('Il mio testo.');
    expect(b.c.querySelector('[role=alert]')?.textContent).toBe(
      'Non siamo riusciti a scrivere la bozza. Riprova.',
    );
    expect(b.button('Ripristina il testo precedente')).toBeUndefined();
    expect(refresh).not.toHaveBeenCalled();
  });

  it('labels a restored text of the user as theirs, not the agent’s', async () => {
    const b = await box(null);
    expect(b.label()).toBe('La tua risposta · da ale@ritmosales.it');
    await b.type('Scritto da me.');
    await b.click('Scrivi una bozza');
    await act(async () => answer({ success: true, data: { draft: 'Bozza nuova.' } }));
    expect(b.label()).toBe("Bozza dell'agente · da ale@ritmosales.it");
    await b.click('Ripristina il testo precedente');
    expect(b.field.value).toBe('Scritto da me.');
    expect(b.label()).toBe('La tua risposta · da ale@ritmosales.it');
  });
});

describe('ReplyBox send refused by the mailbox (#1053)', () => {
  it.each([
    ['INBOX.MAILBOX_ERROR', "La casella non autorizza l'invio: ricollegala in Integrazioni."],
    [
      'INBOX.NO_MAILBOX',
      'Nessuna casella collegata da cui rispondere: collegala o ricollegala in Integrazioni.',
    ],
  ])('keeps the text and links to Integrations after %s', async (code, text) => {
    vi.mocked(sendReplyAction).mockResolvedValue(failed(code));
    const b = await box(null);
    await b.type('Il mio testo.');
    await b.click('Invia risposta');
    expect(b.field.value).toBe('Il mio testo.');
    const alert = b.c.querySelector('[role=alert]');
    expect(alert?.textContent).toContain(text);
    const link = alert?.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/settings/integrations');
    // In this tab the box and its text stay, to send once the mailbox is back.
    expect(link?.getAttribute('target')).toBe('_blank');
  });

  it('links nowhere for an error that a reconnect does not fix', async () => {
    vi.mocked(sendReplyAction).mockResolvedValue(failed('INBOX.SEND_FAILED'));
    const b = await box(null);
    await b.type('Il mio testo.');
    await b.click('Invia risposta');
    expect(b.c.querySelector('[role=alert] a')).toBeNull();
  });
});
