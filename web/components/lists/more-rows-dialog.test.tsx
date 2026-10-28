// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { ListDetail } from '@/lib/lists/api';
import { MESSAGES } from '@/messages';
import { MoreRowsDialog } from './more-rows-dialog';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const list = {
  id: 'l1',
  name: 'Agenzie',
  entity: 'person',
  columns: [
    { key: 'full_name', label: 'Nome', kind: 'static', position: 0 },
    { key: 'email', label: 'Email', kind: 'static', position: 1 },
  ],
  prices: { search: 0, email: 2, phone: 8, ai_cell: 1, ai_cells_per_run: 200 },
} as ListDetail;

async function render() {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <MoreRowsDialog list={list} chatHref="/?s=c1" balance={1942} open onOpenChange={() => {}} />
      </NextIntlClientProvider>,
    );
  });
  return document.body;
}

describe('MoreRowsDialog', () => {
  it('shows the estimate of people still findable, hidden when unknown', async () => {
    Object.assign(list, { findable: 1234 });
    expect((await render()).textContent).toContain('~1.234 persone ancora trovabili');
    Object.assign(list, { entity: 'company' });
    expect((await render()).textContent).toContain('~1.234 aziende ancora trovabili');
    Object.assign(list, { entity: 'person', findable: null });
    expect((await render()).textContent).not.toContain('trovabil');
  });

  it('prices the batch with the list columns at the API prices and drafts it in the chat', async () => {
    const body = await render();
    expect(body.textContent).toContain('fino a 100 crediti');
    expect(body.textContent).toContain('Saldo 1.942');
    const hundred = [...body.querySelectorAll('label')].find((l) => l.textContent === '100');
    await act(async () => hundred?.querySelector('input')?.click());
    expect(body.textContent).toContain('fino a 200 crediti');
    const cta = [...body.querySelectorAll('a')].find((a) => a.textContent === 'Aggiungi 100 righe');
    const href = new URL(cta?.getAttribute('href') ?? '', 'http://x');
    expect(href.searchParams.get('s')).toBe('c1');
    expect(href.searchParams.get('draft')).toBe(
      'Aggiungi 100 righe alla lista l1 dalla stessa ricerca.',
    );
  });
});
