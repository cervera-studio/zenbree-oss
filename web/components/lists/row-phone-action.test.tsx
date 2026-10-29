// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import emailNotSearched from '@/lib/api/fixtures/email-not-searched.json';
import phoneFailed from '@/lib/api/fixtures/phone-failed.json';
import phoneFound from '@/lib/api/fixtures/phone-found.json';
import phoneNoInput from '@/lib/api/fixtures/phone-no-input.json';
import phoneNotFound from '@/lib/api/fixtures/phone-not-found.json';
import phoneRefunded from '@/lib/api/fixtures/phone-refunded.json';
import phoneSearching from '@/lib/api/fixtures/phone-searching.json';
import { type ListRow, rowPhoneSchema } from '@/lib/lists/api';
import { fixtureRow } from '@/lib/lists/testing';
import { MESSAGES } from '@/messages';
import { RowPhoneAction } from './row-phone-action';

const { searchPhone, refresh } = vi.hoisted(() => ({ searchPhone: vi.fn(), refresh: vi.fn() }));
vi.mock('@/lib/lists/api', async (real) => ({
  ...(await real<typeof import('@/lib/lists/api')>()),
  listsApi: { searchPhone },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
beforeEach(() => vi.clearAllMocks());

/** The route's answer as the client hands it on: parsed by the schema `listsApi.searchPhone` uses. */
const answer = (json: object) => ({ success: true, data: rowPhoneSchema.parse(json) });
const neverSearched = fixtureRow(emailNotSearched);

/** Holds the row like the list page does, so the action sees the row the search returned. */
function Harness({ initial }: { initial: ListRow }) {
  const [row, setRow] = useState(initial);
  return <RowPhoneAction listId="l1" row={row} credits={6} onRowChange={setRow} />;
}

async function render(row: ListRow) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <Harness initial={row} />
      </NextIntlClientProvider>,
    );
  });
  return container;
}

async function search(body: HTMLElement) {
  await act(async () => body.querySelector('button')?.click());
}

describe('RowPhoneAction', () => {
  it('refreshes the credit balance after a charged search', async () => {
    searchPhone.mockResolvedValue(answer(phoneFound));
    const body = await render(neverSearched);
    await search(body);
    expect(searchPhone).toHaveBeenCalledWith('l1', emailNotSearched.id);
    expect(body.textContent).toBe('Telefono trovato. Addebitati 8 crediti.');
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('does not offer a paid retry once the search found no number', async () => {
    searchPhone.mockResolvedValue(answer(phoneNotFound));
    const body = await render(neverSearched);
    await search(body);
    expect(body.querySelector('button')).toBeNull();
    expect(body.textContent).toBe('Telefono non trovato. Nessun credito addebitato.');
    expect(refresh).not.toHaveBeenCalled();
  });

  it('keeps the search after a source failed instead of answering', async () => {
    searchPhone.mockResolvedValue(answer(phoneFailed));
    const body = await render(neverSearched);
    await search(body);
    expect(body.querySelector('button')).not.toBeNull();
    expect(body.textContent).toContain('Ricerca non riuscita. Nessun credito addebitato.');
  });

  it('asks for a name when no source can search the row', async () => {
    searchPhone.mockResolvedValue(answer(phoneNoInput));
    const body = await render(neverSearched);
    await search(body);
    expect(body.querySelector('button')).not.toBeNull();
    expect(body.textContent).toContain('Per cercare il telefono servono nome e cognome');
  });

  it('says the person is in Esclusioni when the API refuses an excluded row', async () => {
    searchPhone.mockResolvedValue({
      success: false,
      error: { code: 'ROW.EXCLUDED', message: 'the row is excluded' },
    });
    const body = await render(neverSearched);
    await search(body);
    expect(body.textContent).toContain('in Esclusioni');
    expect(body.textContent).not.toContain('non riuscita');
  });

  it('says no number was found on a row searched before', async () => {
    const body = await render(fixtureRow(phoneNotFound.row));
    expect(body.querySelector('button')).toBeNull();
    expect(body.textContent).toBe('Nessun numero trovato');
  });

  it('offers the search on a row never searched, or whose search failed or was refunded', async () => {
    for (const row of [emailNotSearched, phoneFailed.row, phoneRefunded]) {
      const body = await render(fixtureRow(row));
      expect(body.querySelector('button')?.textContent).toBe('Cerca il telefono6 crediti');
    }
  });

  it('offers no search while one runs or once a number is found', async () => {
    for (const row of [phoneSearching, phoneFound.row]) {
      const body = await render(fixtureRow(row));
      expect(body.querySelector('button')).toBeNull();
    }
  });
});
