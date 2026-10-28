// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { ListRow } from '@/lib/lists/api';
import { MESSAGES } from '@/messages';
import { RowPeople } from './row-people';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

async function render(row: Partial<ListRow>) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <RowPeople row={row as ListRow} chatHref="/?s=c1" />
      </NextIntlClientProvider>,
    );
  });
  return container;
}

describe('RowPeople', () => {
  it('names each person with the role and links to their row', async () => {
    const body = await render({
      people_count: 5,
      people: [{ row_id: 'r1', list_id: 'l1', full_name: 'Ada Rossi', title: 'CEO' }],
    });
    expect(body.textContent).toContain('Ada Rossi');
    expect(body.textContent).toContain('CEO');
    expect(body.textContent).toContain('+4 altre persone');
    expect(body.querySelector('a')?.getAttribute('href')).toBe('/lists/l1?row=r1');
  });

  it('names a person with no name «Senza nome», so the link reads as more than a dash', async () => {
    const body = await render({
      people_count: 1,
      people: [{ row_id: 'r1', list_id: 'l1', full_name: null, title: null }],
    });
    expect(body.querySelector('a')?.textContent).toBe('Senza nome');
  });

  it('points to the chat when no decision maker was found', async () => {
    const body = await render({ people_count: 0, people: [] });
    expect(body.textContent).toBe('Nessun decisore trovato · Chiedi in chat');
    expect(body.querySelector('a')?.getAttribute('href')).toBe('/?s=c1');
  });
});

describe('the people column header', () => {
  it('is named after the issue in both languages', () => {
    expect(MESSAGES.it.lists.column.people).toBe('Persone nella lista Persone');
    expect(MESSAGES.en.lists.column.people).toBe('People in the People list');
  });
});
