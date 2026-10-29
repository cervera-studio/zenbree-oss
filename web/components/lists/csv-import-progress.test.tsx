// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { Schema } from '@/lib/api/schema';
import { csvImportApi } from '@/lib/lists/csv-import';
import { MESSAGES } from '@/messages';
import { DoneStep, ProgressStep } from './csv-import-progress';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.restoreAllMocks());

const imp = (over: Partial<Schema<'CsvImportState'>>): Schema<'CsvImportState'> => ({
  id: 'i1',
  status: 'running',
  name: 'Leads',
  total: 171,
  processed: 110,
  rows_added: 0,
  duplicates: 1,
  in_lists: 2,
  excluded: 3,
  skipped: 4,
  discarded: 10,
  list_id: null,
  chat_session_id: null,
  error: null,
  cursor: null,
  ...over,
});

async function render(node: React.ReactNode) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        {node}
      </NextIntlClientProvider>,
    );
  });
  return container;
}

describe('csv import steps', () => {
  it('says how far the worker is and that the dialog can close', async () => {
    const c = await render(<ProgressStep imp={imp({})} />);
    expect(c.textContent).toContain('110 di 171 righe · puoi chiudere');
  });

  it('says why the import failed by its code, else a generic text', async () => {
    const coded = await render(
      <DoneStep imp={imp({ status: 'failed', error: 'LIST.CSV_EMPTY' })} />,
    );
    expect(coded.textContent).toContain('Il file è vuoto.');
    const old = await render(
      <DoneStep imp={imp({ status: 'failed', error: 'The import failed.' })} />,
    );
    expect(old.textContent).toContain('Importazione non riuscita.');
  });

  it('lists only the reasons that discarded rows', async () => {
    const c = await render(
      <DoneStep
        imp={imp({ status: 'done', duplicates: 0, in_lists: 3, excluded: 0, skipped: 0 })}
      />,
    );
    expect(c.textContent).toContain('Scartate: 3 già presenti nelle tue liste');
    expect(c.textContent).not.toContain('escluse');
  });

  it('offers the discarded rows of a finished import for download', async () => {
    const download = vi.spyOn(csvImportApi, 'downloadDiscarded').mockResolvedValue(true);
    const c = await render(<DoneStep imp={imp({ status: 'done', rows_added: 161 })} />);
    expect(c.textContent).toContain('161 righe importate');
    expect(c.textContent).toContain(
      'Scartate: 1 duplicata nel file, 2 già presenti nelle tue liste',
    );
    const button = [...c.querySelectorAll('button')].find((b) => b.textContent?.includes('10'));
    await act(async () => button?.click());
    expect(download).toHaveBeenCalledWith('i1', 'Leads');
  });
});
