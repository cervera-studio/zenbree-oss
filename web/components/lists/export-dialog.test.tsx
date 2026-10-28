// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { type List, type ListRow, listsApi } from '@/lib/lists/api';
import { MESSAGES } from '@/messages';
import { ExportDialog } from './export-dialog';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.restoreAllMocks());

const list = {
  id: 'l1',
  name: 'Agenzie Milano',
  entity: 'person',
  delivered_count: 2,
  columns: [
    { key: 'full_name', label: 'Nome', kind: 'static', position: 0 },
    { key: 'email', label: 'Email', kind: 'static', position: 1 },
  ],
} as List;
const row = (id: string): ListRow => ({
  id,
  seq: 1,
  cells: {},
  relevance_score: null,
  excluded: false,
  lookups: { email: { state: 'not_searched' }, phone: { state: 'not_searched' } },
});

async function render(shown: List = list) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <ExportDialog list={shown} rows={[row('a'), row('b')]} onClose={() => {}} />
      </NextIntlClientProvider>,
    );
  });
  return document.body;
}

const button = (body: HTMLElement, text: string) => {
  const b = [...body.querySelectorAll('button')].find((el) => el.textContent === text);
  if (!b) throw new Error(`no button ${text}`);
  return b;
};

describe('ExportDialog', () => {
  it('stays open on «File pronto» after the download, and downloads again on demand', async () => {
    const download = vi.spyOn(listsApi, 'download').mockResolvedValue(true);
    const body = await render();
    await act(async () => button(body, 'Esporta 2 righe').click());
    expect(body.textContent).toContain('File pronto');
    expect(body.textContent).toContain('Agenzie Milano.csv');
    expect(body.textContent).toContain('2 righe · 2 colonne.');
    await act(async () => button(body, 'Scarica di nuovo').click());
    expect(download).toHaveBeenCalledTimes(2);
    expect(download).toHaveBeenLastCalledWith('l1', 'Agenzie Milano', {
      format: 'csv',
      rows: 'all',
      columns: ['full_name', 'email'],
      verified_only: false,
      hide_off_target: false,
    });
  });

  it('sends the optional filters to the download', async () => {
    const download = vi.spyOn(listsApi, 'download').mockResolvedValue(true);
    const body = await render();
    const off = [...body.querySelectorAll('label')].find((l) =>
      l.textContent?.includes('fuori target'),
    );
    await act(async () => off?.querySelector('button')?.click());
    expect(body.textContent).toContain('Solo con email verificata');
    await act(async () => button(body, 'Esporta 2 righe').click());
    expect(download).toHaveBeenLastCalledWith(
      'l1',
      'Agenzie Milano',
      expect.objectContaining({ hide_off_target: true }),
    );
  });

  it('keeps «File pronto» while it downloads again, and shows a failed retry on it', async () => {
    let finish: (ok: boolean) => void = () => {};
    const download = vi.spyOn(listsApi, 'download').mockResolvedValueOnce(true);
    const body = await render();
    await act(async () => button(body, 'Esporta 2 righe').click());
    download.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
    await act(async () => button(body, 'Scarica di nuovo').click());
    expect(body.textContent).toContain('File pronto');
    expect(body.textContent).not.toContain('Formato');
    expect(button(body, 'Scarica di nuovo').disabled).toBe(true);
    await act(async () => finish(false));
    expect(body.textContent).toContain('File pronto');
    expect(body.querySelector('[role=alert]')?.textContent).toContain('Esportazione non riuscita.');
    expect(button(body, 'Riprova').disabled).toBe(false);
  });

  it('heads a column the API named in English with its Italian label', async () => {
    const city = { key: 'city', label: 'City', kind: 'static', position: 2 } as const;
    const body = await render({ ...list, columns: [...(list.columns ?? []), city] });
    const chips = [...body.querySelectorAll('fieldset:last-of-type label')].map(
      (l) => l.textContent,
    );
    expect(chips).toEqual(['Nome', 'Email', 'Città']);
  });
});
