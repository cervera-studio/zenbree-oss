// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { AiColumnDialog } from './ai-column-dialog';

const { addAiColumn, success } = vi.hoisted(() => ({ addAiColumn: vi.fn(), success: vi.fn() }));
vi.mock('@/lib/lists/api', () => ({ listsApi: { addAiColumn } }));
vi.mock('@/components/ui/sonner', () => ({ toast: { success, error: vi.fn() } }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
beforeEach(() => vi.clearAllMocks());

async function render(onAdded = () => {}, balance: number | null = null) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <AiColumnDialog
          listId="l1"
          delivered={47}
          balance={balance}
          prices={{ search: 0, email: 1, phone: 8, ai_cell: 2, ai_cells_per_run: 200 }}
          open
          onOpenChange={() => {}}
          onAdded={onAdded}
        />
      </NextIntlClientProvider>,
    );
  });
  return document.body;
}

async function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = Object.getPrototypeOf(el);
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('AiColumnDialog', () => {
  it('shows the credit ceiling of the delivered rows at the API price', async () => {
    const body = await render();
    expect(body.textContent).toContain('Fino a 94 crediti · 47 righe consegnate');
  });

  it('confirms the added column with a toast', async () => {
    addAiColumn.mockResolvedValue({ success: true, data: {} });
    const onAdded = vi.fn();
    const body = await render(onAdded);
    await type(body.querySelector('#ai-column-label') as HTMLInputElement, 'Ospedali');
    await type(
      body.querySelector('#ai-column-prompt') as HTMLTextAreaElement,
      'Vende agli ospedali?',
    );
    await act(async () => body.querySelector('form')?.requestSubmit());
    expect(addAiColumn).toHaveBeenCalledWith('l1', 'Ospedali', 'Vende agli ospedali?');
    expect(success).toHaveBeenCalledWith(
      'Colonna «Ospedali» aggiunta: compilazione in corso, prima le righe con fit più alto.',
    );
    expect(onAdded).toHaveBeenCalled();
  });

  it('warns when the balance covers only part of the ceiling, and still lets the user add the column', async () => {
    const body = await render(undefined, 25);
    expect(body.textContent).toContain(
      'Hai 25 crediti: la ricerca copre al massimo 12 righe, prima quelle con fit più alto.',
    );
    await type(body.querySelector('#ai-column-label') as HTMLInputElement, 'Usa HubSpot');
    await type(body.querySelector('#ai-column-prompt') as HTMLTextAreaElement, 'Usa HubSpot?');
    const submit = body.querySelector<HTMLButtonElement>('button[type=submit]');
    expect(submit?.disabled).toBe(false);
  });

  it('says a balance under one cell fills nothing, and says nothing when the balance covers it', async () => {
    expect((await render(undefined, 1)).textContent).toContain(
      'Hai 1 credito: non basta per una cella.',
    );
    expect((await render(undefined, -3)).textContent).toContain(
      'Hai 0 crediti: non basta per una cella.',
    );
    for (const balance of [94, null]) {
      const text = (await render(undefined, balance)).textContent;
      expect(text).not.toContain('Hai ');
    }
  });
});
