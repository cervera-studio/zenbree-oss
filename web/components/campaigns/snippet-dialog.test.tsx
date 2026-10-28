// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { Frame } from '@/lib/api/event-stream';
import { MESSAGES } from '@/messages';
import { SnippetDialog } from './snippet-dialog';

const { get, estimateSnippet, addSnippetColumn, snippetRows, followStream, refresh } = vi.hoisted(
  () => ({
    get: vi.fn(),
    estimateSnippet: vi.fn(),
    addSnippetColumn: vi.fn(),
    snippetRows: vi.fn(),
    followStream: vi.fn(),
    refresh: vi.fn(),
  }),
);
vi.mock('@/lib/lists/api', () => ({ listsApi: { get, estimateSnippet, addSnippetColumn } }));
vi.mock('@/lib/campaigns/snippet', async (actual) => ({
  ...(await actual<typeof import('@/lib/campaigns/snippet')>()),
  snippetRows,
}));
vi.mock('@/lib/api/event-stream', async (actual) => ({
  ...(await actual<typeof import('@/lib/api/event-stream')>()),
  followStream,
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const ok = <T,>(data: T) => ({ success: true, data });
const column = (key: string, label: string, kind: string, config?: object) => ({
  key,
  label,
  kind,
  position: 0,
  config,
});
const LIST = {
  id: 'l1',
  prices: { search: 0, email: 1, phone: 8, ai_cell: 3, ai_cells_per_run: 150 },
  columns: [
    column('city', 'Città', 'static'),
    column('ai_news', 'Notizie recenti', 'ai', { prompt: 'news' }),
    column('ai_funding', 'Round', 'ai', { prompt: 'funding' }),
    column('ai_old', 'Vecchio snippet', 'ai', { mode: 'snippet' }),
  ],
};

let emit: (frame: Frame) => void = () => {};

beforeEach(() => {
  vi.clearAllMocks();
  snippetRows.mockResolvedValue(ok({ list_id: 'l1', row_ids: ['r1', 'r2', 'r3'], total: 3 }));
  get.mockResolvedValue(ok(LIST));
  estimateSnippet.mockResolvedValue(ok({ research_rows: 2, credits: 2 }));
  followStream.mockImplementation((_path, _cursor, onFrame: (frame: Frame) => void) => {
    emit = onFrame;
    return new Promise(() => {});
  });
});

async function render(people = 3) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS} timeZone="UTC">
        <SnippetDialog campaignId="c1" people={people} open onOpenChange={() => {}} />
      </NextIntlClientProvider>,
    );
  });
  return Object.assign(document.body, { root });
}

async function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = Object.getPrototypeOf(el);
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function fill(body: HTMLElement) {
  await type(body.querySelector('#snippet-label') as HTMLInputElement, 'Apertura');
  await type(
    body.querySelector('#snippet-prompt') as HTMLTextAreaElement,
    'Collega le notizie alla nostra offerta.',
  );
}

const approve = (body: HTMLElement) =>
  [...body.querySelectorAll('button')].find((b) => b.textContent === 'Approva e genera');

async function frame(event: string, data: object) {
  await act(async () => emit({ event, id: '1-0', data: JSON.stringify(data) }));
}

describe('SnippetDialog', () => {
  it('offers only the research columns and estimates only the people with research', async () => {
    const body = await render();
    const inputs = [...body.querySelectorAll('fieldset label')].map((l) => l.textContent);
    expect(inputs).toEqual(['Notizie recenti', 'Round']);
    expect(estimateSnippet).toHaveBeenCalledWith(
      'l1',
      ['ai_news', 'ai_funding'],
      ['r1', 'r2', 'r3'],
    );
    expect(body.textContent).toContain(
      'Fino a 2 crediti · 3 crediti per ogni snippet consegnato · 2 persone con ricerche',
    );
    expect(body.textContent).toContain('La stima conta solo le persone con ricerche');
  });

  it('approves exactly the estimate shown, then follows the fill to its end', async () => {
    addSnippetColumn.mockResolvedValue(ok({ key: 'ai_apertura' }));
    const body = await render();
    expect(approve(body)?.disabled).toBe(true);
    await fill(body);
    await act(async () => approve(body)?.click());
    expect(addSnippetColumn).toHaveBeenCalledWith('l1', {
      label: 'Apertura',
      prompt: 'Collega le notizie alla nostra offerta.',
      inputs: ['ai_news', 'ai_funding'],
      row_ids: ['r1', 'r2', 'r3'],
      approved_credits: 2,
    });
    expect(followStream.mock.calls[0]?.slice(0, 2)).toEqual(['/v1/lists/l1/stream', '0-0']);

    await frame('row', {
      row_id: 'r1',
      cells: { ai_apertura: 'Ciao' },
      cell_meta: { ai_apertura: { status: 'filled' } },
    });
    await frame('row', { row_id: 'r9', cells: {}, cell_meta: { ai_other: { status: 'filled' } } });
    expect(body.textContent).toContain('1 su 2 persone completate');

    await frame('fill', { key: 'ai_apertura', fill: { status: 'done', filled: 1, credits: 1 } });
    expect(body.textContent).toContain('1 snippet scritto · 1 credito addebitato');
    expect(body.textContent).toContain("Apertura è nell'elenco dei campi");
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('never approves a stale estimate: changing the inputs asks again', async () => {
    let answer: (value: unknown) => void = () => {};
    const body = await render();
    await fill(body);
    estimateSnippet.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const funding = body.querySelector('#snippet-input-ai_funding') as HTMLButtonElement;
    await act(async () => funding.click());
    expect(estimateSnippet).toHaveBeenLastCalledWith('l1', ['ai_news'], ['r1', 'r2', 'r3']);
    expect(approve(body)?.disabled).toBe(true);
    await act(async () => answer(ok({ research_rows: 1, credits: 1 })));
    expect(approve(body)?.disabled).toBe(false);
    expect(body.textContent).toContain('Fino a 1 credito');
  });

  it('says there is nothing to write when no one has research', async () => {
    estimateSnippet.mockResolvedValue(ok({ research_rows: 0, credits: 0 }));
    const body = await render();
    await fill(body);
    expect(body.textContent).toContain('Nessuno in questa campagna ha ricerche in queste colonne');
    expect(approve(body)?.disabled).toBe(true);
  });

  it('shows a refused create by its code and keeps the form', async () => {
    addSnippetColumn.mockResolvedValue({
      success: false,
      error: { code: 'LIST.SNIPPET_INPUTS_INVALID', message: 'x', status: 422 },
    });
    const body = await render();
    await fill(body);
    await act(async () => approve(body)?.click());
    expect(body.querySelector('[role="alert"]')?.textContent).toBe(
      MESSAGES.it.errors.LIST.SNIPPET_INPUTS_INVALID,
    );
    expect(approve(body)?.disabled).toBe(false);
  });

  it('says why a fill stopped', async () => {
    addSnippetColumn.mockResolvedValue(ok({ key: 'ai_apertura' }));
    const body = await render();
    await fill(body);
    await act(async () => approve(body)?.click());
    await frame('fill', {
      key: 'ai_apertura',
      fill: { status: 'failed', error: 'insufficient_credits' },
    });
    expect(body.querySelector('[role="alert"]')?.textContent).toBe(
      `Gli snippet si sono fermati: ${MESSAGES.it.lists.aiNoCredits}`,
    );
  });

  it('says a paused kill switch stopped the snippets, not web research', async () => {
    addSnippetColumn.mockResolvedValue(ok({ key: 'ai_apertura' }));
    const body = await render();
    await fill(body);
    await act(async () => approve(body)?.click());
    await frame('fill', {
      key: 'ai_apertura',
      fill: { status: 'failed', error: 'vendor_disabled' },
    });
    expect(body.querySelector('[role="alert"]')?.textContent).toBe(
      'Gli snippet si sono fermati: gli snippet AI sono in pausa per ora.',
    );
  });

  it('says how many people it covers, and the run cap the API sets', async () => {
    const body = await render(5);
    expect(body.textContent).toContain(
      'Copre 3 persone su 5 persone: solo chi può ancora ricevere uno step e ha una riga nella lista della campagna, al massimo 150 persone per esecuzione.',
    );
  });

  it('says why it covers no one without blaming the list', async () => {
    snippetRows.mockResolvedValue(ok({ list_id: 'l1', row_ids: [], total: 0 }));
    const body = await render();
    expect(body.textContent).toContain(
      "Nessuno in questa campagna può ancora ricevere uno step e ha una riga nella lista della campagna: non c'è nessuno per cui scrivere uno snippet.",
    );
  });

  it('stops following the fill when the dialog closes mid-run', async () => {
    addSnippetColumn.mockResolvedValue(ok({ key: 'ai_apertura' }));
    const body = await render();
    await fill(body);
    await act(async () => approve(body)?.click());
    const signal = followStream.mock.calls[0]?.[3] as AbortSignal;
    expect(signal.aborted).toBe(false);
    await act(async () => body.root.unmount());
    expect(signal.aborted).toBe(true);
  });
});
